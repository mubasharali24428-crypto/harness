#!/usr/bin/env python3
"""W23: the readiness report must catch a stale worker daemon, not a person.

Against the deployed stack: take a baseline report, then make the daemon stale
twice and expect the report to name it each time:

1. **token** — recreate worker-daemon with a token the orchestrator does not
   hold (the W20 outage). Expect ``worker: fail WORKER_UNAUTHORIZED``.
2. **image** — recreate worker-daemon on an image from before ``/version``.
   Expect ``revision: fail WORKER_PREDATES_READINESS``.

After each fault the daemon is recreated on the deployed tag and the report
must recover. ``/ready`` is sampled during each fault to record that it stays
green, which is why this check exists. The daemon is always restored, pass or
fail. No model is called.

    python3 w23_readiness_drill.py --tag w23ready1 --stale-tag s1coherent
"""
import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from w3_stop_spike import IDENTITY, ORCHESTRATOR, http, login, sh

REPO = Path(__file__).resolve().parents[2]
DAEMON = "harness-worker-daemon-1"
STALE_TOKEN = "w23-drill-stale-token"
WAIT_SECONDS = 60


def recreate_daemon(tag: str, token: str | None = None) -> None:
    env = {**os.environ, "HARNESS_IMAGE_TAG": tag}
    if token is not None:
        env["HARNESS_WORKER_DAEMON_TOKEN"] = token
    subprocess.run(
        ["docker", "compose", "--env-file", str(REPO / ".env"), "-f",
         str(REPO / "deploy/docker-compose.yml"), "-p", "harness", "--profile", "worker",
         "up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "worker-daemon"],
        env=env, check=True, capture_output=True, text=True,
    )


def readiness(token: str, project_id: str) -> dict:
    status, body = http("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/readiness?projectId={project_id}",
                        token=token)
    assert status == 200, f"readiness {status}: {body}"
    return body


def entry(body: dict, check: str) -> dict:
    return next(c for c in body["checks"] if c["check"] == check)


def wait_for(token: str, project_id: str, predicate) -> tuple[bool, list[dict]]:
    seen, deadline = [], time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        body = readiness(token, project_id)
        seen.append({"t": round(time.monotonic(), 1),
                     "worker": entry(body, "worker")["reason"],
                     "revision": entry(body, "revision")["reason"]})
        if predicate(body):
            return True, seen
        time.sleep(2)
    return False, seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="the deployed HARNESS_IMAGE_TAG")
    ap.add_argument("--stale-tag", required=True, help="a tag whose daemon predates /version")
    args = ap.parse_args()
    drill_id = f"w23-ready-{uuid.uuid4().hex[:12]}"
    out = REPO / "docs/briefs/evidence/w23" / f"{drill_id}.json"
    record: dict = {"drillId": drill_id, "tag": args.tag, "staleTag": args.stale_tag,
                    "verdict": "FAIL", "checks": {}}
    checks = record["checks"]
    try:
        record["git"] = sh("git", "-C", str(REPO), "rev-parse", "HEAD").stdout.strip()
        record["daemonImage"] = sh("docker", "inspect", "-f", "{{.Config.Image}} {{.Image}}",
                                   DAEMON).stdout.strip()
        token = login(f"{drill_id}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": drill_id})
        assert status == 201, f"create project {status}"
        pid = project["id"]

        base = readiness(token, pid)
        record["baseline"] = base
        checks["baselineWorkerAndRevisionOk"] = (
            entry(base, "worker")["status"] == "ok" and entry(base, "revision")["status"] == "ok")

        recreate_daemon(args.tag, STALE_TOKEN)
        ok, seen = wait_for(token, pid,
                            lambda b: entry(b, "worker")["reason"] == "WORKER_UNAUTHORIZED")
        record["tokenFault"] = {"observations": seen,
                                "readyDuringFault": http("GET", f"{ORCHESTRATOR}/ready")[0]}
        checks["tokenFaultNamed"] = ok
        checks["tokenNotEchoed"] = STALE_TOKEN not in json.dumps(seen) and STALE_TOKEN not in \
            json.dumps(readiness(token, pid))

        recreate_daemon(args.tag)
        checks["recoveredAfterToken"], record["tokenRestore"] = wait_for(
            token, pid, lambda b: entry(b, "revision")["status"] == "ok")

        recreate_daemon(args.stale_tag)
        record["staleDaemonImage"] = sh("docker", "inspect", "-f", "{{.Config.Image}}",
                                        DAEMON).stdout.strip()
        ok, seen = wait_for(
            token, pid, lambda b: entry(b, "revision")["reason"] == "WORKER_PREDATES_READINESS")
        record["imageFault"] = {"observations": seen,
                                "readyDuringFault": http("GET", f"{ORCHESTRATOR}/ready")[0]}
        checks["imageFaultNamed"] = ok
        checks["imageFaultComponent"] = entry(readiness(token, pid), "revision")["component"] \
            == "worker-daemon"

        recreate_daemon(args.tag)
        checks["recoveredAfterImage"], record["imageRestore"] = wait_for(
            token, pid, lambda b: entry(b, "revision")["status"] == "ok")
    except Exception as exc:  # noqa: BLE001 - the failure is evidence; restore below
        record["error"] = repr(exc)
    finally:
        # Never leave the shared stack on a stale daemon, pass or fail.
        if sh("docker", "inspect", "-f", "{{.Config.Image}}", DAEMON,
              check=False).stdout.strip() != f"harness-worker-daemon:v{args.tag}":
            recreate_daemon(args.tag)
        record["daemonImageAfter"] = sh("docker", "inspect", "-f", "{{.Config.Image}}",
                                        DAEMON, check=False).stdout.strip()
        record["verdict"] = "PASS" if checks and all(checks.values()) and \
            "error" not in record else "FAIL"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"drillId": drill_id, "verdict": record["verdict"], "checks": checks,
                      "evidence": str(out)}))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
