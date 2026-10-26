#!/usr/bin/env python3
"""W3 spike: does a user's Stop actually reach the confined worker and its children?

W2 (N01) established that the event-stream guard stops a *loop* but does not kill
anything: the SDK session has no cancel channel, so a stopped run keeps burning
until it ends by itself. W3's exit evidence starts with the opposite direction —
a human presses Stop, and the worker must die. The service already has a path for
it (`stop_agent` → `_kill_engine` → `engine.cancel` → daemon `/cancel` →
`boundary.kill`), and this spike measures whether that path does what it says
when the worker is genuinely mid-command.

Method: drive a run over HTTP whose goal makes the agent run a long `sleep`
through the shell tool. Wait until the worker container exists and the shell call
has had time to start, then POST /stop and measure when the containers disappear.
A killed container takes its child processes with it, so "container gone" is also
"the sleep is gone".

Spend: none (local Ollama, and the command sleeps rather than reasons).

    section4/.venv/bin/python scripts/e2e/w3_stop_spike.py
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
WORKER_NAME = re.compile(r"^harness-[0-9a-f]{32}(-relay)?$")
# The goal asks for one long command and nothing else, so the run spends its
# time in the shell rather than in the model. 180 s is far longer than the
# measurement window: if the worker is still alive afterwards, Stop failed.
SLEEP_SECONDS = 180
GOAL = (
    "You MUST call your bash/shell tool now. Run exactly this one command and wait for it to "
    f"return: `sleep {SLEEP_SECONDS}`. Do not read files, do not write files, do not explain, "
    "do not answer before the command returns. After it returns, reply DONE."
)


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


def run_containers(run_id: str) -> dict[str, str]:
    """{"worker"|"relay": container id} for THIS run's boundary.

    Container names carry a random uuid, not the run id, so a name filter cannot
    tell one run's boundary from another's — an earlier pass watched a leftover
    container from a previous run and read its survival as this run's. The run id
    IS visible in the worker's mounts (``.../workspace/<run_id>``), so that is
    the key. The relay has no mounts of its own but is named "<worker>-relay",
    which links it after the worker is identified.
    """
    rows = sh("docker", "ps", "--format", "{{.ID}} {{.Names}}", check=False).stdout.splitlines()
    candidates = [(line.partition(" ")[0], line.partition(" ")[2]) for line in rows
                  if WORKER_NAME.match(line.partition(" ")[2])]
    found: dict[str, str] = {}
    for cid, name in candidates:
        if name.endswith("-relay"):
            continue
        mounts = sh("docker", "inspect", cid, "--format",
                    "{{range .HostConfig.Mounts}}{{.Target}} {{end}}", check=False).stdout
        if f"/workspace/{run_id}" in mounts:
            found["worker"] = cid
            relay = next((c for c, n in candidates if n == f"{name}-relay"), None)
            if relay:
                found["relay"] = relay
            break
    return found


def live_pids_in(container: str) -> list[dict]:
    """Processes still running inside a container — the children a kill must take."""
    out = sh("docker", "top", container, "-eo", "pid,cmd", check=False).stdout
    rows = []
    for line in out.splitlines()[1:]:
        pid, _, cmd = line.strip().partition(" ")
        if cmd.strip():
            rows.append({"pid": pid, "cmd": cmd.strip()[:120]})
    return rows


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


def main() -> int:
    ap = argparse.ArgumentParser()
    # One file per run, named by run id. A fixed path meant a later inconclusive
    # sample silently replaced a decisive one — the measurement is only worth
    # anything if every sample survives, including the ones that say nothing.
    ap.add_argument("--evidence", default=None,
                    help="default: docs/briefs/evidence/pilot-prep/w3-stop-<runId>.json")
    ap.add_argument("--settle", type=float, default=150.0,
                    help="seconds to let the shell call start before stopping")
    ap.add_argument("--kill-window", type=float, default=30.0,
                    help="seconds to wait for the worker to disappear after /stop")
    args = ap.parse_args()

    stamp = int(time.time())
    run_id = f"w3stop-{stamp}"
    if args.evidence is None:
        args.evidence = str(REPO / "docs/briefs/evidence/pilot-prep" / f"w3-stop-{run_id}.json")
    email = f"w3-stop-{stamp}@example.com"
    evidence: dict = {"date": time.strftime("%Y-%m-%d"), "milestone": "W3 stop spike",
                      "runId": run_id, "sleepSeconds": SLEEP_SECONDS, "steps": []}
    steps = evidence["steps"]
    try:
        token = login(email)
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w3 stop {run_id}"})
        steps.append({"step": "project", "ok": status == 201, "status": status})
        base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": GOAL, "projectId": project["id"]})
        steps.append({"step": "plan", "ok": status == 201, "status": status})
        if status != 201:
            raise SystemExit(f"plan failed: {status} {plan!r}")
        status, _ = http("POST", f"{base}/plan/approve", token=token,
                         body={"planId": plan["planId"]})
        steps.append({"step": "approve", "ok": status == 200, "status": status})
        status, _ = http("POST", f"{base}/start", token=token)
        steps.append({"step": "start", "ok": status == 200, "status": status})

        # Wait for the WORKER (not the relay), then wait until the shell call is
        # actually running inside it. "It died" means nothing if nothing was
        # running, and the whole point is to kill a live command.
        t_start = time.monotonic()
        worker = None
        while time.monotonic() - t_start < 120:
            found = run_containers(run_id)
            if "worker" in found:
                worker = found["worker"]
                break
            time.sleep(1)
        steps.append({"step": "worker appeared", "ok": worker is not None,
                      "seconds": round(time.monotonic() - t_start, 2)})
        if worker is None:
            raise SystemExit("no worker container appeared; nothing to stop")
        evidence["workerContainer"] = worker

        deadline = time.monotonic() + args.settle
        procs: list[dict] = []
        while time.monotonic() < deadline:
            procs = live_pids_in(worker)
            if any("sleep" in p["cmd"] for p in procs):
                break
            time.sleep(1)
        # A worker with no live child would make "it died" meaningless: record
        # the processes that were actually running at the moment of the stop.
        evidence["processesAtStop"] = procs
        evidence["hadLiveChild"] = any("sleep" in p["cmd"] for p in procs)
        evidence["waitedForChildSeconds"] = round(time.monotonic() - t_start, 2)
        _, status_body = http("GET", f"{base}/status", token=token)
        evidence["stateAtStop"] = status_body.get("state") if isinstance(status_body, dict) else None

        t_stop = time.monotonic()
        status, stopped = http("POST", f"{base}/stop", token=token)
        steps.append({"step": "stop", "ok": status in (200, 202), "status": status,
                      "body": stopped if isinstance(stopped, dict) else str(stopped)[:200]})

        gone_after = relay_gone_after = None
        relay_id = run_containers(run_id).get("relay")
        while time.monotonic() - t_stop < args.kill_window:
            left = run_containers(run_id)
            if gone_after is None and "worker" not in left:
                gone_after = round(time.monotonic() - t_stop, 2)
            if relay_gone_after is None and "relay" not in left:
                relay_gone_after = round(time.monotonic() - t_stop, 2)
            if gone_after is not None and relay_gone_after is not None:
                break
            time.sleep(0.25)
        evidence["workerGoneAfterSeconds"] = gone_after
        evidence["relayGoneAfterSeconds"] = relay_gone_after
        evidence["relayContainer"] = relay_id
        evidence["workerStillAlive"] = gone_after is None
        evidence["survivorProcesses"] = (
            live_pids_in(worker) if gone_after is None else [])
        # The relay is created before the worker and must not outlive it either.
        time.sleep(2)
        _, status_body = http("GET", f"{base}/status", token=token)
        evidence["stateAfterStop"] = status_body.get("state") if isinstance(status_body, dict) else None
        # The route answers {"runId", "status": "stopped"}; it does not carry a
        # "confirmed" flag — confirmation is `engine_quiesced`, a separate
        # read. Recording an invented field as false would have looked like a
        # product failure when it was my drill guessing at the shape.
        evidence["stopApiStatus"] = stopped.get("status") if isinstance(stopped, dict) else None

        # The claim is only "Stop kills a live command", so a run whose child
        # never started cannot support it either way.
        evidence["ok"] = bool(evidence["hadLiveChild"] and gone_after is not None)
        evidence["verdict"] = (
            "stop killed a live shell command" if evidence["ok"] else
            "INCONCLUSIVE: no live shell child was observed before /stop"
            if not evidence["hadLiveChild"] else "worker survived /stop"
        )
    finally:
        Path(args.evidence).write_text(json.dumps(evidence, indent=1) + "\n")
        print(json.dumps(evidence, indent=1))
    print(f"\nevidence: {args.evidence}")
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
