#!/usr/bin/env python3
"""W23 exit: capture the readiness report on a stack set up from a clean checkout.

Run after docs/quickstart.md step 1 on a fresh stack. It does the quickstart's
step 2 (OTP login) and step 3 (seed a run), then `harness doctor --json --capture`
on the seeded project, and records the report as evidence. ``--label`` names
the stage (for example ``bare`` straight from .env.example, ``configured`` after
a provider and worker were set).

    section4/.venv/bin/python scripts/e2e/w23_fresh_setup_capture.py --label bare
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from w22_stop_drill import IDENTITY, ORCHESTRATOR, http, login

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    drill_id = f"w23-fresh-{args.label}-{uuid.uuid4().hex[:8]}"
    out = REPO / "docs/briefs/evidence/w23" / f"{drill_id}.json"
    token = login(f"{drill_id}@example.com")
    status, seed = http("POST", f"{ORCHESTRATOR}/api/v1/orchestrator/demo/seed", token=token, body={})
    assert status in (200, 201), f"seed {status}: {seed}"
    capture = Path(tempfile.mkdtemp()) / "doctor.json"
    env = {**os.environ, "HARNESS_TOKEN": token, "HARNESS_CONFIG_DIR": tempfile.mkdtemp(),
           "PYTHONPATH": str(REPO / "clients/python")}
    proc = subprocess.run(
        [sys.executable, "-m", "harness_sdk.cli.main", "doctor",
         "--project", seed["projectId"], "--json", "--capture", str(capture),
         "--identity-url", IDENTITY, "--orchestrator-url", ORCHESTRATOR],
        env=env, capture_output=True, text=True, timeout=120, check=False)
    record = {
        "drillId": drill_id, "label": args.label, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git": subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=False).stdout.strip(),
        "seed": {k: seed.get(k) for k in ("projectId", "runId")},
        "doctorExitCode": proc.returncode,
        "doctor": json.loads(capture.read_text()) if capture.exists() else None,
        "stderr": proc.stderr[-2000:],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n")
    for c in (record["doctor"] or {}).get("checks", []):
        print(f"{c['status']:>7}  {c['check']:<11} {c['reason']}")
    print(json.dumps({"drillId": drill_id, "doctorExitCode": proc.returncode,
                      "evidence": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
