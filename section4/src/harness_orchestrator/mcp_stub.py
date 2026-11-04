"""First-party stub MCP server, shipped inside the worker image (W4 slice 3).

Run as ``python -m harness_orchestrator.mcp_stub`` over the stdio transport.
It exists so "a run uses a third-party MCP tool and the audit records it" can be
built one variable at a time: the mechanics (spawn, tool registration, tool
events) are proven against a server whose behaviour we can read, before any
real third-party server is reviewed.

Two tools:

* ``stub_echo`` — returns a recognisable marker, so a transcript can prove the
  call actually crossed the server boundary rather than being model invention;
* ``env_probe`` — returns only the NAMES of environment variables that look like
  credentials, never their values. That is the point: the reviewed config gives a
  spawned server no credentials, and this makes the claim observable from inside
  the sandbox instead of asserted from outside it.
"""

from __future__ import annotations

import json
import os
import sys

MARKER = "HARNESS-MCP-STUB"
PROBE_PATTERNS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "HARNESS_", "DEEPSEEK_", "AWS_", "GITHUB_")

TOOLS = [
    {
        "name": "stub_echo",
        "description": (
            "Return a fixed marker with the given text appended. Use it to prove "
            "this MCP server was really called."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "env_probe",
        "description": (
            "Return the NAMES (never values) of environment variables that look "
            "like credentials. Used to prove a confined run leaks nothing here."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _reply(message_id, result: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n")
    sys.stdout.flush()


def _error(message_id, text: str) -> None:
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": message_id,
                    "error": {"code": -32601, "message": text}}) + "\n"
    )
    sys.stdout.flush()


def handle(message: dict) -> None:
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        _reply(message_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "harness-stub", "version": "0.1.0"},
        })
    elif method == "tools/list":
        _reply(message_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        if name == "stub_echo":
            text = str((params.get("arguments") or {}).get("text", ""))
            _reply(message_id, {
                "content": [{"type": "text", "text": f"{MARKER}:{text}"}], "isError": False
            })
        elif name == "env_probe":
            found = sorted(k for k in os.environ if any(p in k.upper() for p in PROBE_PATTERNS))
            _reply(message_id, {
                "content": [{"type": "text",
                             "text": json.dumps({"credentialLikeNames": found})}],
                "isError": False,
            })
        elif message_id is not None:
            _error(message_id, f"unknown tool: {name}")
    elif message_id is not None:
        _error(message_id, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("method") == "notifications/initialized":
            continue
        handle(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
