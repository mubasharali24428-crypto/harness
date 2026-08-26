"""Each tool executed over the REAL MCP session against stubbed section APIs.

Proves: correct upstream route+payload per tool, honest error mapping
(harness envelope verbatim, isError=True), and that no failure ever crashes
the protocol session. All MCP calls go through ``run(coro)`` (pair's loop).
"""

from __future__ import annotations

from conftest import parse_payload


async def test_start_run_walks_propose_approve_start(mcp, stub_api):
    run = mcp
    res = run(lambda s: s.call_tool("start_run", {"goal": "add rate limiting to login"}))
    assert res.is_error is not True, res.content

    # All three plan-gate routes hit, in walk order: propose → approve → start.
    urls = [u for _, u, _ in stub_api.calls]
    propose_i = urls.index("http://orchestrator.test/api/v1/orchestrator/plans")
    approve_i = next(i for i, u in enumerate(urls) if u.endswith("/plans/plan-777/approve"))
    start_i = next(i for i, u in enumerate(urls) if u.endswith("/runs/run-abc123/start"))
    assert propose_i < approve_i < start_i

    payload = parse_payload(res)
    assert payload == {
        "runId": "run-abc123",
        "planId": "plan-777",
        "state": "executing",
        "goal": "add rate limiting to login",
    }
    # The goal reached the orchestrator verbatim.
    propose_body = next(b for m, u, b in stub_api.calls if u.endswith("/orchestrator/plans"))
    assert propose_body == {"goal": "add rate limiting to login"}


async def test_start_run_failure_mid_walk_is_honest_with_handles(mcp, stub_api):
    """Start leg dies → isError result carrying the harness code + minted handles."""
    from harness_mcp.client import UpstreamError

    def deny_start(_):
        raise UpstreamError(
            409, "", {"error": {"code": "PLAN_NOT_APPROVED", "message": "not approved"}}
        )

    stub_api.on("POST", "/runs/run-abc123/start", deny_start)

    run = mcp
    res = run(lambda s: s.call_tool("start_run", {"goal": "g"}))
    assert res.is_error is True
    payload = parse_payload(res)
    assert payload["error"]["code"] == "PLAN_NOT_APPROVED"
    # Walk progress rides along so a caller can resume instead of re-minting.
    assert payload["error"]["planId"] == "plan-777"
    assert payload["error"]["runId"] == "run-abc123"


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
