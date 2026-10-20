"""Custom tool wrappers: argument narrowing, pinning, grant inheritance."""

from __future__ import annotations

from contextlib import asynccontextmanager

from conftest import as_user
from fixture_server import default_tools, http_mcp_server


@asynccontextmanager
async def _server_and_echo_grant(req):
    """Register a live fixture server and grant `echo`, keeping it alive."""
    async with http_mcp_server(default_tools()) as url:
        server = (
            await req("POST", "/api/v1/extensions/servers", json={"name": "s", "url": url})
        ).json()
        grant = await req(
            "POST",
            "/api/v1/extensions/grants",
            json={"serverId": server["serverId"], "toolName": "echo"},
        )
        assert grant.status_code == 201, grant.text
        yield server


async def test_wrapper_narrows_arguments(client):
    req = as_user(client, "tok-eng-a")
    async with _server_and_echo_grant(req) as server:
        created = await req(
            "POST",
            "/api/v1/extensions/custom-tools",
            json={
                "name": "echo-narrow",
                "serverId": server["serverId"],
                "sourceTool": "echo",
                "allowedArguments": ["text"],
            },
        )
        assert created.status_code == 201, created.text
        tool_id = created.json()["customToolId"]

        ok = await req(
            "POST",
            f"/api/v1/extensions/custom-tools/{tool_id}/call",
            json={"arguments": {"text": "hello"}},
        )
        assert ok.status_code == 200, ok.text
        assert "echo:hello" in ok.json()["blocks"][0]["text"]
        assert ok.json()["toolName"] == "echo-narrow"

        extra = await req(
            "POST",
            f"/api/v1/extensions/custom-tools/{tool_id}/call",
            json={"arguments": {"text": "hello", "sneaky": 1}},
        )
        assert extra.status_code == 400
        assert extra.json()["error"]["code"] == "ARG_NOT_ALLOWED"


async def test_wrapper_pins_defaults(client):
    req = as_user(client, "tok-eng-a")
    async with _server_and_echo_grant(req) as server:
        created = (
            await req(
                "POST",
                "/api/v1/extensions/custom-tools",
                json={
                    "name": "echo-pinned",
                    "serverId": server["serverId"],
                    "sourceTool": "echo",
                    "defaultArguments": {"text": "pinned"},
                },
            )
        ).json()
        tool_id = created["customToolId"]

        ok = await req(
            "POST",
            f"/api/v1/extensions/custom-tools/{tool_id}/call",
            json={"arguments": {}},
        )
        assert ok.status_code == 200, ok.text
        assert "echo:pinned" in ok.json()["blocks"][0]["text"]

        override = await req(
            "POST",
            f"/api/v1/extensions/custom-tools/{tool_id}/call",
            json={"arguments": {"text": "other"}},
        )
        assert override.status_code == 400
        assert override.json()["error"]["code"] == "ARG_PINNED"


async def test_wrapper_unknown_allowed_argument_rejected(client):
    req = as_user(client, "tok-eng-a")
    async with _server_and_echo_grant(req) as server:
        created = await req(
            "POST",
            "/api/v1/extensions/custom-tools",
            json={
                "name": "echo-bad",
                "serverId": server["serverId"],
                "sourceTool": "echo",
                "allowedArguments": ["nonexistent"],
            },
        )
        assert created.status_code == 400
        assert created.json()["error"]["code"] == "ARG_UNKNOWN"


async def test_wrapper_requires_source_grant(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        server = (
            await req("POST", "/api/v1/extensions/servers", json={"name": "s", "url": url})
        ).json()
        # No grant for echo: even a wrapper cannot call it.
        created = (
            await req(
                "POST",
                "/api/v1/extensions/custom-tools",
                json={"name": "w", "serverId": server["serverId"], "sourceTool": "echo"},
            )
        ).json()
        res = await req(
            "POST",
            f"/api/v1/extensions/custom-tools/{created['customToolId']}/call",
            json={"arguments": {"text": "x"}},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "SCOPE_DENIED"


async def test_disabled_wrapper_denied(client):
    req = as_user(client, "tok-eng-a")
    async with _server_and_echo_grant(req) as server:
        created = (
            await req(
                "POST",
                "/api/v1/extensions/custom-tools",
                json={"name": "w2", "serverId": server["serverId"], "sourceTool": "echo"},
            )
        ).json()
        tool_id = created["customToolId"]
        assert (
            await req("DELETE", f"/api/v1/extensions/custom-tools/{tool_id}")
        ).status_code == 200
        res = await req(
            "POST", f"/api/v1/extensions/custom-tools/{tool_id}/call", json={"arguments": {}}
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "CUSTOM_TOOL_DISABLED"


async def test_cross_tenant_wrapper_no_oracle(client):
    req_a = as_user(client, "tok-eng-a")
    req_b = as_user(client, "tok-eng-b")
    async with _server_and_echo_grant(req_a) as server:
        created = (
            await req_a(
                "POST",
                "/api/v1/extensions/custom-tools",
                json={"name": "a-only", "serverId": server["serverId"], "sourceTool": "echo"},
            )
        ).json()
        tool_id = created["customToolId"]
        res = await req_b(
            "POST", f"/api/v1/extensions/custom-tools/{tool_id}/call", json={}
        )
        assert res.status_code == 404
        listed = await req_b("GET", "/api/v1/extensions/custom-tools")
        assert listed.json()["items"] == []
