#!/usr/bin/env python3
"""W22: a stopped run reads the same after the orchestrator and identity restart.

Starts a run whose child sleeps, stops it, snapshots lifecycle, events (REST and
WebSocket replay) and the run's containers, restarts both services, and takes
the same snapshot again. Pass: identical lifecycle and event sequence, the stop
still confirmed, no worker or child back, and no claim.
"""
import asyncio
import json
import time
import uuid
from pathlib import Path

import websockets
from w3_stop_spike import (
    IDENTITY,
    ORCHESTRATOR,
    http,
    live_pids_in,
    login,
    run_containers,
    sh,
)

SERVICES = ("harness-orchestrator-1", "harness-identity-1")


def psql(sql):
    return sh("docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness",
              "-Atc", sql).stdout.strip()


async def ws_replay(pid, token):
    seqs = []
    async with websockets.connect(f"ws://127.0.0.1:8001/api/v1/ws/{pid}",
                                  additional_headers={"Authorization": f"Bearer {token}"}) as ws:
        try:
            while True:
                seqs.append(json.loads(await asyncio.wait_for(ws.recv(), 3))["seq"])
        except TimeoutError:
            return seqs


def snapshot(run_id, pid, token):
    _, status = http("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}/status",
                     token=token)
    _, rest = http("GET", f"{IDENTITY}/api/v1/projects/{pid}/events/replay?after_seq=0",
                   token=token)
    return {
        "state": status.get("state"),
        "lifecycle": status.get("lifecycle"),
        "restEvents": [f'{e["seq"]}:{e["type"]}' for e in rest["items"]],
        "wsSeqs": asyncio.run(ws_replay(pid, token)),
        "claim": psql(f"SELECT state FROM run_claims WHERE run_id='{run_id}'"),
        "containers": run_containers(run_id),
    }


def wait_healthy(deadline_s=180):
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        states = [sh("docker", "inspect", "-f", "{{.State.Health.Status}}", s).stdout.strip()
                  for s in SERVICES]
        if states == ["healthy", "healthy"]:
            return True
        time.sleep(2)
    return False


def main():
    run_id = f"w22-restart-{uuid.uuid4().hex[:12]}"
    record = {"runId": run_id, "verdict": "FAIL"}
    out = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22" / f"{run_id}.json"
    base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
    token = None
    try:
        record["images"] = {s: sh("docker", "inspect", "-f", "{{.Config.Image}} {{.Image}}", s)
                            .stdout.strip() for s in SERVICES}
        token = login(f"{run_id}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": run_id})
        assert status == 201
        pid = project["id"]
        goal = ("Run exactly `python3 -c 'import time; time.sleep(200)'` using bash. "
                "Wait for it to finish; do nothing else.")
        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": goal, "projectId": pid})
        assert status == 201
        assert http("POST", f"{base}/plan/approve", token=token,
                    body={"planId": plan["planId"]})[0] == 200
        assert http("POST", f"{base}/start", token=token)[0] == 200
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            worker = run_containers(run_id).get("worker")
            if worker and any("time.sleep(200)" in p["cmd"] for p in live_pids_in(worker)):
                break
            time.sleep(1)
        else:
            raise RuntimeError("child was not observed")
        record["childObserved"] = True
        record["stopStatus"] = http("POST", f"{base}/stop", token=token)[0]
        time.sleep(5)
        before = record["beforeRestart"] = snapshot(run_id, pid, token)
        started = time.monotonic()
        sh("docker", "restart", *SERVICES)
        record["healthyAfterRestart"] = wait_healthy()
        record["restartSeconds"] = round(time.monotonic() - started, 1)
        time.sleep(15)  # several worker cycles, including orphan recovery at start
        after = record["afterRestart"] = snapshot(run_id, pid, token)
        checks = record["checks"] = {
            "stoppedBefore": (before["lifecycle"] or {}).get("phase") == "stopped",
            "lifecycleSame": after["lifecycle"] == before["lifecycle"],
            "stateSame": after["state"] == before["state"],
            "restEventsSame": after["restEvents"] == before["restEvents"],
            "wsMatchesRest": after["wsSeqs"] == [int(e.split(":")[0])
                                                 for e in after["restEvents"]],
            "noNewEvents": len(after["restEvents"]) == len(before["restEvents"]),
            "noWorkerOrRelay": not after["containers"],
            "noClaim": after["claim"] == "",
        }
        record["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    except Exception as exc:  # noqa: BLE001 — save failed drills
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        if token and record["verdict"] != "PASS" and "stopStatus" not in record:
            try:
                record["cleanupStop"] = http("POST", f"{base}/stop", token=token)[0]
            except Exception as exc:  # noqa: BLE001
                record["cleanupStopError"] = type(exc).__name__
        out.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"runId": run_id, "verdict": record["verdict"],
                          "checks": record.get("checks"), "evidence": str(out)}))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
