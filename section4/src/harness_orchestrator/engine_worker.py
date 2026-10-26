"""Isolated engine worker: ``python -m harness_orchestrator.engine_worker``.

Reads ONE JSON request line on stdin, runs the SDK call in-process (this
process is the OS-confined, killable unit), writes NDJSON progress lines as the
runtime reports tool calls, then ONE result line — all on stdout. Credentials
arrive via the scrubbed environment, never argv or logs.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    req = json.loads(sys.stdin.readline())
    from harness_orchestrator.engine import DeepSeekHarnessWrapper

    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=req["workspace_root"],
        session_root=req["session_root"],
        model=req["model"],
        live_mode="dry",  # construction only; the call below is explicit
        isolated=False,  # we ARE the isolated process
    )
    wrapper.set_shell_policy(req["session_id"], req.get("shell_policy") or {})

    def emit_progress(record: dict) -> None:
        """One line per tool event; the parent persists it as it arrives."""
        sys.stdout.write(json.dumps({"progress": record}, default=str) + "\n")
        sys.stdout.flush()

    text, status, used, events = wrapper._run_live_events(
        req["goal"], req["session_id"], model=req["model"], on_progress=emit_progress
    )
    sys.stdout.write(
        json.dumps({"text": text, "status": status, "model": used, "events": events}, default=str)
        + "\n"
    )
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
