#!/usr/bin/env python3
"""W17 Stage-2: generate REAL 5xx responses and prove the availability SLI sees them.

Stage 2's failure-outcome list ends with "real 5xx generation: open". The availability
SLI (docs/runbooks/slo-and-capacity.md) is only trustworthy if a genuine server failure
moves its numerator, and nothing it should exclude does. Unit tests feed the collector
synthetic status codes; this drill makes the deployed service fail the way production
would -- its database goes away -- and reads the answer off the deployed ``/metrics``.

Method (isolated ``loadtest`` stack only; no provider call):

1. log in and create a project while healthy (so only the later request can fail),
2. scrape the orchestrator ``/metrics``, make healthy requests, scrape again (ok moves),
3. ``docker stop`` the stack's database,
4. while it is down, send N authenticated requests that need the DB (genuine 500s), one
   unauthenticated request (a caller 401), and probe ``/ready`` (503, a synthetic probe),
5. scrape: ``harness_eligible_requests_total{outcome="error"}`` must rise by exactly N,
   ``http_requests_total{status="500"}`` by N, and the 401 and the /ready 503 must NOT
   count -- the exclusions the SLO document specifies,
6. ``docker start`` the database, wait for /ready, send a request that must succeed
   (recovery), and compute ok/(ok+error) over the run.

What this proves: the failure path produces a real 5xx, the numerator and denominator
classify it correctly, and the service recovers without a restart. What it does not
prove: that the 5% ``HarnessHigh5xxRate`` alert pages (promtool covers the rule; this
isolated stack has no Prometheus), or any availability *objective* over a window.

The database container is always restarted, even when an assertion fails.

    scripts/loadtest/run_probe.sh smoke      # PROBE_PROJECT=<p> PROBE_NO_BUILD=1 ...
    python3 scripts/e2e/w17_real_5xx_drill.py \\
        --identity http://127.0.0.1:18002 --orchestrator http://127.0.0.1:18007 \\
        --identity-service identity-loadtest --db-container <p>-db-loadtest-1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from w17_event_freshness_drill import (
    http,
    login,
)

OK = 'harness_eligible_requests_total{outcome="ok"}'
ERR = 'harness_eligible_requests_total{outcome="error"}'
PLAN_500 = 'http_requests_total{method="POST",path="/api/v1/orchestrator/runs/{runId}/plan",status="500"}'


def scrape(orchestrator: str) -> dict[str, float]:
    status, body = http("GET", f"{orchestrator}/metrics")
    assert status == 200 and isinstance(body, str), f"/metrics status {status}"
    out: dict[str, float] = {}
    for line in body.splitlines():
        name, _, value = line.rpartition(" ")
        if name in (OK, ERR) or (name.startswith("http_requests_total") and 'status="5' in name):
            out[name] = float(value)
    return out


def plan(orchestrator: str, token: str | None, project_id: str, tag: str, i: int):
    return http("POST", f"{orchestrator}/api/v1/orchestrator/runs/{tag}-{i}/plan", token=token,
                body={"goal": "real 5xx drill", "projectId": project_id}, timeout=60)


def wait_ready(orchestrator: str, seconds: float = 90.0) -> float:
    start = time.time()
    while time.time() - start < seconds:
        status, _ = http("GET", f"{orchestrator}/ready", timeout=5)
        if status == 200:
            return time.time() - start
        time.sleep(1)
    raise AssertionError(f"orchestrator /ready did not return 200 within {seconds:g}s of db start")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", required=True)
    ap.add_argument("--orchestrator", required=True)
    ap.add_argument("--identity-service", default="identity-loadtest")
    ap.add_argument("--db-container", required=True, help="the ISOLATED stack's database container")
    ap.add_argument("--errors", type=int, default=5, help="authenticated requests sent while down")
    args = ap.parse_args()
    if "loadtest" not in args.db_container:
        raise SystemExit("refusing: --db-container must be an isolated loadtest database")
    orch, n = args.orchestrator, args.errors
    tag = uuid.uuid4().hex[:12]
    evidence: dict = {"tag": tag, "orchestrator": orch, "dbContainer": args.db_container,
                      "injectedErrors": n, "checks": {}, "verdict": "FAIL"}
    out_dir = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w17"
    out_dir.mkdir(parents=True, exist_ok=True)
    db_stopped = False
    try:
        token = login(f"w17-5xx-{tag}@example.com", args.identity, args.identity_service)
        status, project = http("POST", f"{args.identity}/api/v1/projects", token=token,
                               body={"name": f"w17-5xx-{tag}"})
        assert status == 201 and isinstance(project, dict), f"project status {status}"
        pid = project["id"]

        # Healthy phase: ok must move, error must not.
        s0 = scrape(orch)
        for i in range(3):
            status, _ = plan(orch, token, pid, f"{tag}-ok", i)
            assert status == 201, f"healthy plan status {status}"
        s1 = scrape(orch)
        assert s1.get(OK, 0) - s0.get(OK, 0) >= 3, "healthy requests did not move the ok counter"
        assert s1.get(ERR, 0) == s0.get(ERR, 0), "error counter moved during the healthy phase"

        # Fault phase: the database goes away.
        subprocess.run(["docker", "stop", args.db_container], check=True, capture_output=True)
        db_stopped = True
        statuses = [plan(orch, token, pid, f"{tag}-down", i)[0] for i in range(n)]
        unauth_status, _ = plan(orch, None, pid, f"{tag}-anon", 0)
        ready_status, _ = http("GET", f"{orch}/ready", timeout=30)
        s2 = scrape(orch)
        evidence["duringOutage"] = {"authenticatedStatuses": statuses, "unauthenticatedStatus": unauth_status,
                                    "readyStatus": ready_status}
        assert all(s >= 500 for s in statuses), f"outage did not produce genuine 5xx: {statuses}"
        assert 400 <= unauth_status < 500, f"unauthenticated request was {unauth_status}, expected a 4xx"
        assert ready_status == 503, f"/ready during outage was {ready_status}, expected 503"
        d_err = s2.get(ERR, 0) - s1.get(ERR, 0)
        d_500 = s2.get(PLAN_500, 0) - s1.get(PLAN_500, 0)
        assert d_err == n, f"eligible error counter moved by {d_err:g}, expected exactly {n} (401 and /ready 503 must be excluded)"
        assert d_500 == n, f"http_requests_total 500 moved by {d_500:g}, expected {n}"

        # Recovery without a restart of the orchestrator.
        subprocess.run(["docker", "start", args.db_container], check=True, capture_output=True)
        db_stopped = False
        evidence["recoverySeconds"] = round(wait_ready(orch), 1)
        status, _ = plan(orch, token, pid, f"{tag}-rec", 0)
        assert status == 201, f"post-recovery plan status {status}"
        s3 = scrape(orch)
        ok_total = s3.get(OK, 0) - s0.get(OK, 0)
        err_total = s3.get(ERR, 0) - s0.get(ERR, 0)
        evidence["sli"] = {"ok": ok_total, "error": err_total,
                           "availability": round(ok_total / (ok_total + err_total), 4)}
        evidence["checks"] = {"healthyMovesOkOnly": True, "outageProducesReal5xx": True,
                              "errorCounterExact": True, "callerAndProbeExcluded": True,
                              "recoversWithoutRestart": True}
        evidence["verdict"] = "PASS"
    except Exception as exc:  # every failure must land in evidence
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if db_stopped:
            subprocess.run(["docker", "start", args.db_container], check=False, capture_output=True)
        out = out_dir / f"w17-real-5xx-{tag}.json"
        out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"verdict": evidence["verdict"], "evidence": str(out), "sli": evidence.get("sli")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
