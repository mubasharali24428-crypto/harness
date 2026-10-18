"""Stand-in engine worker for isolation/kill tests (selected by the request goal)."""

import json
import os
import subprocess
import sys
import time

req = json.loads(sys.stdin.readline())
goal, sid, root = req["goal"], req["session_id"], req["workspace_root"]
out = {"status": "completed", "model": "standin", "events": []}
if goal == "slow":  # a worker that spawns a grandchild and then hangs
    child = subprocess.Popen(["sleep", "60"])
    open(os.path.join(os.getcwd(), "child.pid"), "w").write(str(child.pid))
    time.sleep(60)
elif goal.startswith("write:"):
    name = goal.split(":", 1)[1]
    open(os.path.join(root, sid, name), "w").write("written by agent\n")
    out["text"] = "ok"
elif goal.startswith("delete:"):
    os.unlink(os.path.join(root, sid, goal.split(":", 1)[1]))
    out["text"] = "deleted"
elif goal.startswith("rewrite:"):
    with open(os.path.join(root, sid, goal.split(":", 1)[1]), "w") as f:
        f.write("".join(f"REWRITTEN {i}\n" for i in range(80)))
    out["text"] = "rewritten"
elif goal == "probe":
    res = {}

    def attempt(name, fn):
        try:
            fn()
            res[name] = "ALLOWED"
        except Exception as e:  # noqa: BLE001
            res[name] = "DENIED:" + type(e).__name__

    attempt("write_own", lambda: open(os.path.join(root, sid, "ok.txt"), "w").write("x"))
    attempt("write_other", lambda: open(os.path.join(root, "run-other", "pwn.txt"), "w").write("x"))
    attempt("read_other", lambda: open(os.path.join(root, "run-other", "secret.txt")).read())
    attempt("write_outside", lambda: open(os.path.expanduser("~/pwn_from_run.txt"), "w").write("x"))
    res["env"] = sorted(k for k in os.environ if k.startswith(("HARNESS_", "AWS_")))
    res["has_key"] = "DEEPSEEK_API_KEY" in os.environ
    out["text"] = json.dumps(res)
else:
    out["text"] = "noop"
sys.stdout.write(json.dumps(out) + "\n")
