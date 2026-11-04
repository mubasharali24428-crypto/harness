#!/usr/bin/env python3
"""Container-side probe for ``scripts/e2e/w7_lsp_real_drill.py``.

Runs inside the ``harness-engine-worker-lsp`` image on a ``--network none``
container as uid 10001, with the workspace mounted read-only. It performs the
four read-only LSP operations against the real (pylsp) server and records what
the sandbox actually did — including the server process's own environment, a
write attempt, a network attempt and a deadline kill.

Prints one JSON object; the drill turns it into checks.
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import socket
import sys
import time
from importlib import metadata

sys.path.insert(0, "/opt/harness-lsp")
from lsp_client import LspSession, LspTimeout

ROOT = "/work"


def timed(call):
    started = time.monotonic()
    value = call()
    return value, round(time.monotonic() - started, 3)


def process_environment(pid: int) -> dict[str, str]:
    """The real environment of the server process, not the client's claim."""
    raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
    out: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, _, value = item.partition(b"=")
        out[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return out


def main() -> int:
    out: dict[str, object] = {
        "cwd": os.getcwd(),
        "pylspVersion": metadata.version("python-lsp-server"),
        "pyflakesVersion": metadata.version("pyflakes"),
    }
    try:
        with LspSession(["pylsp"], cwd=ROOT, timeout=30.0) as session:
            init, out["initializeSeconds"] = timed(session.initialize)
            out["capabilities"] = sorted((init or {}).get("capabilities") or {})
            server_env = process_environment(session.pid)
            out["serverEnvironmentKeys"] = sorted(server_env)
            out["serverHome"] = server_env.get("HOME")
            out["serverTmpdir"] = server_env.get("TMPDIR")

            retry_uri, _ = timed(lambda: session.open_document(f"{ROOT}/pkg/retry.py"))
            out["retryUri"] = retry_uri
            out["diagnostics"], out["diagnosticsSeconds"] = timed(
                lambda: session.diagnostics(retry_uri, timeout=30.0))
            out["symbols"], out["symbolsSeconds"] = timed(
                lambda: session.document_symbols(retry_uri, timeout=30.0))

            jobs_uri, _ = timed(lambda: session.open_document(f"{ROOT}/pkg/jobs.py"))
            out["jobsUri"] = jobs_uri
            out["definition"], out["definitionSeconds"] = timed(
                lambda: session.definition(jobs_uri, 2, 10, timeout=30.0))
            out["references"], out["referencesSeconds"] = timed(
                lambda: session.references(retry_uri, 5, 5, timeout=30.0))

            clean_uri, _ = timed(lambda: session.open_document(f"{ROOT}/pkg/clean.py"))
            out["cleanUri"] = clean_uri
            out["cleanDiagnostics"], _ = timed(
                lambda: session.diagnostics(clean_uri, timeout=30.0))

            try:
                with open(f"{ROOT}/escape.txt", "w", encoding="utf-8") as handle:
                    handle.write("escape\n")
                out["workspaceWrite"] = {"wrote": True}
            except OSError as exc:
                out["workspaceWrite"] = {
                    "wrote": False, "error": f"{type(exc).__name__}: {exc}"}

            try:
                with socket.create_connection(("192.0.2.1", 443), timeout=2.0):
                    out["network"] = {"connected": True}
            except OSError as exc:
                out["network"] = {
                    "connected": False, "error": f"{type(exc).__name__}: {exc}"}

            # A cached pylsp answer can arrive inside 1 ms, so the deadline is
            # tested against a server that provably cannot answer: SIGSTOP it,
            # then make one request. The client must time out and kill it.
            os.kill(session.pid, signal.SIGSTOP)
            started = time.monotonic()
            try:
                session.request("textDocument/documentSymbol",
                                {"textDocument": {"uri": retry_uri}}, timeout=1.0)
                out["deadline"] = {"timedOut": False}
            except LspTimeout:
                out["deadline"] = {"timedOut": True}
            out["deadlineSeconds"] = round(time.monotonic() - started, 3)
            out["diedAfterDeadline"] = session.returncode is not None
    except Exception as exc:  # noqa: BLE001 - the drill records, never hides
        out["fatal"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
