#!/usr/bin/env python3
"""Observe claim heartbeats during one long confined child, then stop it."""
import json
import time
import uuid
from pathlib import Path

from w3_stop_spike import ORCHESTRATOR, http, live_pids_in, login, run_containers, sh


def main():
    run_id = f"w22-heartbeat-{uuid.uuid4().hex[:12]}"
    record = {"runId": run_id, "samples": [], "verdict": "FAIL"}
    out = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22" / f"{run_id}.json"
    token = None
    base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
    try:
        token = login(f"{run_id}@example.com")
        status, project = http("POST", "http://127.0.0.1:8001/api/v1/projects", token=token,
                               body={"name": run_id})
        assert status == 201
        goal = ("Run exactly `python3 -c 'import time; time.sleep(540)'` using bash. "
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
            if worker and any("time.sleep(540)" in p["cmd"] for p in live_pids_in(worker)):
                break
            time.sleep(1)
        else:
            raise RuntimeError("child was not observed")
        record["worker"] = worker
        start = time.monotonic()
        while time.monotonic() - start < 390:
            sql = ("SELECT state, worker_id, heartbeat_at, "
                   "EXTRACT(EPOCH FROM (now()-heartbeat_at::timestamptz))::int "
                   f"FROM run_claims WHERE run_id='{run_id}'")
            claim = sh("docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d",
                       "harness", "-At", "-c", sql, check=False).stdout.strip()
            live = bool(run_containers(run_id).get("worker"))
            record["samples"].append({"elapsed": round(time.monotonic()-start, 1),
                                      "claim": claim, "workerLive": live})
            if not live or not claim.startswith("running|"):
                raise RuntimeError("worker or claim lost")
            time.sleep(10)
        record["verdict"] = "PASS"
    except Exception as exc:  # noqa: BLE001 — save failed and incomplete drills
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if token:
            try:
                record["stopStatus"] = http("POST", f"{base}/stop", token=token)[0]
            except Exception as exc:  # noqa: BLE001
                record["stopError"] = type(exc).__name__
        out.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"runId": run_id, "verdict": record["verdict"],
                          "samples": len(record["samples"]), "evidence": str(out)}))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
