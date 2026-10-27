#!/usr/bin/env python3
"""W4 probe: can the SDK's session-level ``mcpServers`` carry a stdio MCP server?

The runtime's protocol puts ``mcpServers`` on ``session/new``; the Python SDK's
typed ``initialize`` has no parameter for it. This probe sends the field through
the SDK's raw ``request()`` path and asks the model to call the stub tool. It
prints exactly what happened — an accepted server, an ignored field, or a
protocol error — before any production wiring is designed around it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from deepseek_harness import HarnessClient, HarnessConfig, InitializeResponse

REPO = Path(__file__).resolve().parents[2]
STUB = REPO / "scripts" / "e2e" / "mcp_stub_server.py"
PROMPT = (
    "Call the `stub_echo` tool with text 'hello-from-w4' and reply with exactly what it "
    "returned. If no such tool is available, reply exactly: NO_MCP_TOOL."
)


def main() -> int:
    home = tempfile.mkdtemp(prefix="w4mcp-")
    stub_log = Path(home) / "stub.log"
    env = {
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY") or "local",
        "DEEPSEEK_BASE_URL": os.environ.get("DEEPSEEK_BASE_URL")
        or "http://127.0.0.1:11434/v1",
    }
    model = os.environ.get("DSH_MODEL") or "qwen-local:latest"
    client = HarnessClient(HarnessConfig(profile="sdk-minimal", dsh_home=home, cwd=home, env=env))
    client.start()
    payload = {
        "cwd": home,
        "provider": "deepseek-official",
        "model": model,
        "mcpServers": [
            {
                "name": "stub",
                "command": sys.executable,
                "args": [str(STUB)],
                "env": {"STUB_MCP_LOG": str(stub_log)},
            },
        ],
    }
    try:
        client.request("initialize", payload, response_model=InitializeResponse)
    except Exception as exc:  # noqa: BLE001 - the point is to record the refusal verbatim
        print(json.dumps({"initialize": "failed", "error": repr(exc)[:1200]}, indent=1))
        client.close()
        return 1

    session_id = f"w4mcp-{int(time.time())}"
    route = "initialize+prompt"
    new_session_error = None
    try:
        from pydantic import BaseModel

        class NewSessionResponse(BaseModel):
            sessionId: str

        # The runtime's own protocol puts mcpServers on session/new (the typed
        # initialize above silently ignored them and never spawned the stub).
        created = client.request(
            "session/new", {"cwd": home, "mcpServers": payload["mcpServers"]},
            response_model=NewSessionResponse,
            timeout_seconds=60,
        )
        session_id = created.sessionId
        route = "session/new"
    except Exception as exc:  # noqa: BLE001 - record the exact refusal
        new_session_error = repr(exc)[:800]
    events: list[dict] = []
    tool_names: list[str] = []
    with client.subscribe_session_notifications(session_id) as subscription:
        client.session_prompt(session_id, [{"type": "text", "text": PROMPT}],
                              notification_subscription=subscription)
        deadline = time.time() + 120
        idle = False
        while time.time() < deadline:
            notification = subscription.next()
            if notification.method == "session.event":
                event = notification.payload.get("event")
                if isinstance(event, dict):
                    events.append(event)
                    if event.get("type") == "tool/call":
                        tool_names.append(str((event.get("data") or {}).get("name")))
            if notification.method == "session.status" and notification.payload.get("status") == "idle":
                idle = True
                break
    client.close()

    answer = ""
    for event in reversed(events):
        if event.get("type") == "assistant/message":
            data = event.get("data") or {}
            content = (data.get("message") or data).get("content")
            if isinstance(content, list):
                answer = "".join(
                    str(block.get("text", "")) for block in content if isinstance(block, dict)
                )
                break
    result = {
        "initialize": "accepted with mcpServers",
        "route": route,
        "newSessionError": new_session_error,
        "sessionId": session_id,
        "idle": idle,
        "eventTypes": [str(e.get("type")) for e in events],
        "systemMessage": next(
            (str((e.get("data") or {}).get("text", ""))[:1500]
             for e in events if e.get("type") == "system/message"),
            "",
        ),
        "toolCalls": tool_names,
        "answer": answer[:400],
        "stubProcessLog": stub_log.read_text(encoding="utf-8") if stub_log.exists() else "",
        "sawStubTool": any("stub" in name for name in tool_names),
        "answerMentionsStub": "STUB-MCP-OK" in answer,
    }
    print(json.dumps(result, indent=1))
    return 0 if result["sawStubTool"] or result["answerMentionsStub"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
