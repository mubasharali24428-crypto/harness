"""Auth posture: the service token is the principal — and its absence or
invalidity must surface HONESTLY through MCP, never as a silent empty result.
"""

from __future__ import annotations

from conftest import make_settings, parse_payload

from harness_mcp.client import HarnessApiClient


def test_service_token_is_sent_as_bearer():
    """Every upstream call carries `Authorization: Bearer <service token>`."""
    api = HarnessApiClient(make_settings(service_token="svc-token-xyz"))
    assert api._client.headers["Authorization"] == "Bearer svc-token-xyz"


def test_missing_token_configures_client_without_header():
    api = HarnessApiClient(make_settings(service_token=""))
    assert "Authorization" not in api._client.headers


async def test_upstream_401_becomes_honest_error_result(mcp, stub_api):
    """Expired/revoked service token → S1's 401 answer as an isError result.

    Section 1 answers FastAPI string details ("invalid or expired session");
    the client wraps that into the frozen {error:{code,message}} shape so an
    LLM consumer gets a machine-readable failure instead of an empty list or
    a protocol crash.
    """
    from harness_mcp.client import UpstreamError

    stub_api.on(
        "GET",
        "/api/v1/projects",
        (
            lambda _: (_ for _ in ()).throw(
                UpstreamError(
                    401,
                    "",
                    {"error": {"code": "UPSTREAM_ERROR", "message": "invalid or expired session"}},
                )
            )
        ),
    )
    run = mcp
    res = run(lambda s: s.call_tool("list_projects", {}))
    assert res.is_error is True
    payload = parse_payload(res)
    assert payload["error"]["code"] == "UPSTREAM_ERROR"
    assert "invalid or expired session" in payload["error"]["message"]


def test_raw_fastapi_detail_string_maps_into_envelope():
    """A raw {"detail": "..."} body (no envelope) maps into the error shape."""
    import httpx

    import anyio
    import pytest

    from harness_mcp.client import UpstreamError

    api = HarnessApiClient(make_settings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid or expired session"})

    # Swap in a MockTransport-backed client to exercise _request's mapping.
    api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def call():
        await api.list_projects()

    with pytest.raises(UpstreamError) as excinfo:
        anyio.run(call)
    assert excinfo.value.status == 401
    assert excinfo.value.envelope["error"]["message"] == "invalid or expired session"
