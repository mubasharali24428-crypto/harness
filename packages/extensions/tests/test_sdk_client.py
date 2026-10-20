"""Extension SDK (authoring) + REST client (consumption), end to end."""

from __future__ import annotations

import httpx
import pytest
from conftest import build_app, make_settings

from harness_extensions.client import ExtensionsApiError, ExtensionsClient
from harness_extensions.sdk import ExtensionApp, ExtensionError


def _extension() -> tuple[ExtensionApp, list[dict]]:
    app = ExtensionApp("team-tools")

    @app.command("incident-page", template="Page {{service}} on-call about {{issue}}.")
    def page(service: str, issue: str) -> str:
        return f"Page {service} on-call about {issue}."

    captured: list[dict] = []

    @app.hook("tool_call.failed")
    def alert(event) -> None:
        captured.append({"type": event.eventType, "payload": event.payload})

    return app, captured


def test_sdk_declarations_and_local_dry_runs():
    ext, captured = _extension()
    (spec,) = ext.commands.values()
    assert spec.name == "incident-page"
    assert spec.arguments == ["issue", "service"]
    assert ext.render("incident-page", service="api", issue="5xx spike") == (
        "Page api on-call about 5xx spike."
    )

    with pytest.raises(ExtensionError) as err:
        ext.render("incident-page", service="api")
    assert err.value.code == "EXT_COMMAND_ARGS_MISSING"

    results = ext.dispatch("tool_call.failed", {"toolName": "deploy"})
    assert results == [None]
    assert captured[0] == {"type": "tool_call.failed", "payload": {"toolName": "deploy"}}


def test_sdk_rejects_bad_declarations():
    ext = ExtensionApp("bad")

    with pytest.raises(ExtensionError) as err:

        @ext.command("broken", template="uses {{missing}}", arguments=[])
        def broken() -> str:  # pragma: no cover — declaration fails first
            return ""

    assert err.value.code == "EXT_COMMAND_TEMPLATE_INVALID"

    with pytest.raises(ExtensionError) as err2:

        @ext.hook("not.a.real.event")
        def wat(event) -> None:  # pragma: no cover — declaration fails first
            pass

    assert err2.value.code == "EXT_HOOK_EVENT_INVALID"


async def test_client_end_to_end_against_service(tmp_path):
    settings = make_settings(tmp_path)
    app = build_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as http:
        client = ExtensionsClient("http://ext.test", token="tok-eng-a", client=http)

        from fixture_server import default_tools, http_mcp_server

        async with http_mcp_server(default_tools()) as url:
            server = await client.register_server(name="echo", url=url)
            assert server["health"] == "healthy"
            tools = await client.list_server_tools(server["serverId"])
            assert {t["name"] for t in tools} == {"echo", "slow", "big", "crash"}

            await client.create_grant(server_id=server["serverId"], tool_name="echo")
            result = await client.call_tool(
                server_id=server["serverId"], tool_name="echo", arguments={"text": "sdk"}
            )
            assert result["status"] == "succeeded"
            assert result["blocks"][0]["text"] == "echo:sdk"

        command = await client.create_command(
            name="standup",
            template="Yesterday: {{yesterday}}. Today: {{today}}.",
            arguments=["yesterday", "today"],
        )
        rendered = await client.invoke_command(
            command["commandId"], {"yesterday": "shipped S1", "today": "ship S2"}
        )
        assert rendered["renderedText"] == "Yesterday: shipped S1. Today: ship S2."

        skill = await client.create_skill(name="sdk-skill", content="body")
        assert skill["skill"]["latestVersion"] == 1

        catalog = await client.list_catalog()
        assert {e["pluginId"] for e in catalog} >= {"incident-response"}
        installed = await client.install_plugin("repo-hygiene")
        assert installed["status"] == "installed"
        uninstalled = await client.uninstall_plugin("repo-hygiene")
        assert uninstalled["status"] == "removed"


async def test_client_raises_typed_errors_from_envelope(tmp_path):
    settings = make_settings(tmp_path)
    app = build_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as http:
        eng = ExtensionsClient("http://ext.test", token="tok-eng-a", client=http)
        viewer = ExtensionsClient("http://ext.test", token="tok-view-a", client=http)

        with pytest.raises(ExtensionsApiError) as err:
            await viewer.create_skill(name="nope", content="x")
        assert (err.value.status_code, err.value.code) == (403, "ROLE_DENIED")

        with pytest.raises(ExtensionsApiError) as err2:
            await eng.get_server("11111111-1111-1111-1111-111111111111")
        assert (err2.value.status_code, err2.value.code) == (404, "SERVER_NOT_FOUND")
