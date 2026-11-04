#!/usr/bin/env python3
"""W3: rewind a run's workspace to its checkpoint, over HTTP, on the deployed stack.

The policy chosen for W3 is "revert to the last checkpoint". Until this lens
existed there was nothing to revert to: a run executes in its own directory on
the `harness_runs` volume and nothing snapshotted it. This drill proves the whole
path end to end:

1. start a run whose agent edits a fixture file (the M07 shape);
2. `GET /runs/{id}/checkpoints` shows the before/after pair the attempt left;
3. `POST /runs/{id}/rewind` on the BEFORE checkpoint puts the file back to its
   unedited content;
4. a rewind aimed at a live run is refused with 409 rather than resetting files
   under a worker that is still writing them.

Spend: none (local Ollama).

    section4/.venv/bin/python scripts/e2e/w3_rewind_drill.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IDENTITY = "http://127.0.0.1:8001"
ORCHESTRATOR = "http://127.0.0.1:8004"
ORCHESTRATOR_NAME = "harness-orchestrator-1"
WORKSPACE_ROOT = "/var/lib/harness-runs/workspace"
CHECKPOINT_ROOT = "/var/lib/harness-runs/workspace/.checkpoints"
GOAL = (
    "calc.py has a bug: add() must return the sum of its arguments. Read calc.py, fix it with "
    "the edit or write tool, then read calc.py again to verify the fix, and reply DONE."
)
CALC_BUGGY = "def add(a, b):\n    return a - b\n"
SETTLED = {"done", "error", "failed", "execution_failed", "stopped", "cancelled", "blocked",
           "awaiting_done_signal"}


def http(method: str, url: str, *, token: str | None = None, body: dict | None = None,
         timeout: float = 30.0) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content, status = resp.read().decode("utf-8", "replace"), resp.status
    except urllib.error.HTTPError as e:
        content, status = e.read().decode("utf-8", "replace"), e.code
    try:
        return status, json.loads(content) if content else {}
    except ValueError:
        return status, content


def sh(*argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, check=check)


def fetch_otp(email: str) -> str:
    pattern = re.compile(rf"HARNESS-OTP login code for {re.escape(email)}: (\d+)")
    cid = sh("docker", "ps", "-q", "--filter",
             "label=com.docker.compose.service=identity").stdout.split()
    if not cid:
        raise SystemExit("identity container not found")
    deadline = time.time() + 30
    while time.time() < deadline:
        proc = sh("docker", "logs", "--tail", "400", cid[0], check=False)
        match = None
        for match in pattern.finditer(proc.stdout + proc.stderr):
            pass
        if match:
            return match.group(1)
        time.sleep(1)
    raise SystemExit(f"no OTP for {email}")


def login(email: str) -> str:
    status, _ = http("POST", f"{IDENTITY}/api/v1/auth/request-login", body={"email": email})
    assert status == 200, f"request-login {status}"
    status, body = http("POST", f"{IDENTITY}/api/v1/auth/verify-otp",
                        body={"email": email, "code": fetch_otp(email)})
    assert status == 200 and body.get("token"), f"verify-otp {status}"
    return body["token"]


def seed_fixture(run_id: str) -> None:
    code = (
        "import os\n"
        f"d = {WORKSPACE_ROOT!r} + '/' + {run_id!r}\n"
        "os.makedirs(d, exist_ok=True)\n"
        "open(d + '/calc.py', 'w').write(" + repr(CALC_BUGGY) + ")\n"
        "open(d + '/README.md', 'w').write('calc fixture\\n')\n"
    )
    sh("docker", "exec", ORCHESTRATOR_NAME, "python3", "-c", code)


def read_file(run_id: str, name: str) -> str:
    return sh(
        "docker", "exec", ORCHESTRATOR_NAME, "python3", "-c",
        f"print(open({WORKSPACE_ROOT!r} + '/' + {run_id!r} + '/' + {name!r}).read())",
        check=False,
    ).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=str(
        REPO / "docs/briefs/evidence/pilot-prep/w3-rewind.json"))
    ap.add_argument("--timeout", type=float, default=420.0)
    args = ap.parse_args()

    stamp = int(time.time())
    run_id = f"w3rewind-{stamp}"
    email = f"w3-rewind-{stamp}@example.com"
    evidence: dict = {"date": time.strftime("%Y-%m-%d"), "milestone": "W3 rewind",
                      "runId": run_id, "steps": []}
    steps = evidence["steps"]
    try:
        token = login(email)
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w3 rewind {run_id}"})
        steps.append({"step": "project", "ok": status == 201, "status": status})
        base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": GOAL, "projectId": project["id"]})
        steps.append({"step": "plan", "ok": status == 201, "status": status})
        if status != 201:
            raise SystemExit(f"plan failed: {status} {plan!r}")
        # Seed AFTER the plan created the run row but BEFORE it starts: the
        # "before attempt" snapshot must contain the buggy fixture.
        seed_fixture(run_id)
        evidence["calcBeforeRun"] = read_file(run_id, "calc.py")
        status, _ = http("POST", f"{base}/plan/approve", token=token,
                         body={"planId": plan["planId"]})
        steps.append({"step": "approve", "ok": status == 200, "status": status})

        status, _ = http("POST", f"{base}/start", token=token)
        steps.append({"step": "start", "ok": status == 200, "status": status})

        # A rewind while the worker is writing must be REFUSED, not raced: the
        # reset would interleave with the agent's own writes.
        #
        # Wait for a checkpoint to EXIST first. The first version of this drill
        # asked too early and got 409 CHECKPOINT_UNAVAILABLE — a refusal for the
        # wrong reason, which would have read as the race guard working when
        # nothing had been checkpointed at all.
        live_code, live_body = None, None
        for _ in range(240):
            _, status_body = http("GET", f"{base}/status", token=token)
            state = status_body.get("state") if isinstance(status_body, dict) else None
            _, cps = http("GET", f"{base}/checkpoints", token=token)
            count = len(cps.get("items") or []) if isinstance(cps, dict) else 0
            if state == "executing" and count:
                live_code, live_body = http("POST", f"{base}/rewind", token=token, body={})
                break
            if state not in (None, "executing", "planning", "awaiting_plan_approval"):
                break  # settled before a checkpoint appeared; the step below reports it
            time.sleep(0.5)
        live_error = (
            (live_body or {}).get("error", {}).get("code")
            if isinstance(live_body, dict) else None
        )
        steps.append({"step": "rewind refused while executing", "ok": live_code == 409
                      and live_error == "INVALID_STATE",
                      "status": live_code, "code": live_error,
                      "body": str(live_body)[:200]})

        deadline = time.time() + args.timeout
        state = None
        while time.time() < deadline:
            _, body = http("GET", f"{base}/status", token=token)
            state = body.get("state") if isinstance(body, dict) else None
            if state in SETTLED:
                break
            time.sleep(2)
        steps.append({"step": "settled", "ok": state in SETTLED, "state": state})
        evidence["calcAfterAgent"] = read_file(run_id, "calc.py")

        status, checkpoints = http("GET", f"{base}/checkpoints", token=token)
        items = checkpoints.get("items") if isinstance(checkpoints, dict) else None
        steps.append({"step": "list checkpoints", "ok": status == 200 and bool(items),
                      "status": status, "count": len(items or [])})
        evidence["checkpoints"] = items
        evidence["beforeSnapshotContainsCalc"] = bool(
            items and items[0].get("files", 0) >= 1)

        first = items[0]["id"] if items else None
        status, rewind = http("POST", f"{base}/rewind", token=token,
                              body={"checkpoint": first})
        steps.append({"step": "rewind to first checkpoint", "ok": status == 200,
                      "status": status})
        evidence["rewind"] = rewind
        evidence["calcAfterRewind"] = read_file(run_id, "calc.py")
        # read_file prints, so the two strings differ by print's newline; compare
        # stripped content — the first run of this drill reported a correct
        # restore as a failure for exactly that reason.
        evidence["fileRestored"] = (
            evidence["calcAfterRewind"].strip() == evidence.get("calcBeforeRun", "").strip()
            == CALC_BUGGY.strip()
        )
        evidence["ok"] = bool(
            all(s["ok"] for s in steps) and evidence["fileRestored"]
            and evidence["beforeSnapshotContainsCalc"]
        )
    finally:
        Path(args.evidence).write_text(json.dumps(evidence, indent=1) + "\n")
        print(json.dumps(evidence, indent=1))
    print(f"\nevidence: {args.evidence}")
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
