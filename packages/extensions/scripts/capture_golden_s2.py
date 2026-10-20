"""Capture A5-S2 interop artifacts: OpenAPI + golden fixtures for commands,
hooks, policy — full journeys against real fixture servers, normalized.

Run from the repo root:
    ./packages/extensions/.venv/bin/python packages/extensions/scripts/capture_golden_s2.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import tempfile

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling imports

from capture_golden import PREFIX, Recorder, normalize, principal_dep  # noqa: E402
from fixture_server import default_tools, http_mcp_server  # noqa: E402
from webhook_sink import webhook_sink  # noqa: E402

from harness_extensions.api import make_app  # noqa: E402
from harness_extensions.config import Settings  # noqa: E402

OUT = ROOT / "docs" / "agent-program" / "a5" / "session-2" / "interop"


def _settings(tmp: str) -> Settings:
    return Settings(
        db_path=str(pathlib.Path(tmp) / "registry.db"),
        allow_private_hosts=True,
        discover_timeout_s=5.0,
        call_timeout_s=5.0,
    )


async def main() -> None:
    tmp = tempfile.TemporaryDirectory(prefix="a5-s2-capture-")
    app = make_app(_settings(tmp.name), get_principal_dep=principal_dep)
    # Second app with private hosts DISALLOWED, to capture the refusal contract.
    strict_settings = Settings(
        db_path=str(pathlib.Path(tmp.name) / "strict.db"),
        allow_private_hosts=False,
        discover_timeout_s=5.0,
        call_timeout_s=5.0,
    )
    strict_app = make_app(strict_settings, get_principal_dep=principal_dep)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "openapi.json").write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print("wrote", OUT / "openapi.json")

    rec = Recorder()
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c,
        webhook_sink() as (hook_url, sink),
        http_mcp_server(default_tools()) as mcp_url,
    ):
        reg = await rec.step(
            "register_server",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/servers",
            json_body={"name": "fixture-echo", "url": mcp_url},
        )
        server_id = reg.json()["serverId"]
        await rec.step(
            "create_grant",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/grants",
            json_body={"serverId": server_id, "toolName": "echo"},
        )

        cmd = await rec.step(
            "create_command",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/commands",
            json_body={
                "name": "standup",
                "template": "Yesterday: {{yesterday}}\nToday: {{today}}",
                "arguments": ["yesterday", "today"],
                "description": "Standup helper",
            },
        )
        command_id = cmd.json()["commandId"]
        await rec.step(
            "invoke_command",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/commands/{command_id}/invoke",
            json_body={"arguments": {"yesterday": "shipped S1", "today": "ship S2"}},
        )
        await rec.step(
            "invoke_command_missing_argument_error",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/commands/{command_id}/invoke",
            json_body={"arguments": {"yesterday": "shipped S1"}},
        )

        hook = await rec.step(
            "create_hook",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/hooks",
            json_body={
                "name": "ops-webhook",
                "eventType": "tool_call.succeeded",
                "target": {"kind": "webhook", "url": hook_url},
            },
        )
        hook_id = hook.json()["hookId"]

        # Inline delivery: the call path itself fires the event.
        await rec.step(
            "call_tool_triggers_hook",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/calls",
            json_body={"serverId": server_id, "toolName": "echo", "arguments": {"text": "hi"}},
        )
        await rec.step(
            "hook_deliveries_after_call",
            c,
            "tok-eng-a",
            "GET",
            f"{PREFIX}/hooks/{hook_id}/deliveries",
        )
        assert len(sink.requests) == 1, "webhook sink saw no real delivery"

        await rec.step(
            "dispatch_manual_event",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/hooks/dispatch",
            json_body={"eventType": "tool_call.succeeded", "payload": {"note": "manual"}},
        )
        sink.status = 500
        await rec.step(
            "dispatch_event_delivery_failed",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/hooks/dispatch",
            json_body={"eventType": "tool_call.succeeded", "payload": {"note": "boom"}},
        )
        delivs = await rec.step(
            "hook_deliveries_delivered_and_failed",
            c,
            "tok-eng-a",
            "GET",
            f"{PREFIX}/hooks/{hook_id}/deliveries",
        )
        statuses = [d["status"] for d in delivs.json()["items"]]
        assert statuses == ["failed", "delivered", "delivered"], statuses
        sink.status = 200

        await rec.step(
            "create_policy_deny",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/policy",
            json_body={"mode": "deny", "pattern": "echo"},
        )
        await rec.step(
            "call_denied_by_policy",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/calls",
            json_body={"serverId": server_id, "toolName": "echo", "arguments": {"text": "hi"}},
        )
        await rec.step(
            "grant_denied_by_policy",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/grants",
            json_body={"serverId": server_id, "toolName": "echo"},
        )
        await rec.step("list_commands", c, "tok-eng-a", "GET", f"{PREFIX}/commands")
        await rec.step("list_hooks", c, "tok-eng-a", "GET", f"{PREFIX}/hooks")
        await rec.step("list_policy", c, "tok-eng-a", "GET", f"{PREFIX}/policy")

        # Refusal contract on the strict app: private webhook target denied.
        strict_transport = httpx.ASGITransport(app=strict_app)
        async with httpx.AsyncClient(
            transport=strict_transport, base_url="http://ext.test"
        ) as sc:
            res = await sc.post(
                f"{PREFIX}/hooks",
                json={
                    "name": "refused",
                    "eventType": "tool_call.succeeded",
                    "target": {"kind": "webhook", "url": "http://10.0.0.1/wiki"},
                },
                headers={"Authorization": "Bearer tok-eng-a"},
            )
            body = res.json()
            rec.entries.append(
                {
                    "name": "hook_private_target_refused",
                    "request": normalize(
                        {
                            "method": "POST",
                            "path": f"{PREFIX}/hooks",
                            "identity": "engineer@team-a",
                            "body": {
                                "name": "refused",
                                "eventType": "tool_call.succeeded",
                                "target": {"kind": "webhook", "url": "http://10.0.0.1/wiki"},
                            },
                        }
                    ),
                    "response": {"status": res.status_code, "body": normalize(body)},
                }
            )
            assert res.status_code == 400, res.text

    golden = OUT / "golden"
    golden.mkdir(exist_ok=True)
    for i, entry in enumerate(rec.entries, start=1):
        path = golden / f"{i:02d}_{entry['name']}.json"
        path.write_text(json.dumps(entry, indent=2) + "\n")
        print("wrote", path.name, entry["response"]["status"])
    tmp.cleanup()
    print(f"{len(rec.entries)} golden fixtures + openapi written")


if __name__ == "__main__":
    asyncio.run(main())
