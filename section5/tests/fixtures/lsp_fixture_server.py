#!/usr/bin/env python3
"""Stdlib fixture language server for the W4/W7 LSP-client tests and drills.

It implements the smallest useful surface (initialize, shutdown, definition,
references, document symbols, diagnostics on open) plus probe methods that make
the client's limits observable:

* ``test/env``    — the environment keys the server actually received;
* ``test/cwd``    — the directory it runs in;
* ``test/network``— one outbound connect to TEST-NET-1 (192.0.2.0/24);
* ``test/write``  — one write attempt inside its cwd;
* ``test/slow``   — never answers inside a short deadline;
* ``test/flood``  — one frame larger than any sane byte cap.

During ``initialize`` it sends one server-to-client request
(``workspace/configuration``) and reports whether the client answered, so the
"answer housekeeping requests or real servers block" path is observable.

Nothing here is a language server product; it exists so the client's process,
environment, byte and deadline limits can be tested without a real server.
"""

from __future__ import annotations

import json
import os
import select
import socket
import sys
import time

_BUFFER = bytearray()
_OPEN: dict[str, str] = {}

_SYMBOL_KIND = {"def": 12, "class": 5}


def _fill(deadline: float | None) -> bool:
    """Read one chunk from stdin (select when a deadline is given)."""
    if deadline is None:
        chunk = os.read(0, 65536)
    else:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        ready, _, _ = select.select([0], [], [], remaining)
        if not ready:
            return False
        chunk = os.read(0, 65536)
    if not chunk:
        return False
    _BUFFER.extend(chunk)
    return True


def read_message(timeout: float | None = None) -> dict | None:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        sep = _BUFFER.find(b"\r\n\r\n")
        if sep >= 0:
            header = _BUFFER[:sep].decode("ascii", "replace")
            length = 0
            for line in header.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            start = sep + 4
            while len(_BUFFER) < start + length:
                if not _fill(deadline):
                    return None
            body = bytes(_BUFFER[start:start + length])
            del _BUFFER[:start + length]
            try:
                parsed = json.loads(body)
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None
        if not _fill(deadline):
            return None


def send(message: dict) -> None:
    body = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


def location(uri: str) -> dict:
    return {
        "uri": uri,
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
    }


def analyze(text: str) -> list[dict]:
    """One error per ``undefined_name`` line — enough for a diagnostics test."""
    out = []
    for number, line in enumerate(text.splitlines()):
        if "undefined_name" in line:
            column = line.index("undefined_name")
            out.append({
                "range": {
                    "start": {"line": number, "character": column},
                    "end": {"line": number, "character": column + len("undefined_name")},
                },
                "severity": 1,
                "source": "w4-fixture-lsp",
                "message": "undefined name 'undefined_name'",
            })
    return out


def symbols(uri: str, text: str) -> list[dict]:
    """Top-level ``def``/``class`` lines as DocumentSymbols."""
    out = []
    for number, line in enumerate(text.splitlines()):
        stripped = line.strip()
        keyword = stripped.split(" ", 1)[0] if stripped else ""
        if keyword not in _SYMBOL_KIND or not stripped.endswith(":"):
            continue
        name = stripped[len(keyword):].strip().rstrip(":").split("(", 1)[0].strip()
        if not name.isidentifier():
            continue
        span = {
            "start": {"line": number, "character": 0},
            "end": {"line": number, "character": len(line)},
        }
        out.append({
            "name": name,
            "kind": _SYMBOL_KIND[keyword],
            "range": span,
            "selectionRange": span,
        })
    return out


def client_answers_configuration() -> bool | None:
    """Ask the client for configuration; ``None`` means it never answered."""
    send({"jsonrpc": "2.0", "id": 900, "method": "workspace/configuration",
          "params": {"items": [{"section": "python"}]}})
    while True:
        message = read_message(timeout=2.0)
        if message is None:
            return None
        if message.get("id") == 900:
            return "result" in message


def handle(message: dict) -> None:
    method = message.get("method")
    request_id = message.get("id")

    def reply(result: object) -> None:
        send({"jsonrpc": "2.0", "id": request_id, "result": result})

    if method == "initialize":
        answered = client_answers_configuration()
        reply({
            "capabilities": {
                "definitionProvider": True,
                "referencesProvider": True,
                "documentSymbolProvider": True,
                "textDocumentSync": 1,
            },
            "serverInfo": {"name": "w4-fixture-lsp", "clientAnswered": answered},
        })
    elif method == "initialized":
        return
    elif method == "exit":
        raise SystemExit(0)
    elif method == "shutdown":
        reply(None)
    elif method == "textDocument/didOpen":
        document = (message.get("params") or {}).get("textDocument") or {}
        uri = str(document.get("uri") or "")
        text = str(document.get("text") or "")
        _OPEN[uri] = text
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
              "params": {"uri": uri, "diagnostics": analyze(text)}})
    elif method == "textDocument/documentSymbol":
        uri = str(((message.get("params") or {}).get("textDocument") or {}).get("uri") or "")
        reply(symbols(uri, _OPEN.get(uri, "")))
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
    elif method == "test/notifyFlood":
        for index in range(300):
            send({"jsonrpc": "2.0", "method": "test/tick", "params": {"i": index}})
        reply({"sent": 300})
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
