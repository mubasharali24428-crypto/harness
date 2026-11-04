#!/usr/bin/env python3
"""W3: a run interrupted mid-write — rewind restores, resume adopts the disk.

The last open clause. Two policies were chosen for it: rewind reverts to the last
checkpoint, and resume adopts whatever is on disk (the common behaviour; see
docs/week-3-plan.md). This drill produces a genuinely TORN write rather than
pretending one happened:

* the fixture ships `slow_writer.py`, which writes 120 lines with a flush and a
  pause between each — so at any kill moment the file is real but incomplete;
* the run is PAUSED (not stopped: stop is terminal, pause is resumable) while
  that script is provably running inside the worker;
* the torn file is read back from the volume, so "mid-write" is an observation,
  not an assumption;
* rewind to the before-attempt checkpoint must remove `out.txt` entirely — it
  did not exist then, and a revert that leaves it is not a revert;
* resume must then run attempt 2 against that rewound tree, appending a fresh
  checkpoint pair.

Spend: none (local Ollama). The model only has to run one short command; if it
does not, the drill says so and records the sample instead of looping quietly.

    section4/.venv/bin/python scripts/e2e/w3_partial_write_drill.py
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
WORKER_NAME = re.compile(r"^harness-[0-9a-f]{32}(-relay)?$")
TOTAL_LINES = 120
GOAL = (
    "Use your bash tool to run exactly this one command and wait for it to finish: "
    "`python3 slow_writer.py`. Do not read or write any other file. When the command "
    "returns, reply DONE."
)
SLOW_WRITER = (
    "import time\n"
    "with open('out.txt', 'w') as f:\n"
    f"    for i in range({TOTAL_LINES}):\n"
    "        f.write(f'line {i}\\n')\n"
    "        f.flush()\n"
    "        time.sleep(0.15)\n"
)
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


def in_orchestrator(code: str) -> str:
    return sh("docker", "exec", ORCHESTRATOR_NAME, "python3", "-c", code, check=False).stdout


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
        "open(d + '/slow_writer.py', 'w').write(" + repr(SLOW_WRITER) + ")\n"
    )
    sh("docker", "exec", ORCHESTRATOR_NAME, "python3", "-c", code)


def read_file(run_id: str, name: str) -> str:
    return in_orchestrator(
        f"print(open({WORKSPACE_ROOT!r} + '/' + {run_id!r} + '/' + {name!r}).read())"
    )


def file_exists(run_id: str, name: str) -> bool:
    return in_orchestrator(
        f"import os; print(os.path.exists({WORKSPACE_ROOT!r} + '/' + {run_id!r} + '/' + {name!r}))"
    ).strip() == "True"


def _sampled_containers() -> list[dict]:
    """Every run-like container with its mount targets — evidence for the lookup."""
    rows = sh("docker", "ps", "--format", "{{.ID}} {{.Names}}", check=False).stdout.splitlines()
    out = []
    for line in rows:
        cid, _, name = line.partition(" ")
        if not WORKER_NAME.match(name):
            continue
        mounts = sh("docker", "inspect", cid, "--format",
                    "{{range .HostConfig.Mounts}}{{.Target}} {{end}}", check=False).stdout
        out.append({"name": name, "mounts": mounts.split()})
    return out


def worker_for(run_id: str) -> str | None:
    """This run's worker container, identified by the run id in its mounts."""
    rows = sh("docker", "ps", "--format", "{{.ID}} {{.Names}}", check=False).stdout.splitlines()
    for line in rows:
        cid, _, name = line.partition(" ")
        if not WORKER_NAME.match(name) or name.endswith("-relay"):
            continue
        mounts = sh("docker", "inspect", cid, "--format",
                    "{{range .HostConfig.Mounts}}{{.Target}} {{end}}", check=False).stdout
        if f"/workspace/{run_id}" in mounts:
            return cid
    return None


TOP_SEEN: list[dict] = []


def writer_running(container: str) -> bool:
    # "docker top <c> -eo cmd" fails with "Couldn't find PID field in ps output"
    # and still exits 0 — the PID column is mandatory, and without it every
    # sample came back empty while the process was demonstrably running.
    proc = sh("docker", "top", container, "-eo", "pid,cmd", check=False)
    out = proc.stdout
    found = any("slow_writer.py" in line for line in out.splitlines())
    # Keep a rolling window: "never matched" is only diagnosable with the actual
    # process table, and the interesting moment is whenever python3 appears.
    snippet = [ln.strip()[:90] for ln in out.splitlines()[1:] if ln.strip()]
    if not found and snippet and len(TOP_SEEN) < 200:
        TOP_SEEN.append({"t": round(time.time(), 1), "procs": snippet[:8],
                         "rc": proc.returncode, "err": proc.stderr[:120]})
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=str(
        REPO / "docs/briefs/evidence/pilot-prep/w3-partial-write.json"))
    ap.add_argument("--timeout", type=float, default=420.0)
    ap.add_argument("--write-settle", type=float, default=4.0,
                    help="seconds to let the writer land lines before pausing")
    args = ap.parse_args()

    stamp = int(time.time())
    run_id = f"w3torn-{stamp}"
    email = f"w3-torn-{stamp}@example.com"
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"), "milestone": "W3 partial write",
        "runId": run_id, "totalLines": TOTAL_LINES, "steps": [],
    }
    steps = evidence["steps"]
    try:
        token = login(email)
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w3 torn {run_id}"})
        steps.append({"step": "project", "ok": status == 201, "status": status})
        base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": GOAL, "projectId": project["id"]})
        steps.append({"step": "plan", "ok": status == 201, "status": status})
        if status != 201:
            raise SystemExit(f"plan failed: {status} {plan!r}")
        seed_fixture(run_id)
        status, _ = http("POST", f"{base}/plan/approve", token=token,
                         body={"planId": plan["planId"]})
        steps.append({"step": "approve", "ok": status == 200, "status": status})
        status, _ = http("POST", f"{base}/start", token=token)
        steps.append({"step": "start", "ok": status == 200, "status": status})

        # Wait for the writer to be provably running, then pause mid-write.
        worker, saw_writer = None, False
        deadline = time.time() + args.timeout
        samples: list[dict] = []
        while time.time() < deadline:
            worker = worker_for(run_id)
            if worker and writer_running(worker):
                saw_writer = True
                break
            if len(samples) < 6:
                # A lookup that never matches has to say what it DID see, or the
                # next debugging pass is a guess.
                samples.append({"t": round(time.time() - deadline + args.timeout, 1),
                                "containers": _sampled_containers()})
            _, body = http("GET", f"{base}/status", token=token)
            state = body.get("state") if isinstance(body, dict) else None
            if state in SETTLED:
                break
            time.sleep(0.5)
        evidence["containerSamples"] = samples
        evidence["topSamples"] = TOP_SEEN[-6:]
        steps.append({"step": "writer observed running", "ok": saw_writer,
                      "worker": worker})
        if saw_writer:
            # Pausing the instant the process appears can catch an empty file —
            # open() truncates before the first line lands. Let it write.
            time.sleep(args.write_settle)
        evidence["writerObserved"] = saw_writer
        if not saw_writer:
            evidence["verdict"] = (
                "INCONCLUSIVE: the model never ran slow_writer.py, so nothing was "
                "interrupted mid-write"
            )
            evidence["ok"] = False
            return 1

        status, _ = http("POST", f"{base}/pause", token=token)
        steps.append({"step": "pause", "ok": status in (200, 202), "status": status})
        time.sleep(2)  # let the container die before reading the file

        torn = read_file(run_id, "out.txt")
        lines = len([ln for ln in torn.splitlines() if ln.strip()])
        evidence["tornLines"] = lines
        evidence["tornContentHead"] = "\n".join(torn.splitlines()[:3])
        steps.append({"step": "file is torn", "ok": 0 < lines < TOTAL_LINES,
                      "lines": lines})

        _, cps = http("GET", f"{base}/checkpoints", token=token)
        items = cps.get("items") if isinstance(cps, dict) else []
        evidence["checkpointsBeforeRewind"] = items
        first = items[0]["id"] if items else None
        steps.append({"step": "before-attempt checkpoint exists", "ok": bool(first),
                      "id": first})

        status, rewound = http("POST", f"{base}/rewind", token=token,
                               body={"checkpoint": first})
        steps.append({"step": "rewind", "ok": status == 200, "status": status})
        evidence["rewind"] = rewound
        evidence["outExistsAfterRewind"] = file_exists(run_id, "out.txt")
        steps.append({"step": "torn file removed by rewind",
                      "ok": evidence["outExistsAfterRewind"] is False})

        # Resume must now run against the rewound tree.
        status, _ = http("POST", f"{base}/resume", token=token)
        steps.append({"step": "resume", "ok": status == 200, "status": status})
        deadline = time.time() + args.timeout
        state = None
        while time.time() < deadline:
            _, body = http("GET", f"{base}/status", token=token)
            state = body.get("state") if isinstance(body, dict) else None
            if state in SETTLED:
                break
            time.sleep(2)
        steps.append({"step": "resumed run settled", "ok": state in SETTLED, "state": state})
        _, cps2 = http("GET", f"{base}/checkpoints", token=token)
        labels = [c["label"] for c in (cps2.get("items") or [])]
        evidence["checkpointLabelsAfterResume"] = labels
        evidence["attemptTwoCheckpointed"] = any(
            label.startswith("before-attempt-2") for label in labels
        )
        steps.append({"step": "attempt 2 checkpointed",
                      "ok": evidence["attemptTwoCheckpointed"], "labels": labels})

        evidence["ok"] = bool(
            all(s["ok"] for s in steps)
            and 0 < lines < TOTAL_LINES
            and evidence["outExistsAfterRewind"] is False
        )
        evidence["verdict"] = (
            "torn write interrupted, rewind exact, resume ran a second attempt"
            if evidence["ok"] else "one or more clauses did not hold"
        )
    finally:
        Path(args.evidence).write_text(json.dumps(evidence, indent=1) + "\n")
        print(json.dumps(evidence, indent=1))
    print(f"\nevidence: {args.evidence}")
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
