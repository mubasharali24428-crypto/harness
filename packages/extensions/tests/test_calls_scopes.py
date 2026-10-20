"""Tool calls: deny-by-default grants, expiry, revocation, bindings, timeouts."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from conftest import TEAM_A, as_user, build_app, make_settings
from fixture_server import default_tools, http_mcp_server

from harness_extensions.models import ToolGrant


async def _register(req, name: str, url: str) -> str:
    res = await req("POST", "/api/v1/extensions/servers", json={"name": name, "url": url})
    assert res.status_code == 201, res.text
    return res.json()["serverId"]


async def _grant(req, server_id: str, tool: str, **extra) -> dict:
    res = await req(
        "POST",
        "/api/v1/extensions/grants",
        json={"serverId": server_id, "toolName": tool, **extra},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _call(req, server_id: str, tool: str, arguments: dict | None = None, **extra):
    return await req(
        "POST",
        "/api/v1/extensions/calls",
        json={"serverId": server_id, "toolName": tool, "arguments": arguments or {}, **extra},
    )


async def test_deny_by_default(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        res = await _call(req, sid, "echo", {"text": "hi"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "SCOPE_DENIED"


async def test_exact_grant_allows_call(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        await _grant(req, sid, "echo")
        res = await _call(req, sid, "echo", {"text": "hi"})
        assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "succeeded"
    assert body["isError"] is False
    assert body["observationId"]
    assert "echo:hi" in body["blocks"][0]["text"]


async def test_wildcard_grant(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        await _grant(req, sid, "*")
        ok = await _call(req, sid, "echo", {"text": "w"})
        assert ok.status_code == 200


async def test_grant_for_other_tool_does_not_cover(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        await _grant(req, sid, "slow")
        res = await _call(req, sid, "echo", {"text": "x"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "SCOPE_DENIED"


async def test_expired_grant_denied(app, client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        # Insert an already-expired grant straight into the store: the API
        # refuses past expiries, so expiry must be defeated at call time.
        store = app.state.extension_store
        store.create_grant(
            ToolGrant(
                grantId="g-expired",
                teamId=TEAM_A,
                serverId=sid,
                toolName="echo",
                expiresAt=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                createdBy="u-eng-a",
                createdAt=datetime.now(UTC).isoformat(),
            )
        )
        res = await _call(req, sid, "echo", {"text": "x"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "GRANT_EXPIRED"


async def test_revoked_grant_denied(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        grant = await _grant(req, sid, "echo")
        revoke = await req("DELETE", f"/api/v1/extensions/grants/{grant['grantId']}")
        assert revoke.status_code == 200
        res = await _call(req, sid, "echo", {"text": "x"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "SCOPE_DENIED"


async def test_run_binding(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        await _grant(req, sid, "echo", runId="run-1")
        unbound = await _call(req, sid, "echo", {"text": "x"})
        matching = await _call(req, sid, "echo", {"text": "x"}, runId="run-1")
        wrong = await _call(req, sid, "echo", {"text": "x"}, runId="run-2")
    assert unbound.status_code == 403
    assert matching.status_code == 200
    assert wrong.status_code == 403


async def test_project_binding_with_lookup(tmp_path):
    settings = make_settings(tmp_path)
    projects = {"proj-a": TEAM_A}
    app = build_app(settings, project_owner_lookup=projects.get)
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        req = as_user(c, "tok-eng-a")
        async with http_mcp_server(default_tools()) as url:
            sid = await _register(req, "s", url)
            await _grant(req, sid, "echo", projectId="proj-a")
            ok = await _call(req, sid, "echo", {"text": "x"}, projectId="proj-a")
            assert ok.status_code == 200, ok.text
            unbound = await _call(req, sid, "echo", {"text": "x"})
            assert unbound.status_code == 403
            unknown = await _call(req, sid, "echo", {"text": "x"}, projectId="proj-zz")
            assert unknown.status_code == 404
            assert unknown.json()["error"]["code"] == "PROJECT_NOT_FOUND"


async def test_project_call_without_lookup_fails_closed(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        await _grant(req, sid, "echo")  # no project binding needed to create
        res = await _call(req, sid, "echo", {"text": "x"}, projectId="proj-a")
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "PROJECT_CHECK_UNAVAILABLE"


async def test_call_timeout_is_enforced(tmp_path):
    settings = make_settings(tmp_path, call_timeout_s=0.75)
    app = build_app(settings)
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        req = as_user(c, "tok-eng-a")
        async with http_mcp_server(default_tools()) as url:
            sid = await _register(req, "s", url)
            await _grant(req, sid, "slow")
            started = time.perf_counter()
            res = await _call(req, sid, "slow", {"seconds": 5})
            elapsed = time.perf_counter() - started
    assert res.status_code == 504, res.text
    assert res.json()["error"]["code"] == "MCP_TIMEOUT"
    assert elapsed < 3.5, f"timeout not enforced (took {elapsed:.1f}s)"


async def test_dead_server_after_registration(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        await _grant(req, sid, "echo")
    # Fixture server is now shut down.
    res = await _call(req, sid, "echo", {"text": "x"})
    assert res.status_code == 502
    assert res.json()["error"]["code"] in {"MCP_SERVER_UNREACHABLE", "MCP_PROTOCOL_ERROR"}


async def test_oversized_result_truncated(tmp_path):
    settings = make_settings(tmp_path, max_result_bytes=4096)
    app = build_app(settings)
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        req = as_user(c, "tok-eng-a")
        async with http_mcp_server(default_tools()) as url:
            sid = await _register(req, "s", url)
            await _grant(req, sid, "big")
            res = await _call(req, sid, "big", {"bytes": 200_000})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["truncated"] is True
    assert "[truncated]" in body["blocks"][0]["text"]
    assert len(body["blocks"][0]["text"]) < 10_000


async def test_hostile_discovery_rejects_tools(tmp_path):
    from fixture_server import malicious_tools

    settings = make_settings(tmp_path)
    app = build_app(settings)
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        req = as_user(c, "tok-eng-a")
        async with http_mcp_server(malicious_tools()) as url:
            res = await req("POST", "/api/v1/extensions/servers", json={"name": "evil", "url": url})
            assert res.status_code == 201, res.text
            body = res.json()
            assert body["health"] == "degraded"
            assert body["rejectedToolCount"] == 3
            assert body["toolCount"] == 2
            sid = body["serverId"]
            tools = (await req("GET", f"/api/v1/extensions/servers/{sid}/tools")).json()["items"]
            by_name = {t["name"]: t for t in tools}
            assert by_name["echo"]["callable"] is True
            for hostile in ("deep_bomb", "huge_desc", "non_object_root"):
                assert by_name[hostile]["callable"] is False
                assert by_name[hostile]["rejectionReason"]
            # $ref bombs are neutralized by construction: refs are never
            # dereferenced, so the node-count bound is what applies.
            assert by_name["ref_bomb"]["callable"] is True
            # A rejected tool can never be granted or called.
            grant = await req(
                "POST",
                "/api/v1/extensions/grants",
                json={"serverId": sid, "toolName": "deep_bomb"},
            )
            assert grant.status_code == 409
            assert grant.json()["error"]["code"] == "TOOL_REJECTED"
            call = await _call(req, sid, "deep_bomb")
            assert call.status_code == 409
            assert call.json()["error"]["code"] == "TOOL_REJECTED"


async def test_tool_crash_is_error_result(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        await _grant(req, sid, "crash")
        res = await _call(req, sid, "crash")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["isError"] is True
    assert body["status"] == "failed"
    assert "boom" in body["blocks"][0]["text"]


async def test_arguments_size_capped(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        await _grant(req, sid, "echo")
        res = await _call(req, sid, "echo", {"text": "y" * 300_000})
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "ARGUMENTS_TOO_LARGE"


async def test_cross_tenant_call_no_oracle(client):
    req_a = as_user(client, "tok-eng-a")
    req_b = as_user(client, "tok-eng-b")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req_a, "s", url)
        await _grant(req_a, sid, "echo")
        res = await _call(req_b, sid, "echo", {"text": "x"})
    # 404, not 403: team B learns nothing about team A's server existence.
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "SERVER_NOT_FOUND"


async def test_unknown_tool_404(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register(req, "s", url)
        await _grant(req, sid, "echo")
        res = await _call(req, sid, "nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "TOOL_NOT_FOUND"
