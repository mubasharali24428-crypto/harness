"""Team tool policy: deny wins, allowlist mode, grant-time gate."""

from __future__ import annotations

from contextlib import asynccontextmanager

from conftest import as_user
from fixture_server import default_tools, http_mcp_server


@asynccontextmanager
async def _live_server(req):
    """Register a fixture server and grant echo+big, keeping it alive."""
    async with http_mcp_server(default_tools()) as url:
        sid = (
            await req("POST", "/api/v1/extensions/servers", json={"name": "s", "url": url})
        ).json()["serverId"]
        for tool in ("echo", "big"):
            assert (
                await req(
                    "POST", "/api/v1/extensions/grants", json={"serverId": sid, "toolName": tool}
                )
            ).status_code == 201
        yield sid


async def test_deny_exact_blocks_even_with_grant(client):
    req = as_user(client, "tok-eng-a")
    async with _live_server(req) as sid:
        rule = await req(
            "POST", "/api/v1/extensions/policy", json={"mode": "deny", "pattern": "echo"}
        )
        assert rule.status_code == 201, rule.text
        res = await req(
            "POST",
            "/api/v1/extensions/calls",
            json={"serverId": sid, "toolName": "echo", "arguments": {"text": "x"}},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "TOOL_DENIED_BY_POLICY"
        # Other granted tool still works.
        ok = await req(
            "POST",
            "/api/v1/extensions/calls",
            json={"serverId": sid, "toolName": "big", "arguments": {"bytes": 10}},
        )
        assert ok.status_code == 200, ok.text


async def test_deny_wildcard_blocks_prefix(client):
    req = as_user(client, "tok-eng-a")
    async with _live_server(req) as sid:
        await req("POST", "/api/v1/extensions/policy", json={"mode": "deny", "pattern": "ec*"})
        res = await req(
            "POST",
            "/api/v1/extensions/calls",
            json={"serverId": sid, "toolName": "echo", "arguments": {"text": "x"}},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "TOOL_DENIED_BY_POLICY"


async def test_grant_creation_blocked_for_denied_tool(client):
    req = as_user(client, "tok-eng-a")
    async with http_mcp_server(default_tools()) as url:
        sid = (
            await req("POST", "/api/v1/extensions/servers", json={"name": "s", "url": url})
        ).json()["serverId"]
        await req("POST", "/api/v1/extensions/policy", json={"mode": "deny", "pattern": "echo"})
        grant = await req(
            "POST", "/api/v1/extensions/grants", json={"serverId": sid, "toolName": "echo"}
        )
    assert grant.status_code == 409
    assert grant.json()["error"]["code"] == "TOOL_DENIED_BY_POLICY"


async def test_allowlist_mode_blocks_everything_not_listed(client):
    req = as_user(client, "tok-eng-a")
    async with _live_server(req) as sid:
        await req("POST", "/api/v1/extensions/policy", json={"mode": "allow", "pattern": "big"})
        blocked = await req(
            "POST",
            "/api/v1/extensions/calls",
            json={"serverId": sid, "toolName": "echo", "arguments": {"text": "x"}},
        )
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "TOOL_DENIED_BY_POLICY"
        allowed = await req(
            "POST",
            "/api/v1/extensions/calls",
            json={"serverId": sid, "toolName": "big", "arguments": {"bytes": 10}},
        )
        assert allowed.status_code == 200, allowed.text


async def test_policy_applies_to_custom_tool_names_and_sources(client):
    req = as_user(client, "tok-eng-a")
    async with _live_server(req) as sid:
        created = (
            await req(
                "POST",
                "/api/v1/extensions/custom-tools",
                json={"name": "wrapped-echo", "serverId": sid, "sourceTool": "echo"},
            )
        ).json()
        # Deny the SOURCE tool: the wrapper must not bypass policy.
        await req("POST", "/api/v1/extensions/policy", json={"mode": "deny", "pattern": "echo"})
        res = await req(
            "POST",
            f"/api/v1/extensions/custom-tools/{created['customToolId']}/call",
            json={"arguments": {"text": "x"}},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "TOOL_DENIED_BY_POLICY"

        # Remove the source denial; deny the WRAPPER name instead.
        rules = (await req("GET", "/api/v1/extensions/policy")).json()["items"]
        for r in rules:
            deleted = await req("DELETE", f"/api/v1/extensions/policy/{r['policyId']}")
            assert deleted.status_code == 200
        await req(
            "POST", "/api/v1/extensions/policy", json={"mode": "deny", "pattern": "wrapped-echo"}
        )
        res2 = await req(
            "POST",
            f"/api/v1/extensions/custom-tools/{created['customToolId']}/call",
            json={"arguments": {"text": "x"}},
        )
        assert res2.status_code == 403
        assert res2.json()["error"]["code"] == "TOOL_DENIED_BY_POLICY"


async def test_rule_validation_and_conflict(client):
    req = as_user(client, "tok-eng-a")
    bad = await req(
        "POST", "/api/v1/extensions/policy", json={"mode": "deny", "pattern": "a*b"}
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "POLICY_PATTERN_INVALID"
    first = await req(
        "POST", "/api/v1/extensions/policy", json={"mode": "deny", "pattern": "echo"}
    )
    assert first.status_code == 201
    dup = await req(
        "POST", "/api/v1/extensions/policy", json={"mode": "deny", "pattern": "echo"}
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "POLICY_RULE_CONFLICT"
