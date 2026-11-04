#!/usr/bin/env python3
"""W23 exit: a real stop, seen through the surfaces a person reads.

One live run on the deployed stack, its agent held in a long shell command
(the W22 stop drill's method and helpers). While it runs:

* ``lifecycle.userStatus`` must be ``working`` (slice 2);
* guidance sent now must answer ``takesEffect: next_execution`` (slice 3): the
  engine call in flight has read its guidance already.

Then ``harness stop <run> --json`` (the real CLI, slice 4) must exit 0 only once
the server confirms the engine is gone, and afterwards:

* the worker and relay containers are gone and no container is left on the run;
* ``lifecycle`` reads ``stopped``, ``cancelConfirmed``, ``userStatus: finished``.

Spend: one short model turn (the agent sleeps rather than reasons).

    section4/.venv/bin/python scripts/e2e/w23_stop_drill.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from w22_stop_drill import (
    GOAL,
    IDENTITY,
    ORCHESTRATOR,
    http,
    live_pids_in,
    login,
    run_containers,
)

REPO = Path(__file__).resolve().parents[2]
SETTLE_SECONDS = 150


def lifecycle(base: str, token: str) -> dict:
    _, body = http("GET", base, token=token)
    return (body.get("lifecycle") or {}) if isinstance(body, dict) else {}


def harness_cli(token: str, *argv: str) -> tuple[int, dict]:
    env = {**os.environ, "HARNESS_TOKEN": token, "HARNESS_CONFIG_DIR": tempfile.mkdtemp(),
           "PYTHONPATH": str(REPO / "clients/python")}
    proc = subprocess.run(
        [sys.executable, "-m", "harness_sdk.cli.main", *argv,
         "--identity-url", IDENTITY, "--orchestrator-url", ORCHESTRATOR],
        env=env, capture_output=True, text=True, timeout=120, check=False)
    return proc.returncode, json.loads(proc.stdout or "{}")


def main() -> int:
    drill_id = f"w23-stop-{uuid.uuid4().hex[:12]}"
    run_id = f"w23stop-{int(time.time())}"
    out = REPO / "docs/briefs/evidence/w23" / f"{drill_id}.json"
    record: dict = {"drillId": drill_id, "runId": run_id, "verdict": "FAIL", "checks": {}}
    checks = record["checks"]
    try:
        record["git"] = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                                       capture_output=True, text=True, check=False).stdout.strip()
        token = login(f"{drill_id}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": drill_id})
        assert status == 201, f"project {status}"
        base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": GOAL, "projectId": project["id"]})
        assert status == 201, f"plan {status}: {plan}"
        assert http("POST", f"{base}/plan/approve", token=token,
                    body={"planId": plan["planId"]})[0] == 200
        assert http("POST", f"{base}/start", token=token)[0] == 200

        deadline, worker = time.monotonic() + 120, None
        while time.monotonic() < deadline and worker is None:
            worker = run_containers(run_id).get("worker")
            time.sleep(1)
        assert worker, "no worker container appeared"
        deadline, procs = time.monotonic() + SETTLE_SECONDS, []
        while time.monotonic() < deadline and not any("sleep" in p["cmd"] for p in procs):
            procs = live_pids_in(worker)
            time.sleep(1)
        record["processesAtStop"] = procs
        checks["liveChildBeforeStop"] = any("sleep" in p["cmd"] for p in procs)

        record["lifecycleRunning"] = lifecycle(base, token)
        checks["userStatusWorking"] = record["lifecycleRunning"].get("userStatus") == "working"

        assert http("POST", f"{base}/join", token=token)[0] == 200
        status, sent = http("POST", f"{base}/redirect", token=token,
                            body={"kind": "guidance", "content": "w23 drill: no-op note"})
        record["guidance"] = {"status": status, "body": sent}
        checks["guidanceSaysNextExecution"] = status == 200 and \
            isinstance(sent, dict) and sent.get("takesEffect") == "next_execution"

        t_stop = time.monotonic()
        code, stopped = harness_cli(token, "stop", run_id, "--json", "--wait", "60")
        record["cliStop"] = {"exitCode": code, "body": stopped,
                             "seconds": round(time.monotonic() - t_stop, 2)}
        checks["cliStopConfirmed"] = code == 0 and stopped.get("confirmed") is True \
            and stopped.get("status") == "stopped"

        time.sleep(2)
        left = run_containers(run_id)
        record["containersAfterStop"] = left
        checks["noOrphanContainers"] = not left
        run_net = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={worker}", "--format", "{{.Names}}"],
            capture_output=True, text=True, check=False).stdout.split()
        record["workerByNameAfterStop"] = run_net
        checks["workerContainerRemoved"] = not run_net

        after = lifecycle(base, token)
        record["lifecycleAfter"] = after
        checks["lifecycleConfirmed"] = after.get("phase") == "stopped" and \
            after.get("cancelConfirmed") is True and after.get("mutationPossible") is False
        checks["userStatusFinished"] = after.get("userStatus") == "finished"

        code, status_out = harness_cli(token, "status", run_id, "--json")
        record["cliStatus"] = {"exitCode": code, "userStatus": status_out.get("userStatus")}
        checks["cliStatusFinished"] = code == 0 and status_out.get("userStatus") == "finished"
    except Exception as exc:  # noqa: BLE001 - the failure is the evidence
        record["error"] = repr(exc)
    finally:
        record["verdict"] = "PASS" if checks and all(checks.values()) and \
            "error" not in record else "FAIL"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"drillId": drill_id, "verdict": record["verdict"], "checks": checks,
                      "error": record.get("error"), "evidence": str(out)}, indent=1))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
