#!/usr/bin/env python3
"""Freeze the orchestrator past the heartbeat timeout mid-run, then resume it.

Models the 2026-10-30 incident: the Mac slept for about 7 minutes and the reaper
reaped a live run 2 s after wake. Pass means the resumed orchestrator keeps its
own claim (no heartbeat_lost) and the run's child is still alive.
"""
import json
import time
import uuid
from pathlib import Path

from w3_stop_spike import ORCHESTRATOR, http, live_pids_in, login, run_containers, sh

PAUSE_SECONDS = 120  # > 90 s HEARTBEAT_TIMEOUT_SECONDS


def psql(sql):
    return sh("docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness",
              "-At", "-c", sql, check=False).stdout.strip()


def snapshot(run_id):
    return {
        "claim": psql("SELECT state, worker_id, heartbeat_at, EXTRACT(EPOCH FROM "
                      "(now()-heartbeat_at::timestamptz))::int FROM run_claims "
                      f"WHERE run_id='{run_id}'"),
        "runState": psql(f"SELECT state FROM agent_runs WHERE id='{run_id}'"),
        "workerLive": bool(run_containers(run_id).get("worker")),
        "events": psql("SELECT string_agg(type || coalesce(':' || (payload->>'reason'), ''), ',' "
                       f"ORDER BY seq) FROM agent_events WHERE run_id='{run_id}'"),
    }


def main():
    run_id = f"w22-suspend-{uuid.uuid4().hex[:12]}"
    record = {"runId": run_id, "pauseSeconds": PAUSE_SECONDS, "verdict": "FAIL"}
    out = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22" / f"{run_id}.json"
    token = None
    paused = False
    base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
    try:
        record["image"] = sh("docker", "inspect", "-f", "{{.Config.Image}} {{.Image}}",
                             "harness-orchestrator-1").stdout.strip()
        token = login(f"{run_id}@example.com")
        status, project = http("POST", "http://127.0.0.1:8001/api/v1/projects", token=token,
                               body={"name": run_id})
        assert status == 201
        goal = ("Run exactly `python3 -c 'import time; time.sleep(200)'` using bash. "
                "Wait for it to finish; do nothing else.")
        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": goal, "projectId": project["id"]})
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
        record["worker"] = worker
        time.sleep(5)
        record["before"] = snapshot(run_id)
        sh("docker", "pause", "harness-orchestrator-1")
        paused = True
        time.sleep(PAUSE_SECONDS)
        record["duringPause"] = snapshot(run_id)
        sh("docker", "unpause", "harness-orchestrator-1")
        paused = False
        time.sleep(30)  # three heartbeat intervals and many reaper cycles
        after = snapshot(run_id)
        after["childLive"] = any("time.sleep(200)" in p["cmd"]
                                 for p in live_pids_in(worker)) if after["workerLive"] else False
        record["after"] = after
        reaped = "heartbeat_lost" in (after["events"] or "")
        kept = after["claim"].startswith("running|") and after["childLive"]
        record["verdict"] = "PASS" if kept and not reaped else "FAIL"
    except Exception as exc:  # noqa: BLE001 — save failed and incomplete drills
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if paused:
            sh("docker", "unpause", "harness-orchestrator-1", check=False)
        if token:
            try:
                record["stopStatus"] = http("POST", f"{base}/stop", token=token)[0]
            except Exception as exc:  # noqa: BLE001
                record["stopError"] = type(exc).__name__
        out.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"runId": run_id, "verdict": record["verdict"],
                          "evidence": str(out)}))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
