#!/usr/bin/env python3
"""Stop one live run and verify its live neighbour remains intact."""
import json
import time
import uuid
from pathlib import Path

from w3_stop_spike import (
    IDENTITY,
    ORCHESTRATOR,
    http,
    live_pids_in,
    login,
    run_containers,
)


def main():
    tag = uuid.uuid4().hex[:12]
    ids = [f"w22-stop-{tag}-{i}" for i in range(2)]
    out = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22" / f"stop-{tag}.json"
    evidence = {"runIds": ids, "verdict": "FAIL"}
    token = None
    bases = [f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{rid}" for rid in ids]
    try:
        token = login(f"w22-stop-{tag}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w22 stop {tag}"})
        assert status == 201
        for base in bases:
            status, plan = http("POST", f"{base}/plan", token=token,
                                body={"goal": "Run `python3 -c 'import time; time.sleep(180)'` "
                                      "using bash; wait and do nothing else.",
                                      "projectId": project["id"]})
            assert status == 201
            assert http("POST", f"{base}/plan/approve", token=token,
                        body={"planId": plan["planId"]})[0] == 200
            assert http("POST", f"{base}/start", token=token)[0] == 200
        deadline = time.monotonic() + 90
        workers = []
        while time.monotonic() < deadline:
            workers = [run_containers(rid).get("worker") for rid in ids]
            if all(workers) and all(any("time.sleep(180)" in p["cmd"] for p in live_pids_in(w))
                                    for w in workers):
                break
            time.sleep(1)
        else:
            raise RuntimeError("two live children not observed")
        evidence["workersBefore"] = workers
        status, response = http("POST", f"{bases[0]}/stop", token=token)
        evidence["stopResponse"] = response
        assert status == 200
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and run_containers(ids[0]).get("worker"):
            time.sleep(0.2)
        evidence["victimAfter"] = run_containers(ids[0])
        evidence["neighbourAfter"] = run_containers(ids[1])
        evidence["neighbourChildAlive"] = any("time.sleep(180)" in p["cmd"]
                                               for p in live_pids_in(workers[1]))
        _, status_body = http("GET", f"{bases[0]}/status", token=token)
        evidence["lifecycle"] = status_body.get("lifecycle")
        assert not evidence["victimAfter"].get("worker")
        assert evidence["neighbourAfter"].get("worker") == workers[1]
        assert evidence["neighbourChildAlive"]
        assert evidence["lifecycle"]["phase"] == "stopped"
        assert evidence["lifecycle"]["cancelConfirmed"] is True
        assert evidence["lifecycle"]["mutationPossible"] is False
        evidence["verdict"] = "PASS"
    except Exception as exc:  # noqa: BLE001 — retain failed test without credential text
        evidence["errorType"] = type(exc).__name__
    finally:
        if token:
            for base in bases:
                try:
                    http("POST", f"{base}/stop", token=token)
                except Exception as exc:  # noqa: BLE001
                    evidence.setdefault("cleanupErrors", []).append(type(exc).__name__)
                    evidence["verdict"] = "FAIL"
        out.write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps({"verdict": evidence["verdict"], "evidence": str(out)}))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
