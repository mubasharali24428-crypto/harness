#!/usr/bin/env python3
"""A minimal stdio MCP server used to prove the harness can consume one.

Speaks newline-delimited JSON-RPC 2.0 as the MCP stdio transport does:
``initialize``, ``notifications/initialized``, ``tools/list``, ``tools/call``.
One tool, ``stub_echo``, returns a recognisable marker so a transcript can prove
the call actually crossed the server boundary.

    python3 scripts/e2e/mcp_stub_server.py
"""

from __future__ import annotations

import json
import os
import sys


def _log(entry: str) -> None:
    path = os.environ.get("STUB_MCP_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(entry + "\n")


def reply(message_id, result: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> int:
    _log("started")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        method = message.get("method")
        _log(f"method:{method}")
        message_id = message.get("id")
        if method == "initialize":
            reply(message_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "harness-stub", "version": "0.1.0"},
            })
        elif method == "tools/list":
            reply(message_id, {"tools": [{
                "name": "stub_echo",
                "description": "Echo text back with a marker. Use it to prove MCP reach.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }]})
        elif method == "tools/call":
            arguments = (message.get("params") or {}).get("arguments") or {}
            reply(message_id, {
                "content": [{"type": "text", "text": f"STUB-MCP-OK:{arguments.get('text', '')}"}],
                "isError": False,
            })
        elif message_id is not None:
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": message_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
