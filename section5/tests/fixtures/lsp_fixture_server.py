#!/usr/bin/env python3
"""Stdlib fixture language server for the W4 LSP-client tests and drill.

It implements the smallest useful surface (initialize, shutdown, definition,
references) plus probe methods that make the client's limits observable:

* ``test/env``    — the environment keys the server actually received;
* ``test/cwd``    — the directory it runs in;
* ``test/network``— one outbound connect to TEST-NET-1 (192.0.2.0/24);
* ``test/write``  — one write attempt inside its cwd;
* ``test/slow``   — never answers inside a short deadline;
* ``test/flood``  — one frame larger than any sane byte cap.

Nothing here is a language server product; it exists so the client's process,
environment, byte and deadline limits can be tested without a real server.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time


def read_message() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii", "replace").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    payload = sys.stdin.buffer.read(length)
    try:
        parsed = json.loads(payload)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def send(message: dict) -> None:
    body = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


def location(uri: str) -> dict:
    return {
        "uri": uri,
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
    }


def handle(message: dict) -> None:
    method = message.get("method")
    request_id = message.get("id")

    def reply(result: object) -> None:
        send({"jsonrpc": "2.0", "id": request_id, "result": result})

    if method == "initialize":
        reply({
            "capabilities": {"definitionProvider": True, "referencesProvider": True},
            "serverInfo": {"name": "w4-fixture-lsp"},
        })
    elif method == "initialized":
        return
    elif method == "exit":
        raise SystemExit(0)
    elif method == "shutdown":
        reply(None)
    elif method == "textDocument/definition":
        reply(location("file:///work/pkg/retry.py"))
    elif method == "textDocument/references":
        reply([location("file:///work/pkg/retry.py"), location("file:///work/pkg/jobs.py")])
    elif method == "test/env":
        reply({"keys": sorted(os.environ)})
    elif method == "test/cwd":
        reply({"cwd": os.getcwd()})
    elif method == "test/network":
        try:
            with socket.create_connection(("192.0.2.1", 443), timeout=1.0):
                reply({"connected": True})
        except OSError as exc:
            reply({"error": f"{type(exc).__name__}: {exc}", "connected": False})
    elif method == "test/write":
        try:
            with open("escape.txt", "w", encoding="utf-8") as handle_out:
                handle_out.write("escape\n")
            reply({"wrote": True})
        except OSError as exc:
            reply({"wrote": False, "error": f"{type(exc).__name__}: {exc}"})
    elif method == "test/slow":
        time.sleep(30)
        reply({"late": True})
    elif method == "test/flood":
        body = json.dumps({"jsonrpc": "2.0", "id": request_id,
                           "result": {"blob": "x" * 5_000_000}}).encode("utf-8")
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        sys.stdout.buffer.flush()
    elif request_id is not None:
        send({"jsonrpc": "2.0", "id": request_id,
              "error": {"code": -32601, "message": f"no method {method}"}})


def main() -> int:
    while True:
        message = read_message()
        if message is None:
            return 0
        handle(message)


if __name__ == "__main__":
    raise SystemExit(main())
