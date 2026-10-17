"""F023: child entrypoint — one engine call, result as a ``@@RESULT@@`` line.

Runs in its own process group with an explicit environment (see
process_engine.build_child_env); the parent SIGKILLs the group on timeout.
"""

from __future__ import annotations

import json
import os
import sys

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.process_engine import RESULT_MARKER


def main() -> int:
    req = json.loads(sys.stdin.read())
    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=os.environ["DSH_WORKSPACE"],
        session_root=os.environ["DSH_SESSION_ROOT"],
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        base_url=os.environ.get("DEEPSEEK_BASE_URL", ""),
        model=os.environ.get("DSH_MODEL"),
        live_mode="live",
    )
    result = wrapper.execute_goal(req["goal"], req["sessionId"])
    sys.stdout.write(RESULT_MARKER + json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
