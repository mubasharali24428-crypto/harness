import pytest
from fastapi.testclient import TestClient
from fixtures.golden_fixtures import (
    GOLDEN_DIFF_CLEAN,
    GOLDEN_RUN_ID,
    GOLDEN_USER_ID,
)

from harness_orchestrator.api import get_principal, router
from harness_orchestrator.planner import PlanNotApprovedError
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState


def _principal():
    from harness_contracts.roles import Principal, Role

    return Principal(userId=GOLDEN_USER_ID, teamId="team-golden", role=Role.ADMIN)


def _app_with_auth():
    """Router wired with a test principal — mirrors deploy-time auth wiring."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: _principal()
    return app


def test_orchestrator_implements_protocol():
    service = OrchestratorService()
    for method_name in [
        "propose_plan",
        "approve_plan",
        "reject_plan",
        "start_execution",
        "pause_agent",
        "resume_agent",
        "stop_agent",
        "submit_human_edit",
    ]:
        method = getattr(service, method_name, None)
        assert method is not None, f"OrchestratorService missing {method_name}"
        assert callable(method)


async def test_full_orchestration_lifecycle_with_done_signal():
    events = []
    service = OrchestratorService(on_event=lambda t, p: events.append((t, p)))

    # 1. Propose Plan
    plan_id = await service.propose_plan(GOLDEN_RUN_ID)
    assert plan_id.startswith("plan-")
    status = await service.get_status(GOLDEN_RUN_ID)
    assert status["state"] == AgentState.AWAITING_PLAN_APPROVAL

    # 2. Cannot start without approval
    with pytest.raises(PlanNotApprovedError):
        await service.start_execution(GOLDEN_RUN_ID)

    # 3. Approve Plan
    await service.approve_plan(GOLDEN_RUN_ID, plan_id, GOLDEN_USER_ID)
    status = await service.get_status(GOLDEN_RUN_ID)
    assert status["planApproved"] is True

    # 4. Start execution (engine runs as a background task; state flips immediately)
    await service.start_execution(GOLDEN_RUN_ID)
    status = await service.get_status(GOLDEN_RUN_ID)
    assert status["state"] == AgentState.EXECUTING

    # 5. Submit Human Edit (implicit pause + live-task cancellation)
    await service.submit_human_edit(GOLDEN_RUN_ID, "src/auth.py", "def new_auth(): pass")
    status = await service.get_status(GOLDEN_RUN_ID)
    assert status["state"] == AgentState.PAUSED
    assert status["isHumanEditing"] is True

    # 6. Explicit Done Signal triggers Reviewer pass
    findings = await service.signal_done(GOLDEN_RUN_ID, diff_text=GOLDEN_DIFF_CLEAN)
    assert findings.passed is True
    status = await service.get_status(GOLDEN_RUN_ID)
    assert status["state"] == AgentState.EXECUTING


async def test_approve_plan_rejects_foreign_plan_id():
    """H9 regression guard: a planId from another run must never approve this run."""
    service = OrchestratorService()
    await service.propose_plan("run-a")
    await service.propose_plan("run-b")
    with pytest.raises(KeyError):
        await service.approve_plan("run-a", "plan-not-of-run-a", GOLDEN_USER_ID)


async def test_unauthenticated_requests_are_rejected():
    """F2 regression guard: no auth wiring => 503, never an open endpoint."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)  # no dependency override, no app.state wiring
    client = TestClient(app)
    res = client.post("/api/v1/orchestrator/runs/auth-probe/plan")
    assert res.status_code in (401, 403, 503)


def test_rest_api_conformance():
    app = _app_with_auth()
    client = TestClient(app)

    run_id = "api-run-909"

    # 1. POST /api/v1/orchestrator/runs/{runId}/plan
    res = client.post(
        f"/api/v1/orchestrator/runs/{run_id}/plan", json={"projectId": "p-conf"}
    )
    assert res.status_code == 201
    plan_id = res.json()["planId"]

    # 2. Try start before approval -> 403 Forbidden
    res = client.post(f"/api/v1/orchestrator/runs/{run_id}/start")
    assert res.status_code == 403

    # 3. Approve plan (approver identity comes from the session, not the body)
    res = client.post(
        f"/api/v1/orchestrator/runs/{run_id}/plan/approve",
        json={"planId": plan_id},
    )
    assert res.status_code == 200

    # 4. Start execution
    res = client.post(f"/api/v1/orchestrator/runs/{run_id}/start")
    assert res.status_code == 200

    # 5. Submit human edit
    res = client.post(
        f"/api/v1/orchestrator/runs/{run_id}/human-edit",
        json={"path": "auth.py", "content": "updated content"},
    )
    assert res.status_code == 200

    # 6. Submit done signal — findings keys are camelCase on the wire
    res = client.post(
        f"/api/v1/orchestrator/runs/{run_id}/done-signal",
        json={"diffText": GOLDEN_DIFF_CLEAN},
    )
    assert res.status_code == 200
    body = res.json()["findings"]
    assert body["passed"] is True
    assert "syntaxOk" in body and "syntax_ok" not in body
