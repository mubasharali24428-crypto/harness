"""Stand-in engine worker for isolation/kill tests (selected by the request goal)."""

import json
import os
import pwd
import subprocess
import sys
import time

try:
    REAL_HOME = pwd.getpwuid(os.getuid()).pw_dir
except KeyError:  # container uses the host uid for bind-mount ownership
    REAL_HOME = "/home/worker"
# HOME itself is repointed to the run tmp dir.
req = json.loads(sys.stdin.readline())
# W2 (N02): the engine now appends a repository-context block to the goal, so the
# directive is the first line rather than the whole string. Parsing the whole
# string made `delete:gone.txt` resolve to "gone.txt\n\n## Repository context…",
# which silently turned this worker into a no-op and failed five staging tests —
# the double's assumption, not the product change, was wrong.
goal, sid, root = req["goal"].splitlines()[0].strip(), req["session_id"], req["workspace_root"]
out = {"status": "completed", "model": "standin", "events": []}
if goal == "slow":  # a worker that spawns a grandchild and then hangs
    child = subprocess.Popen(["sleep", "60"])
    open(os.path.join(os.getcwd(), "child.pid"), "w").write(str(child.pid))
    time.sleep(60)
elif goal == "progress":  # one transcript record before the result, as the real worker streams
    print(json.dumps({"progress": {"phase": "call", "tool": "read", "callId": "c1"}}), flush=True)
    out["text"] = "streamed"
elif goal == "crash":  # a worker that dies mid-run (OOM kill / segfault stand-in)
    os._exit(7)
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
elif goal == "probe" or goal.startswith("probe:"):
    res = {}
    other = goal.split(":", 1)[1] if ":" in goal else "run-other"

    def attempt(name, fn):
        try:
            fn()
            res[name] = "ALLOWED"
        except Exception as e:  # noqa: BLE001
            res[name] = "DENIED:" + type(e).__name__

    attempt("write_own", lambda: open(os.path.join(root, sid, "ok.txt"), "w").write("x"))
    attempt("write_other", lambda: open(os.path.join(root, other, "pwn.txt"), "w").write("x"))
    attempt("read_other", lambda: open(os.path.join(root, other, "secret.txt")).read())
    outside = os.path.join(REAL_HOME, "pwn_from_run.txt")
    attempt("write_outside", lambda: open(outside, "w").write("x"))
    res["env"] = sorted(k for k in os.environ if k.startswith(("HARNESS_", "AWS_")))
    res["has_key"] = "DEEPSEEK_API_KEY" in os.environ
    out["text"] = json.dumps(res)
else:
    out["text"] = "noop"
sys.stdout.write(json.dumps(out) + "\n")
