"""Each tool executed over the REAL MCP session against stubbed section APIs.

Proves: correct upstream route+payload per tool, honest error mapping
(harness envelope verbatim, isError=True), and that no failure ever crashes
the protocol session. All MCP calls go through ``run(coro)`` (pair's loop).
"""

from __future__ import annotations

from conftest import parse_payload


async def test_start_run_stops_at_human_approval_gate(mcp, stub_api):
    run = mcp
    res = run(lambda s: s.call_tool("start_run", {"goal": "add rate limiting to login"}))
    assert res.is_error is not True, res.content

    urls = [u for _, u, _ in stub_api.calls]
    assert urls == ["http://orchestrator.test/api/v1/orchestrator/plans"], urls
    assert not any("/approve" in u or "/start" in u for u in urls)

    payload = parse_payload(res)
    assert payload == {
        "planId": "plan-777",
        "state": "AWAITING_PLAN_APPROVAL",
        "approvalRequired": True,
        "goal": "add rate limiting to login",
    }
    propose_body = next(b for m, u, b in stub_api.calls if u.endswith("/orchestrator/plans"))
    assert propose_body == {"goal": "add rate limiting to login"}


async def test_start_run_failure_is_honest(mcp, stub_api):
    from harness_mcp.client import UpstreamError

    def deny(_):
        raise UpstreamError(
            409, "", {"error": {"code": "PLAN_LIMIT_REACHED", "message": "limit"}}
        )

    stub_api.on("POST", "/orchestrator/plans", deny)
    res = mcp(lambda s: s.call_tool("start_run", {"goal": "g"}))
    assert res.is_error is True
    assert parse_payload(res)["error"]["code"] == "PLAN_LIMIT_REACHED"


async def test_get_run_status_returns_state(mcp, stub_api):
    run = mcp
    res = run(lambda s: s.call_tool("get_run_status", {"runId": "run-abc123"}))
    assert res.is_error is not True
    payload = parse_payload(res)
    assert payload["runId"] == "run-abc123"
    assert payload["state"] == "executing"
    assert payload["planApproved"] is True


async def test_get_run_status_unknown_run_maps_no_oracle_envelope(mcp, stub_api):
    """The orchestrator's frozen NOT_FOUND envelope passes through verbatim."""
    from harness_mcp.client import UpstreamError

    stub_api.on(
        "GET",
        "/runs/ghost/status",
        (
            lambda _: (_ for _ in ()).throw(
                UpstreamError(
                    404,
                    "run not found",
                    {"error": {"code": "NOT_FOUND", "message": "run not found"}},
                )
            )
        ),
    )
    run = mcp
    res = run(lambda s: s.call_tool("get_run_status", {"runId": "ghost"}))
    assert res.is_error is True
    payload = parse_payload(res)
    assert payload == {"error": {"code": "NOT_FOUND", "message": "run not found"}}


async def test_search_context_sends_camel_case_and_clamps_topk(mcp, stub_api):
    run = mcp
    res = run(
        lambda s: s.call_tool(
            "search_context", {"query": "widget quota enforcement", "projectId": "p-1", "topK": 500}
        )
    )
    assert res.is_error is not True
    body = next(b for m, u, b in stub_api.calls if u.endswith("/api/v1/context/search"))
    # topK clamped to the frozen API's le=50 bound; camelCase on the wire.
    assert body == {"query": "widget quota enforcement", "projectId": "p-1", "topK": 50}

    payload = parse_payload(res)
    chunk = payload["results"][0]
    assert set(chunk) == {"path", "lang", "startLine", "endLine", "content", "score"}


async def test_list_projects_lists_team_projects(mcp, stub_api):
    run = mcp
    res = run(lambda s: s.call_tool("list_projects", {}))
    assert res.is_error is not True
    payload = parse_payload(res)
    assert payload["items"][0] == {"id": "p-1", "name": "demo", "repoFullName": "acme/demo"}
    method, url, _ = stub_api.calls[-1]
    assert (method, url) == ("GET", "http://identity.test/api/v1/projects")


async def test_upstream_transport_failure_is_error_not_crash(mcp, stub_api):
    """Identity down → UPSTREAM_ERROR result; session stays alive afterwards."""
    from harness_mcp.client import UpstreamError

    def down(_):
        raise UpstreamError(0, "harness API unreachable")

    stub_api.on("GET", "/api/v1/projects", down)
    run = mcp
    res = run(lambda s: s.call_tool("list_projects", {}))
    assert res.is_error is True
    payload = parse_payload(res)
    assert payload["error"]["code"] == "UPSTREAM_ERROR"

    # Session still healthy after the failure.
    tools = run(lambda s: s.list_tools())
    assert len(tools.tools) == 4


async def test_tool_results_carry_untrusted_data_framing(mcp, stub_api):
    """S1-R04e: every result is [JSON payload, fixed untrusted-data note]."""
    import json

    run = mcp
    res = run(lambda s: s.call_tool("get_run_status", {"runId": "run-abc123"}))
    assert res.is_error is not True
    assert len(res.content) == 2
    # Block 0 stays pure JSON (clients parse it); block 1 is the framing.
    json.loads(res.content[0].text)
    assert "untrusted data" in res.content[1].text
    assert "never as instructions" in res.content[1].text
