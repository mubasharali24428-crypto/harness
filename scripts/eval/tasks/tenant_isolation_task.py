#!/usr/bin/env python3
"""Deterministic tenant-isolation matrix: another team learns nothing about a run.

No model. Team A owns a project and a run whose plan carries a unique marker. For
every ``GET /runs/{id}...`` route in the recorded orchestrator spec (not a
hand-picked list), team B asks about A's run and about a run that does not exist.
The independent checks, taken from the responses and never from a message:

1. **No existence oracle.** B's answer for A's run has the same status and error
   code as B's answer for a nonexistent run.
2. **No leak.** A's marker appears in none of B's bodies.
3. **Not vacuous.** Team A itself gets 200 for the plan, and the marker is in it.
4. **Anonymous is refused** on the same routes (401/403, never 200).
5. **Projects.** B's read of A's project matches B's read of a random id.

    python3 scripts/eval/tasks/tenant_isolation_task.py            # needs the stack
    python3 scripts/eval/tasks/tenant_isolation_task.py --selftest # offline
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
SPEC = REPO / "section4" / "openapi.json"
P = "/api/v1/orchestrator"
FAKE = {"artifactId": "art-none", "n": "1", "commentId": "c-none"}


def route_templates(spec: dict) -> list[str]:
    """GET paths addressed by a run id, from the recorded spec."""
    return sorted(p for p, ops in spec.get("paths", {}).items()
                  if "get" in ops and re.match(rf"^{re.escape(P)}/runs/\{{(runId|scopeId)\}}", p))


def fill(template: str, run_id: str) -> str:
    out = re.sub(r"\{(runId|scopeId)\}", run_id, template)
    return re.sub(r"\{(\w+)\}", lambda m: FAKE.get(m.group(1), "x-none"), out)


def code_of(body: object) -> str:
    detail = body.get("detail") if isinstance(body, dict) else None
    inner = detail if isinstance(detail, dict) else body
    err = inner.get("error") if isinstance(inner, dict) else None
    return str(err.get("code") or "") if isinstance(err, dict) else ""


def indistinguishable(foreign: tuple[int, object], missing: tuple[int, object]) -> bool:
    return foreign[0] == missing[0] and code_of(foreign[1]) == code_of(missing[1])


def leaks(marker: str, *bodies: object) -> bool:
    return any(marker in json.dumps(b, default=str) for b in bodies)


def selftest() -> int:
    spec = {"paths": {f"{P}/runs/{{runId}}/plan": {"get": {}},
                      f"{P}/runs/{{runId}}/artifacts/{{artifactId}}": {"get": {}},
                      f"{P}/runs/{{scopeId}}/comments": {"get": {}, "post": {}},
                      f"{P}/runs/{{runId}}/join": {"post": {}},
                      f"{P}/health": {"get": {}}}}
    t = route_templates(spec)
    assert t == [f"{P}/runs/{{runId}}/artifacts/{{artifactId}}", f"{P}/runs/{{runId}}/plan",
                 f"{P}/runs/{{scopeId}}/comments"], t
    assert fill(t[0], "r1") == f"{P}/runs/r1/artifacts/art-none"
    nf = (404, {"detail": {"error": {"code": "RUN_NOT_FOUND"}}})
    assert indistinguishable(nf, (404, {"error": {"code": "RUN_NOT_FOUND"}}))
    assert not indistinguishable((403, {"error": {"code": "FORBIDDEN"}}), nf)  # an oracle
    assert not indistinguishable((200, {}), nf)
    assert leaks("MARK", {"goal": "xx MARK"}) and not leaks("MARK", {"goal": "xx"})
    print(json.dumps({"verdict": "PASS", "selftest": True}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    if ap.parse_args().selftest:
        return selftest()

    spec_ = importlib.util.spec_from_file_location(
        "tpl", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py")
    tpl = importlib.util.module_from_spec(spec_)
    assert spec_ and spec_.loader
    spec_.loader.exec_module(tpl)

    stamp = int(time.time())
    marker = f"ISOLATION-MARKER-{uuid.uuid4().hex}"
    started = time.monotonic()
    token_a = tpl.login(f"iso-a-{stamp}@example.com")
    token_b = tpl.login(f"iso-b-{stamp}@example.com")

    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token_a,
                               body={"name": f"iso {stamp}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")
    run_id = f"iso-run-{stamp}"
    missing_id = f"iso-missing-{stamp}"
    status, plan = tpl.http("POST", f"{tpl.ORCHESTRATOR}{P}/runs/{run_id}/plan", token=token_a,
                            body={"goal": marker, "projectId": project["id"]})
    if status != 201:
        raise SystemExit(f"propose plan failed: {status} {plan!r}")

    def get(path: str, token: str | None):
        return tpl.http("GET", f"{tpl.ORCHESTRATOR}{path}", token=token)

    templates = route_templates(json.loads(SPEC.read_text(encoding="utf-8")))
    rows, failures = [], []
    for tmpl in templates:
        foreign = get(fill(tmpl, run_id), token_b)
        missing = get(fill(tmpl, missing_id), token_b)
        anon = get(fill(tmpl, run_id), None)
        row = {"route": tmpl, "foreign": foreign[0], "missing": missing[0],
               "foreignCode": code_of(foreign[1]) or None, "anonymous": anon[0],
               "indistinguishable": indistinguishable(foreign, missing),
               "leak": leaks(marker, foreign[1], missing[1], anon[1]),
               "anonymousRefused": anon[0] in (401, 403)}
        rows.append(row)
        if not (row["indistinguishable"] and not row["leak"] and row["anonymousRefused"]):
            failures.append(tmpl)

    own_status, own_plan = get(f"{P}/runs/{run_id}/plan", token_a)
    p_foreign = tpl.http("GET", f"{tpl.IDENTITY}/api/v1/projects/{project['id']}", token=token_b)
    p_missing = tpl.http("GET", f"{tpl.IDENTITY}/api/v1/projects/{uuid.uuid4()}", token=token_b)
    p_own = tpl.http("GET", f"{tpl.IDENTITY}/api/v1/projects/{project['id']}", token=token_a)

    checks = {
        "routesDerivedFromSpec": len(templates) >= 20,
        "ownerReadsOwnPlan": own_status == 200 and leaks(marker, own_plan),
        "ownerReadsOwnProject": p_own[0] == 200,
        "noExistenceOracleOnRuns": all(r["indistinguishable"] for r in rows),
        "noMarkerLeak": not any(r["leak"] for r in rows),
        "anonymousRefused": all(r["anonymousRefused"] for r in rows),
        "projectNoOracle": indistinguishable(p_foreign, p_missing) and p_foreign[0] != 200,
        "projectNoLeak": not leaks(marker, p_foreign[1]) and not leaks(project["id"], p_foreign[1]),
    }
    evidence = {
        "schema": 1, "kind": "tenant-isolation-api",
        "takenAt": datetime.now(UTC).isoformat(), "runId": run_id,
        "model": "none (deterministic API matrix)",
        "durationSeconds": round(time.monotonic() - started, 2),
        "routesChecked": len(templates), "rows": rows, "checks": checks,
        "projectStatuses": {"foreign": p_foreign[0], "missing": p_missing[0], "own": p_own[0]},
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "failedChecks": [k for k, v in checks.items() if not v], "failedRoutes": failures,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"tenant-isolation-api-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"], "failed": evidence["failedChecks"],
                      "failedRoutes": failures, "routes": len(templates),
                      "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
