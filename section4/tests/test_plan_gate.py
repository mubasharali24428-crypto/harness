"""W1-C1 tests — plan-gate orchestrator API (pair contract W1-C1↔W1-C2).

Covers the brief's required matrix:
- happy path: propose → approve → startRun walk (201/200 shapes)
- reject persists plan-status/event + audit row
- role gates: admin/engineer may decide, viewer/guest get 403
- idempotent re-approve returns the same runId

No repo conftest exists for section4; this module builds its own apps.
"""

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.plan_gate import (
    _plan_gate_principal,
    configure_plan_gate,
    router,
)
from harness_orchestrator.service import OrchestratorService


def _principal(role: Role = Role.ADMIN, user_id: str = "u-gate") -> Principal:
    return Principal(userId=user_id, teamId="team-a", role=role)


def _client(
    service: OrchestratorService,
    principal: Principal,
) -> TestClient:
    """Fresh app per call: plan_gate router + principal override (no bearer)."""
    configure_plan_gate(service)
    app = FastAPI()
    app.include_router(router)

    async def _override() -> Principal:
        return principal

    app.dependency_overrides[_plan_gate_principal] = _override
    return TestClient(app)


def _post(client: TestClient, path: str, body: dict[str, Any] | None = None):
    return client.post(path, json=body if body is not None else {})


def _run_for_plan(service: OrchestratorService, plan_id: str) -> str | None:
    for rid, rd in service.runs.items():
        if rd.get("plan_id") == plan_id:
            return rid
    return None


# ----------------------------------------------------------------- fixtures


@pytest.fixture()
def service() -> OrchestratorService:
    """Bare in-memory orchestrator (no DB): pure unit mode."""
    return OrchestratorService()


class RecordingBus:
    """Duck-typed EventBus recording publishes (mirrors test_publish_ordering)."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        self.published.append(f"{run_id}:{name}:{json.dumps(payload or {}, default=str)}")

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        return None


@pytest.fixture()
def db_parts(tmp_path):
    """SQLite-backed service with the frozen tables the gate touches.

    run_claims stays ABSENT on purpose: with no project bound to /plans runs,
    bus publishes short-circuit and start_run takes the synchronous spawn path,
    so the queue table is never written and the walk works on a minimal schema.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/plan_gate.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE agent_events ("
                    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "project_id VARCHAR(36) NOT NULL,"
                    "run_id VARCHAR(36),"
                    "type VARCHAR(60) NOT NULL,"
                    "payload TEXT NOT NULL,"
                    "ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE agent_plans ("
                    "id VARCHAR(36) PRIMARY KEY,"
                    "run_id VARCHAR(36) NOT NULL,"
                    "goal TEXT NOT NULL,"
                    "files_likely_to_change JSON NOT NULL,"
                    "steps JSON NOT NULL,"
                    "tests JSON NOT NULL,"
                    "risks JSON NOT NULL,"
                    "estimated_cost_usd NUMERIC(12,6) NOT NULL,"
                    "forbidden_paths JSON NOT NULL,"
                    "expected_result TEXT NOT NULL,"
                    "status VARCHAR(20) NOT NULL,"
                    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE audit_log ("
                    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "actor_member_id VARCHAR(36),"
                    "actor_kind VARCHAR(16) NOT NULL,"
                    "action VARCHAR(80) NOT NULL,"
                    "target_type VARCHAR(60) NOT NULL,"
                    "target_id VARCHAR(80) NOT NULL,"
                    "detail JSON NOT NULL,"
                    "ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=RecordingBus())
    yield {"service": service, "factory": factory, "bus": None}
    asyncio.run(engine.dispose())


async def _event_rows(factory, run_id: str) -> list[dict[str, Any]]:
    async with factory() as db:
        result = await db.execute(
            text("SELECT type FROM agent_events WHERE run_id = :r ORDER BY seq"),
            {"r": run_id},
        )
        return [{"type": row["type"]} for row in result.mappings().all()]


async def _plan_status(factory, plan_id: str) -> str | None:
    async with factory() as db:
        return (
            await db.execute(
                text("SELECT status FROM agent_plans WHERE id = :p"), {"p": plan_id}
            )
        ).scalar()


async def _audit_rows(factory, action: str, target_id: str) -> list[Any]:
    async with factory() as db:
        result = await db.execute(
            text(
                "SELECT actor_member_id, target_type FROM audit_log "
                "WHERE action = :a AND target_id = :t"
            ),
            {"a": action, "t": target_id},
        )
        return result.all()


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- happy path


def test_propose_returns_201_with_plandid_and_plan(service):
    client = _client(service, _principal(Role.ADMIN))
    res = _post(client, "/api/v1/orchestrator/plans", {"goal": "Ship feature X"})
    assert res.status_code == 201
    body = res.json()
    assert set(body.keys()) == {"planId", "runId", "projectId", "plan"}
    assert body["runId"].startswith("run-")
    assert isinstance(body["planId"], str) and body["planId"]
    plan = body["plan"]
    # camelCase wire mirror of AgentPlan, field-by-field
    assert set(plan.keys()) == {
        "goal",
        "filesLikelyToChange",
        "steps",
        "tests",
        "risks",
        "estimatedCostUsd",
        "forbiddenPaths",
        "expectedResult",
    }
    assert plan["goal"] == "Ship feature X"


def test_propose_rejects_blank_goal(service):
    client = _client(service, _principal(Role.ENGINEER))
    res = _post(client, "/api/v1/orchestrator/plans", {"goal": ""})
    assert res.status_code == 422  # pydantic validation error envelope


def test_approve_returns_200_with_runid(service):
    client = _client(service, _principal(Role.ADMIN))
    plan_id = _post(client, "/api/v1/orchestrator/plans", {"goal": "g"}).json()["planId"]
    res = _post(client, f"/api/v1/orchestrator/plans/{plan_id}/approve")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"runId"}
    assert isinstance(body["runId"], str) and body["runId"]


def test_happy_walk_propose_approve_start(service):
    """The seam probe walk: propose → approve → start leaves the run EXECUTING."""
    client = _client(service, _principal(Role.ADMIN))
    proposed = _post(client, "/api/v1/orchestrator/plans", {"goal": "walk it"}).json()
    approved = _post(
        client, f"/api/v1/orchestrator/plans/{proposed['planId']}/approve"
    ).json()
    started = _post(client, f"/api/v1/orchestrator/runs/{approved['runId']}/start")
    assert started.status_code == 200
    assert started.json()["state"] == "executing"


def test_start_without_approval_conflicts_with_error_body(service):
    client = _client(service, _principal(Role.ENGINEER))
    res = client.post("/api/v1/orchestrator/runs/run-unapproved/start", json={})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"]["code"] == "PLAN_NOT_APPROVED"


# ------------------------------------------------------------ reject + audit


def test_reject_persists_status_and_audit_row(db_parts):
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    client = _client(service, _principal(Role.ADMIN, user_id="u-rej"))
    proposed = _post(client, "/api/v1/orchestrator/plans", {"goal": "reject me"}).json()
    plan_id = proposed["planId"]
    run_id = _run_for_plan(service, plan_id)
    assert run_id is not None

    res = _post(client, f"/api/v1/orchestrator/plans/{plan_id}/reject")
    assert res.status_code == 202
    body = res.json()
    assert body["audited"] is True
    assert body["status"] == "rejected"

    # plan mirror persisted as rejected (restart-safe, per D1's restart probe)
    assert _run(_plan_status(factory, plan_id)) == "rejected"

    # audit row persisted with actor identity from the session principal
    rows = _run(_audit_rows(factory, action="plan.rejected", target_id=plan_id))
    assert len(rows) == 1
    assert rows[0][0] == "u-rej"  # actor_member_id
    assert rows[0][1] == "plan"  # target_type

    # state machine went back to IDLE via state_machine vocabulary
    run_data = service.runs[run_id]
    sm = run_data["state_machine"]
    from harness_orchestrator.state_machine import AgentState

    assert sm.state is AgentState.IDLE
    assert sm.plan_approved is False


def test_reject_emits_nothing_after_state_reset(db_parts):
    """/plans runs carry no projectId yet, so bus publish short-circuits — the
    durable trail for reject is agent_plans.status + audit_log (asserted above).
    This pins that reject leaves NO approval flag behind."""
    service: OrchestratorService = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN))
    proposed = _post(client, "/api/v1/orchestrator/plans", {"goal": "g"}).json()
    plan_id = proposed["planId"]
    assert _post(client, f"/api/v1/orchestrator/plans/{plan_id}/reject").status_code == 202
    run_data = next(iter(service.runs.values()))
    assert run_data["state_machine"].plan_approved is False
    assert service.planner.is_approved(plan_id) is False


def test_rejected_then_start_conflicts(db_parts):
    service: OrchestratorService = db_parts["service"]
    client = _client(service, _principal(Role.ENGINEER))
    proposed = _post(client, "/api/v1/orchestrator/plans", {"goal": "g"}).json()
    plan_id = proposed["planId"]
    _post(client, f"/api/v1/orchestrator/plans/{plan_id}/reject")
    run_id = _run_for_plan(service, plan_id)
    res = _post(client, f"/api/v1/orchestrator/runs/{run_id}/start")
    assert res.status_code == 409
    assert res.json()["detail"]["error"]["code"] == "PLAN_NOT_APPROVED"


# ------------------------------------------------------------------ roles


def test_viewer_cannot_propose(service):
    client = _client(service, _principal(Role.VIEWER))
    res = _post(client, "/api/v1/orchestrator/plans", {"goal": "nope"})
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"
    assert service.runs == {}  # nothing minted behind the 403


def test_viewer_cannot_approve_or_reject(service):
    client = _client(service, _principal(Role.VIEWER))
    admin_client = _client(service, _principal(Role.ADMIN))
    seeded = _post(admin_client, "/api/v1/orchestrator/plans", {"goal": "g"}).json()["planId"]
    assert _post(client, f"/api/v1/orchestrator/plans/{seeded}/approve").status_code == 403
    assert _post(client, f"/api/v1/orchestrator/plans/{seeded}/reject").status_code == 403


def test_guest_cannot_decide(service):
    client = _client(service, _principal(Role.GUEST))
    admin_client = _client(service, _principal(Role.ADMIN))
    seeded = _post(admin_client, "/api/v1/orchestrator/plans", {"goal": "g"}).json()["planId"]
    assert _post(client, f"/api/v1/orchestrator/plans/{seeded}/approve").status_code == 403


def test_engineer_can_propose_approve_and_start(service):
    client = _client(service, _principal(Role.ENGINEER))
    proposed = _post(client, "/api/v1/orchestrator/plans", {"goal": "eng ok"}).json()
    approved = _post(
        client, f"/api/v1/orchestrator/plans/{proposed['planId']}/approve"
    ).json()
    started = _post(client, f"/api/v1/orchestrator/runs/{approved['runId']}/start")
    assert started.status_code == 200


def test_admin_can_reject(db_parts):
    service: OrchestratorService = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN))
    proposed = _post(client, "/api/v1/orchestrator/plans", {"goal": "g"}).json()
    res = _post(client, f"/api/v1/orchestrator/plans/{proposed['planId']}/reject")
    assert res.status_code == 202


# -------------------------------------------------------------- idempotency


def test_idempotent_reapprove_returns_same_runid(service):
    client = _client(service, _principal(Role.ADMIN))
    plan_id = _post(client, "/api/v1/orchestrator/plans", {"goal": "again"}).json()["planId"]
    first = _post(client, f"/api/v1/orchestrator/plans/{plan_id}/approve").json()
    second = _post(client, f"/api/v1/orchestrator/plans/{plan_id}/approve").json()
    third = _post(client, f"/api/v1/orchestrator/plans/{plan_id}/approve").json()
    assert first["runId"] == second["runId"] == third["runId"]


def test_unknown_plan_is_404(service):
    client = _client(service, _principal(Role.ADMIN))
    for suffix in ("approve", "reject"):
        res = _post(client, f"/api/v1/orchestrator/plans/plan-does-not-exist/{suffix}")
        assert res.status_code == 404
        # M17-B4 (MAC2-F2): unknown and FOREIGN plans now answer one shared
        # NOT_FOUND code (no-oracle law, mirroring api.py's run routes) —
        # the old PLAN_NOT_FOUND named the resource's existence class and
        # would have confirmed foreign ids across the tenant boundary.
        assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------- contract conformance


def test_openapi_declares_contracted_shapes(service):
    configure_plan_gate(service)
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    paths = schema["paths"]

    propose = paths["/api/v1/orchestrator/plans"]["post"]
    assert propose["responses"]["201"] is not None

    approve = paths["/api/v1/orchestrator/plans/{planId}/approve"]["post"]
    assert approve["responses"]["200"] is not None

    reject = paths["/api/v1/orchestrator/plans/{planId}/reject"]["post"]
    assert reject["responses"]["202"] is not None

    start = paths["/api/v1/orchestrator/runs/{runId}/start"]["post"]
    assert start["responses"]["200"] is not None
