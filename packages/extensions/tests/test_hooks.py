"""Event hooks: real webhook + mcp_tool deliveries, refusal, honest records."""

from __future__ import annotations

import asyncio

from conftest import as_user
from fixture_server import default_tools, http_mcp_server
from webhook_sink import webhook_sink


async def _register_and_grant(req, url: str, tools: list[str]) -> str:
    sid = (
        await req("POST", "/api/v1/extensions/servers", json={"name": "s", "url": url})
    ).json()["serverId"]
    for tool in tools:
        grant = await req(
            "POST", "/api/v1/extensions/grants", json={"serverId": sid, "toolName": tool}
        )
        assert grant.status_code == 201
    return sid


async def test_webhook_hook_receives_tool_call_events(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url, webhook_sink() as (hook_url, sink):
        sid = await _register_and_grant(req, url, ["echo"])
        created = await req(
            "POST",
            "/api/v1/extensions/hooks",
            json={
                "name": "on-call-notify",
                "eventType": "tool_call.succeeded",
                "target": {"kind": "webhook", "url": hook_url},
            },
        )
        assert created.status_code == 201, created.text

        call = await req(
            "POST",
            "/api/v1/extensions/calls",
            json={"serverId": sid, "toolName": "echo", "arguments": {"text": "hi"}},
        )
        assert call.status_code == 200

        for _ in range(50):
            if sink.requests:
                break
            await asyncio.sleep(0.02)
        assert len(sink.requests) == 1
        envelope = sink.requests[0]
        assert envelope["eventType"] == "tool_call.succeeded"
        assert envelope["payload"]["toolName"] == "echo"
        assert envelope["payload"]["serverId"] == sid

        deliveries = (
            await req("GET", f"/api/v1/extensions/hooks/{created.json()['hookId']}/deliveries")
        ).json()["items"]
        assert deliveries[0]["status"] == "delivered"


async def test_mcp_tool_hook_invokes_target_tool(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = await _register_and_grant(req, url, ["echo"])
        hook = await req(
            "POST",
            "/api/v1/extensions/hooks",
            json={
                "name": "echo-on-events",
                "eventType": "*",
                "target": {"kind": "mcp_tool", "serverId": sid, "toolName": "echo",
                           "arguments": {"text": "fired"}},
            },
        )
        assert hook.status_code == 201, hook.text

        dispatch = await req(
            "POST",
            "/api/v1/extensions/hooks/dispatch",
            json={"eventType": "tool_call.succeeded", "payload": {"note": "manual"}},
        )
        assert dispatch.status_code == 200, dispatch.text
        deliveries = dispatch.json()["deliveries"]
        assert len(deliveries) == 1
        assert deliveries[0]["status"] == "delivered"


async def test_hook_failed_when_webhook_returns_5xx(client):
    req = as_user(client, "tok-eng-a")
    async with webhook_sink() as (hook_url, sink):
        sink.status = 500
        hook = await req(
            "POST",
            "/api/v1/extensions/hooks",
            json={
                "name": "failing",
                "eventType": "tool_call.failed",
                "target": {"kind": "webhook", "url": hook_url},
            },
        )
        assert hook.status_code == 201
        dispatch = await req(
            "POST",
            "/api/v1/extensions/hooks/dispatch",
            json={"eventType": "tool_call.failed", "payload": {"note": "boom"}},
        )
        assert dispatch.status_code == 200
    deliveries = (
        await req("GET", f"/api/v1/extensions/hooks/{hook.json()['hookId']}/deliveries")
    ).json()["items"]
    assert deliveries[0]["status"] == "failed"
    assert "500" in deliveries[0]["detail"]


async def test_deny_listed_webhook_url_refused_at_creation(client, tmp_path):
    # Default settings deny private hosts; make a LOCAL app just for this.
    import httpx
    from conftest import principal_dep

    from harness_extensions.api import make_app
    from harness_extensions.config import Settings

    settings = Settings(
        db_path=str(tmp_path / "registry.db"),
        allow_private_hosts=False,  # default posture
    )
    app = make_app(settings, get_principal_dep=principal_dep)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        req = as_user(c, "tok-eng-a")
        res = await req(
            "POST",
            "/api/v1/extensions/hooks",
            json={
                "name": "ssrf",
                "eventType": "*",
                "target": {"kind": "webhook", "url": "http://127.0.0.1:9999/hook"},
            },
        )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "HOOK_TARGET_REFUSED"


async def test_mcp_tool_hook_refused_without_grant(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = (
            await req("POST", "/api/v1/extensions/servers", json={"name": "s", "url": url})
        ).json()["serverId"]
        # No grant for echo.
        await req(
            "POST",
            "/api/v1/extensions/hooks",
            json={
                "name": "nogrant",
                "eventType": "*",
                "target": {"kind": "mcp_tool", "serverId": sid, "toolName": "echo"},
            },
        )
        dispatch = await req(
            "POST",
            "/api/v1/extensions/hooks/dispatch",
            json={"eventType": "tool_call.succeeded", "payload": {}},
        )
        assert dispatch.json()["deliveries"][0]["status"] == "refused"


async def test_disabled_hook_stops_firing_and_unknown_event_rejected(client):
    req = as_user(client, "tok-eng-a")
    async with webhook_sink() as (hook_url, sink):
        hook = await req(
            "POST",
            "/api/v1/extensions/hooks",
            json={
                "name": "toggle",
                "eventType": "*",
                "target": {"kind": "webhook", "url": hook_url},
            },
        )
        assert hook.status_code == 201
        hook_id = hook.json()["hookId"]
        assert (await req("DELETE", f"/api/v1/extensions/hooks/{hook_id}")).status_code == 200
        dispatch = await req(
            "POST",
            "/api/v1/extensions/hooks/dispatch",
            json={"eventType": "tool_call.succeeded", "payload": {}},
        )
        assert dispatch.json()["deliveries"] == []
        assert sink.requests == []

    bad_event = await req(
        "POST",
        "/api/v1/extensions/hooks",
        json={"name": "bad", "eventType": "not.real", "target": {"kind": "webhook", "url": "https://example.com/h"}},
    )
    assert bad_event.status_code == 409
    assert bad_event.json()["error"]["code"] == "HOOK_EVENT_INVALID"


async def test_hook_name_conflict_and_cross_tenant_isolation(client):
    req_a = as_user(client, "tok-eng-a")
    req_b = as_user(client, "tok-eng-b")
    async with webhook_sink() as (hook_url, sink):
        first = await req_a(
            "POST",
            "/api/v1/extensions/hooks",
            json={"name": "same", "eventType": "*", "target": {"kind": "webhook", "url": hook_url}},
        )
        assert first.status_code == 201
        again = await req_a(
            "POST",
            "/api/v1/extensions/hooks",
            json={"name": "same", "eventType": "*", "target": {"kind": "webhook", "url": hook_url}},
        )
        assert again.status_code == 409

        # Team B dispatches: team A's hook must not fire.
        dispatch = await req_b(
            "POST",
            "/api/v1/extensions/hooks/dispatch",
            json={"eventType": "tool_call.succeeded", "payload": {}},
        )
        assert dispatch.json()["deliveries"] == []
        b_list = await req_b("GET", "/api/v1/extensions/hooks")
        assert b_list.json()["items"] == []
        assert sink.requests == []
