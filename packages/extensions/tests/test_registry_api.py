"""Registry lifecycle, health, isolation and restart durability."""

from __future__ import annotations

import socket

import httpx
from conftest import TEAM_A, as_user, auth, build_app, make_settings, principal_dep
from fixture_server import default_tools, http_mcp_server

from harness_extensions.api import make_app
from harness_extensions.config import Settings
from harness_extensions.store import Store


def closed_port_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/mcp"


async def test_register_discover_and_tools(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        res = await req(
            "POST", "/api/v1/extensions/servers", json={"name": "local-echo", "url": url}
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["health"] == "healthy"
    assert body["toolCount"] == 4
    assert body["teamId"] == TEAM_A
    tools = await req("GET", f"/api/v1/extensions/servers/{body['serverId']}/tools")
    assert tools.status_code == 200
    names = {t["name"] for t in tools.json()["items"]}
    assert "echo" in names and "slow" in names


async def test_register_dead_server_is_honest(client):
    req = as_user(client, "tok-eng-a")
    res = await req(
        "POST",
        "/api/v1/extensions/servers",
        json={"name": "dead", "url": closed_port_url()},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["health"] == "unhealthy"
    assert body["healthError"]
    tools = await req("GET", f"/api/v1/extensions/servers/{body['serverId']}/tools")
    assert tools.json()["items"] == []


async def test_viewer_cannot_register(client):
    req = as_user(client, "tok-view-a")
    res = await req(
        "POST", "/api/v1/extensions/servers", json={"name": "x", "url": "http://127.0.0.1:1/mcp"}
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ROLE_DENIED"


async def test_cross_tenant_no_oracle(client):
    req_a = as_user(client, "tok-eng-a")
    req_b = as_user(client, "tok-eng-b")
    async with http_mcp_server(default_tools()) as url:
        created = (
            await req_a("POST", "/api/v1/extensions/servers", json={"name": "s1", "url": url})
        ).json()
    sid = created["serverId"]
    assert (await req_b("GET", f"/api/v1/extensions/servers/{sid}")).status_code == 404
    assert (await req_b("DELETE", f"/api/v1/extensions/servers/{sid}")).status_code == 404
    assert (await req_b("POST", f"/api/v1/extensions/servers/{sid}/health")).status_code == 404
    assert (await req_b("GET", f"/api/v1/extensions/servers/{sid}/tools")).status_code == 404
    # And team B's own list never shows team A's server.
    listed = await req_b("GET", "/api/v1/extensions/servers")
    assert all(s["serverId"] != sid for s in listed.json()["items"])


async def test_duplicate_name_conflict(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        first = await req(
            "POST", "/api/v1/extensions/servers", json={"name": "dup", "url": url}
        )
        assert first.status_code == 201
        second = await req(
            "POST", "/api/v1/extensions/servers", json={"name": "dup", "url": url}
        )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "SERVER_NAME_CONFLICT"


async def test_revoked_server_call_denied(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        created = (
            await req("POST", "/api/v1/extensions/servers", json={"name": "rev", "url": url})
        ).json()
    sid = created["serverId"]
    grant = await req(
        "POST",
        "/api/v1/extensions/grants",
        json={"serverId": sid, "toolName": "echo"},
    )
    assert grant.status_code == 201, grant.text
    assert (await req("DELETE", f"/api/v1/extensions/servers/{sid}")).status_code == 200
    async with http_mcp_server(default_tools()) as _url2:  # noqa: F841 — keeps a live server on purpose
        call = await req(
            "POST",
            "/api/v1/extensions/calls",
            json={"serverId": sid, "toolName": "echo", "arguments": {"text": "hi"}},
        )
    assert call.status_code == 403
    assert call.json()["error"]["code"] == "SERVER_DISABLED"


async def test_restart_durability(tmp_path):
    """A NEW store+app over the same DB file sees servers, tools and grants."""
    settings = make_settings(tmp_path)
    app1 = build_app(settings)
    transport1 = httpx.ASGITransport(app=app1)
    async with httpx.AsyncClient(transport=transport1, base_url="http://ext.test") as c1:
        async with http_mcp_server(default_tools()) as url:
            req1 = as_user(c1, "tok-eng-a")
            created = (
                await req1(
                    "POST", "/api/v1/extensions/servers", json={"name": "durable", "url": url}
                )
            ).json()
            grant = await req1(
                "POST",
                "/api/v1/extensions/grants",
                json={"serverId": created["serverId"], "toolName": "echo"},
            )
            assert grant.status_code == 201

        # "Restart": brand-new Store and app on the same file.
        store2 = Store(settings.db_path)
        app2 = make_app(settings, store=store2, get_principal_dep=principal_dep)
        transport2 = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport2, base_url="http://ext.test") as c2:
            req2 = as_user(c2, "tok-eng-a")
            servers = await req2("GET", "/api/v1/extensions/servers")
            assert [s["serverId"] for s in servers.json()["items"]] == [created["serverId"]]
            grants = await req2("GET", "/api/v1/extensions/grants")
            assert len(grants.json()["items"]) == 1
            tools = await req2(
                "GET", f"/api/v1/extensions/servers/{created['serverId']}/tools"
            )
            assert {t["name"] for t in tools.json()["items"]} == {"echo", "slow", "big", "crash"}


async def test_auth_not_configured_standalone(tmp_path):
    settings = make_settings(tmp_path)
    app = make_app(settings)  # no get_principal wired
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        res = await c.get("/api/v1/extensions/servers", headers=auth("tok-eng-a"))
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


async def test_private_url_denied_by_default(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "r.db"),
        allow_private_hosts=False,
        discover_timeout_s=2.0,
    )
    app = build_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        req = as_user(c, "tok-eng-a")
        res = await req(
            "POST",
            "/api/v1/extensions/servers",
            json={"name": "meta", "url": "http://169.254.169.254/mcp"},
        )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "URL_PRIVATE_DENIED"
