"""W1-C1 plan-gate conformance goldens (M11-W1 glue lane).

Pins the verbatim W1-C1↔W1-C2 pair contract the same way the walk above pins
the run-scoped API: exact status codes, exact response key sets, camelCase
field names mirrored by the S7 TS client, and ``error_body`` envelopes from
``harness_contracts.api``. Covered routes:

- POST /api/v1/orchestrator/plans            → 201 {planId, plan}
- POST /api/v1/orchestrator/plans/{id}/approve → 200 {runId} (idempotent)
- POST /api/v1/orchestrator/plans/{id}/reject  → 202 audited rejection
- POST /api/v1/orchestrator/runs/{runId}/start → approval actually gates it

Golden ids (run/plan) are minted server-side per request, so the goldens here
assert *shapes* (key sets, prefixes, echo fields), never literal uuid values —
mirroring the fixture philosophy in tests/fixtures/golden_fixtures.py, whose
constants these walks consume.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fixtures.golden_fixtures import GOLDEN_GOAL, GOLDEN_USER_ID
from harness_contracts.roles import Principal, Role

from harness_orchestrator.plan_gate import (
    _plan_gate_principal,
    configure_plan_gate,
    router,
)
from harness_orchestrator.service import OrchestratorService

PLAN_GATE_PLAN_KEYS = {
    "goal",
    "filesLikelyToChange",
    "steps",
    "tests",
    "risks",
    "estimatedCostUsd",
    "forbiddenPaths",
    "expectedResult",
}


def _gate_principal(role: Role) -> Principal:
    return Principal(userId=GOLDEN_USER_ID, teamId="team-golden", role=role)


def _plan_gate_client(service: OrchestratorService, role: Role) -> TestClient:
    """Fresh app per call: plan-gate router + principal override (no bearer)."""
    configure_plan_gate(service)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_plan_gate_principal] = lambda: _gate_principal(role)
    return TestClient(app)


@pytest.fixture()
def gate_service() -> OrchestratorService:
    """Bare in-memory orchestrator (no DB): persistence guards short-circuit."""
    return OrchestratorService()


def test_plan_gate_golden_walk_propose_approve_start(gate_service):
    """The contracted happy walk: 201 propose → 200 approve → 200 start."""
    client = _plan_gate_client(gate_service, Role.ADMIN)

    # 1. POST /plans {goal} → 201 {planId, plan} — exact wire shapes
    res = client.post("/api/v1/orchestrator/plans", json={"goal": GOLDEN_GOAL})
    assert res.status_code == 201
    proposed = res.json()
    assert set(proposed.keys()) == {"planId", "plan"}
    assert isinstance(proposed["planId"], str) and proposed["planId"].startswith("plan-")
    plan = proposed["plan"]
    assert set(plan.keys()) == PLAN_GATE_PLAN_KEYS  # camelCase TS-mirror fields
    assert plan["goal"] == GOLDEN_GOAL
    assert plan["expectedResult"]  # non-empty contract field

    # 2. POST /plans/{planId}/approve → 200 {runId} — approver from session
    res = client.post(f"/api/v1/orchestrator/plans/{proposed['planId']}/approve")
    assert res.status_code == 200
    approved = res.json()
    assert set(approved.keys()) == {"runId"}
    assert isinstance(approved["runId"], str) and approved["runId"]

    # 3. Idempotent re-approve → same 200 shape, same runId
    res = client.post(f"/api/v1/orchestrator/plans/{proposed['planId']}/approve")
    assert res.status_code == 200
    assert res.json() == approved

    # 4. Approval actually gates execution: start → 200, state executing
    res = client.post(f"/api/v1/orchestrator/runs/{approved['runId']}/start", json={})
    assert res.status_code == 200
    assert res.json()["state"] == "executing"


def test_plan_gate_reject_golden(gate_service):
    """Reject → 202 audited envelope; the rejected plan can never start after."""
    client = _plan_gate_client(gate_service, Role.ADMIN)
    plan_id = client.post(
        "/api/v1/orchestrator/plans", json={"goal": GOLDEN_GOAL}
    ).json()["planId"]

    res = client.post(f"/api/v1/orchestrator/plans/{plan_id}/reject")
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "rejected"
    assert body["audited"] is True
    assert body["planId"] == plan_id
    assert isinstance(body["runId"], str) and body["runId"]

    # D1's law observable through the gate: rejected ⇒ never executable
    start = client.post(f"/api/v1/orchestrator/runs/{body['runId']}/start", json={})
    assert start.status_code == 409
    assert start.json()["detail"]["error"]["code"] == "PLAN_NOT_APPROVED"


def test_plan_gate_role_gates_and_error_envelopes_are_frozen(gate_service):
    """viewer/guest get 403 ROLE_DENIED error_body envelopes; nothing leaks."""
    # propose behind a read-only role BEFORE anything is minted:
    # 403 envelope AND no run created behind it
    viewer_client = _plan_gate_client(gate_service, Role.VIEWER)
    denied = viewer_client.post("/api/v1/orchestrator/plans", json={"goal": GOLDEN_GOAL})
    assert denied.status_code == 403
    detail = denied.json()["detail"]
    assert detail["error"]["code"] == "ROLE_DENIED"
    assert detail["error"]["message"]
    assert gate_service.runs == {}

    admin_client = _plan_gate_client(gate_service, Role.ADMIN)
    guest_client = _plan_gate_client(gate_service, Role.GUEST)
    seeded = admin_client.post(
        "/api/v1/orchestrator/plans", json={"goal": GOLDEN_GOAL}
    ).json()["planId"]

    # decide routes gated identically
    for path in (
        f"/api/v1/orchestrator/plans/{seeded}/approve",
        f"/api/v1/orchestrator/plans/{seeded}/reject",
    ):
        for client in (viewer_client, guest_client):
            res = client.post(path)
            assert res.status_code == 403, path
            assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"

    # unknown plan id: 404 with the contracted envelope code. M17-B4
    # (MAC2-F2): unknown and FOREIGN plans deliberately share ONE plain
    # NOT_FOUND code — the M13-G5a no-oracle law outranks naming the
    # existence class, so a foreign team cannot confirm plan ids exist.
    missing = admin_client.post("/api/v1/orchestrator/plans/plan-does-not-exist/reject")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "NOT_FOUND"
