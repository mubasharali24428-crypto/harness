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
