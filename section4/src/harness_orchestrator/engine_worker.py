"""Isolated engine worker: ``python -m harness_orchestrator.engine_worker``.

Reads ONE JSON request line on stdin, runs the SDK call in-process (this
process is the OS-confined, killable unit), writes ONE JSON result line to
stdout. Credentials arrive via the scrubbed environment, never argv or logs.
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
    text, status, used, events = wrapper._run_live_events(
        req["goal"], req["session_id"], model=req["model"]
    )
    sys.stdout.write(
        json.dumps({"text": text, "status": status, "model": used, "events": events}, default=str)
        + "\n"
    )
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
