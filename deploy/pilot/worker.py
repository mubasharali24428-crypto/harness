"""Deterministic P1 worker; installed only in the isolation-tests image target."""

import json
import subprocess
import sys
import time
from pathlib import Path

request = json.loads(sys.stdin.readline())
workspace = Path(request["workspace_root"]) / request["session_id"]
goal = request["goal"]
if "P1_DELETE" in goal:
    (workspace / "gone.txt").unlink()
    message = "Stand-in deleted gone.txt; staging must withhold it."
elif "P1_DENY" in goal:
    # Three refused shell calls naming one path, in the shell plugin's own wording
    # (harness-shell.js): the orchestrator's loop guard must stop the run on its own.
    for n, cmd in enumerate(("cat deploy/.env", "head deploy/.env", "grep KEY deploy/.env")):
        for record in (
            {"phase": "call", "callId": f"d{n}", "name": "bash", "arguments": json.dumps({"command": cmd})},
            {"phase": "result", "callId": f"d{n}", "ok": False, "output": "",
             "error": "Denied by the harness command policy: path is outside the run scope."
                      ' [scope-request] {"path": "deploy/.env"}'},
        ):
            print(json.dumps({"progress": record}), flush=True)
    time.sleep(60)  # the guard's kill lands here; reaching the end means it never stopped the run
    message = "Stand-in was never stopped by the denial guard."
elif "P1_SLOW" in goal:
    child = subprocess.Popen(["sleep", "120"], start_new_session=True)
    (workspace / "child.pid").write_text(str(child.pid))
    time.sleep(120)
    message = "Stand-in wait finished."
else:
    (workspace / "hello.txt").write_text("pilot ok\n")
    message = "Deterministic P1 stand-in completed."
print(
    json.dumps(
        {"text": message, "status": "completed", "model": "pilot-standin", "events": []}
    )
)
