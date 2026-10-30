#!/usr/bin/env python3
"""Inject a per-run model relay egress outage; never disconnect shared services."""
import json
import time
import uuid
from pathlib import Path

from w3_stop_spike import IDENTITY, ORCHESTRATOR, http, login, sh


def main():
    rid = f"w22-outage-{uuid.uuid4().hex[:12]}"
    data = {"runId": rid, "verdict": "FAIL"}
    out = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22" / f"{rid}.json"
    token = None
    relay = None
    base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{rid}"
    try:
        token = login(f"{rid}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": rid})
        assert status == 201
        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": "Create a file named outage_test.txt with text hello.",
                                  "projectId": project["id"]})
        assert status == 201
        assert http("POST", f"{base}/plan/approve", token=token,
                    body={"planId": plan["planId"]})[0] == 200
        assert http("POST", f"{base}/start", token=token)[0] == 200
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            rows = sh("docker", "network", "ls", "--format",
                      '{{.Name}} {{.Label "harness-run"}}', check=False).stdout.splitlines()
            networks = [line.split()[0] for line in rows if line.endswith(" " + rid)]
            if networks:
                name = networks[0].removesuffix("-net") + "-relay"
                if sh("docker", "inspect", name, check=False).returncode == 0:
                    relay = name
                    break
            time.sleep(0.1)
        assert relay, "relay absent"
        data["relay"] = relay
        proc = sh("docker", "network", "disconnect", "bridge", relay, check=False)
        data["disconnectExit"] = proc.returncode
        assert proc.returncode == 0
        deadline = time.monotonic() + 160
        while time.monotonic() < deadline:
            _, body = http("GET", f"{base}/status", token=token)
            state = body.get("state") if isinstance(body, dict) else None
            if state in {"blocked", "done", "awaiting_done_signal", "failed"}:
                break
            time.sleep(2)
        data["state"] = state
        _, result = http("GET", f"{base}/engine", token=token)
        data["engineStatus"] = result.get("status") if isinstance(result, dict) else None
        data["engineName"] = result.get("engine") if isinstance(result, dict) else None
        data["verdict"] = "PASS" if state == "blocked" else "FAIL"
    except Exception as exc:  # noqa: BLE001 — retain failed evidence
        data["errorType"] = type(exc).__name__
    finally:
        if relay:
            sh("docker", "network", "connect", "bridge", relay, check=False)
        if token and data.get("state") not in {"blocked", "done", "awaiting_done_signal", "failed"}:
            http("POST", f"{base}/stop", token=token)
        out.write_text(json.dumps(data, indent=2) + "\n")
        print(json.dumps({"verdict": data["verdict"], "evidence": str(out)}))
    return 0 if data["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
