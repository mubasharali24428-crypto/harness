#!/usr/bin/env python3
"""W15 — prove a run's routing decision is durable and readable on the live stack.

W14 shipped opt-in model routing but kept the outcome in process memory
(``run_data["routing"]``) and a log line: after a restart, from another replica,
or from the run page, nobody could say which model an attempt was handed. W15
emits a ``model_routed`` agent event before the engine call and reads it back in
``GET /runs/{id}``.

The probe deliberately uses model ids that exist in no catalog and no rate card.
A live deployment therefore CANNOT succeed at the provider call — which is the
point: the decision must be recorded even when the attempt dies, and the drill
must not be able to spend money on a frontier model by accident.

    section4/.venv/bin/python scripts/e2e/w15_routing_drill.py

The run it creates is left in place as evidence; the only shared setting it
touches — the team's routing policy — is reset before it exits.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "briefs" / "evidence" / "eval"
IDENTITY = os.environ.get("E2E_IDENTITY_URL", "http://127.0.0.1:8001").rstrip("/")
ORCHESTRATOR = os.environ.get("E2E_ORCHESTRATOR_URL", "http://127.0.0.1:8004").rstrip("/")

sys.path[:0] = [str(ROOT / "section4/src"), str(ROOT / "contracts/src")]

spec = importlib.util.spec_from_file_location(
    "compose_journey", ROOT / "scripts" / "e2e" / "compose_journey.py"
)
cj = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cj)

MECHANICAL_MODEL = "w15-probe/mechanical-does-not-exist"
REASONING_MODEL = "w15-probe/reasoning-does-not-exist"
GOAL = "W15 routing evidence: touch two files so the plan classifies, then stop."


def http(method: str, url: str, *, token: str | None = None, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(req, timeout=30) as resp:
            raw, status = resp.read(), resp.status
    except HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw) if raw else {}
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def durable_event(run_id: str) -> dict | None:
    """Read the row straight from PostgreSQL — the store is the witness, not the
    process that wrote it. Rows are JSONB, so the payload comes back as text."""
    sql = (
        "select payload::text from agent_events "
        f"where run_id = '{run_id}' and type = 'model_routed' order by seq desc limit 1;"
    )
    proc = subprocess.run(
        ["docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness", "-tAc", sql],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def main() -> int:
    stamp = int(time.time())
    run_id = f"w15-routing-{stamp}"
    email = f"w15-routing-{stamp}@example.com"
    checks: list[dict] = []

    def expect(name: str, cond: bool, detail: object = "") -> None:
        checks.append({"check": name, "ok": bool(cond), "detail": detail})
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail if not cond else ''}", flush=True)
        if not cond:
            raise SystemExit(json.dumps({"verdict": "FAIL", "failed": name, "checks": checks}))

    token, principal = cj.authenticate("w15 routing login", email)
    team_id = principal["teamId"]

    status, before = http("GET", f"{IDENTITY}/api/v1/teams/{team_id}/settings/model-routing", token=token)
    expect("routing reads the disabled default", status == 200 and not before["routing"]["enabled"], before)

    status, put = http(
        "PUT",
        f"{IDENTITY}/api/v1/teams/{team_id}/settings/model-routing",
        token=token,
        body={
            "routing": {
                "enabled": True,
                "mechanicalModel": MECHANICAL_MODEL,
                "reasoningModel": REASONING_MODEL,
            }
        },
    )
    expect("routing can be turned on", status == 200 and put["routing"]["enabled"], put)

    status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token, body={"name": f"w15 routing {run_id}"})
    expect("project created", status == 201 and isinstance(project, dict) and project.get("id"), project)
    project_id = project["id"]

    base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
    status, plan = http("POST", f"{base}/plan", token=token, body={"goal": GOAL, "projectId": project_id})
    expect("plan proposed", status == 201 and isinstance(plan, dict) and plan.get("planId"), plan)
    plan_id = plan["planId"]

    # Predict the class the same way the deployed service does, from the plan the
    # server actually stored — not from a guess about the goal text.
    status, stored = http("GET", f"{base}/plan", token=token)
    expect("plan readable", status == 200 and isinstance(stored, dict), stored)
    from harness_orchestrator.model_routing import MECHANICAL, classify_plan

    plan_class = classify_plan(
        len(stored.get("steps") or []), len(stored.get("filesLikelyToChange") or [])
    )
    expected_model = MECHANICAL_MODEL if plan_class == MECHANICAL else REASONING_MODEL

    status, _approved = http("POST", f"{base}/plan/approve", token=token, body={"planId": plan_id})
    expect("plan approved", status == 200, _approved)
    status, started = http("POST", f"{base}/start", token=token)
    expect("execution started", status == 200, started)

    # The aggregate must answer without the executing process being asked nicely:
    # poll until the event is durable and readable.
    deadline = time.time() + 90
    aggregate: dict = {}
    while time.time() < deadline:
        status, body = http("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}", token=token)
        if isinstance(body, dict) and body.get("routing") is not None:
            aggregate = body
            break
        time.sleep(2)
    decision = aggregate.get("routing")
    expect("GET /runs/{id} carries the routing decision", decision is not None, aggregate.get("state"))
    expect("the decision says routing was enabled", decision.get("enabled") is True, decision)
    expect("the model came from the policy", decision.get("source") == "policy", decision)
    expect("the class is the one the plan has", decision.get("class") == plan_class, decision)
    expect("the recorded model is the policy's for that class", decision.get("model") == expected_model,
           {"recorded": decision.get("model"), "expected": expected_model})

    row = durable_event(run_id)
    expect("the decision is a durable agent_events row", row == decision, {"db": row, "api": decision})

    # No provider can serve these ids, so the attempt must fail — and the
    # decision must still have been recorded BEFORE the call, which is what the
    # two reads above just proved. State is captured for the evidence file.
    status, final = http("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}", token=token)
    evidence = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "milestone": "W15 routing decision is durable and readable",
        "runId": run_id,
        "teamId": team_id,
        "projectId": project_id,
        "planId": plan_id,
        "planClass": plan_class,
        "policyModels": {"mechanicalModel": MECHANICAL_MODEL, "reasoningModel": REASONING_MODEL},
        "decision": decision,
        "durableEventMatchesApi": row == decision,
        "runStateAfter": final.get("state") if isinstance(final, dict) else final,
        "note": (
            "probe model ids exist in no catalog and no rate card, so the provider call "
            "cannot succeed and no money can be spent; the decision is recorded anyway "
            "because it is emitted before the engine call"
        ),
        "checks": checks,
        "verdict": "PASS",
    }

    # Leave the team exactly as it was found: an evidence drill must not be able
    # to re-route someone else's next run.
    status, _ = http("DELETE", f"{IDENTITY}/api/v1/teams/{team_id}/settings/model-routing", token=token)
    expect("routing policy reset", status == 204, status)

    # The run, its plan and its event are LEFT IN PLACE: they are the evidence,
    # and deleting rows from a shared database is not this drill's call to make.
    # Nothing here removes anything it did not create; the routing policy above
    # was the only shared setting touched, and it was reset.

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / f"w15-routing-{stamp}.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"verdict": "PASS", "evidence": str(out.relative_to(ROOT)),
                      "decision": decision, "planClass": plan_class}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
