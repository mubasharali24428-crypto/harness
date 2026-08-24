"""M12-F2 tests — optimistic concurrency for plan decisions (lane F2, Squad BETA).

Mission matrix:
- sequential approve → second approve stays idempotent-same-runId
  (W1-C1 behavior preserved)
- simulated race with two connections and one stale version → exactly one
  decision wins, loser gets 409 PLAN_ALREADY_DECIDED error_body
- rejected plan reject → 409, consistent (single winner, single audit row)
- CAS primitive: success bumps+returns version; stale version raises and
  leaves the row untouched; missing mirror row keeps the legacy silent skip
- pre-0013 mirror (no ``version`` column) degrades to legacy unconditional
  decision writes ("participates when the column exists", cf. test_billing)

No repo conftest exists for section4; this module builds its own apps
(same pattern as test_plan_gate.py, whose W1-C1 surface must keep passing).
"""

import asyncio
from typing import Any

import aiosqlite
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
from harness_orchestrator.service import OrchestratorService, PlanAlreadyDecidedError


def _principal(role: Role = Role.ADMIN, user_id: str = "u-f2") -> Principal:
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


def _run(coro):
    return asyncio.run(coro)


class RecordingBus:
    """Duck-typed EventBus recording publishes (mirrors test_plan_gate)."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        self.published.append(f"{run_id}:{name}")

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        return None


# Post-0013 agent_plans shape (0001 frozen columns + version INTEGER NOT NULL 0).
_AGENT_PLANS_DDL_V1 = (
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
    "version INTEGER NOT NULL DEFAULT 0,"
    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)

# Pre-0013 shape (what test_plan_gate's fixture built before lane F2 existed).
_AGENT_PLANS_DDL_V0 = (
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

_EVENTS_DDL = (
    "CREATE TABLE agent_events ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    "project_id VARCHAR(36) NOT NULL,"
    "run_id VARCHAR(36),"
    "type VARCHAR(60) NOT NULL,"
    "payload TEXT NOT NULL,"
    "ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)

_AUDIT_DDL = (
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


async def _create_schema(engine, plans_ddl: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(_EVENTS_DDL))
        await conn.execute(text(plans_ddl))
        await conn.execute(text(_AUDIT_DDL))


def _db_parts(tmp_path, plans_ddl: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/f2.db")
    asyncio.run(_create_schema(engine, plans_ddl))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=RecordingBus())
    yield {"service": service, "factory": factory, "path": f"{tmp_path}/f2.db"}
    asyncio.run(engine.dispose())


@pytest.fixture()
def db_parts(tmp_path):
    """SQLite store carrying the post-0013 agent_plans (version column present)."""
    yield from _db_parts(tmp_path, _AGENT_PLANS_DDL_V1)


@pytest.fixture()
def legacy_db_parts(tmp_path):
    """Pre-0013 mirror (no version column): graceful-degradation guarantees."""
    yield from _db_parts(tmp_path, _AGENT_PLANS_DDL_V0)


async def _plan_state(factory, plan_id: str) -> dict[str, Any]:
    async with factory() as db:
        row = (
            await db.execute(
                text("SELECT status, version FROM agent_plans WHERE id = :p"),
                {"p": plan_id},
            )
        ).first()
    return {"status": row.status if row else None, "version": row.version if row else None}


async def _raw_exec(path: str, sql: str, params: tuple = ()) -> None:
    async with aiosqlite.connect(path) as conn:
        await conn.execute(sql, params)
        await conn.commit()


async def _audit_count(factory, action: str, target_id: str) -> int:
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT seq FROM audit_log WHERE action = :a AND target_id = :t"
                ),
                {"a": action, "t": target_id},
            )
        ).all()
    return len(rows)


def _propose(client: TestClient, goal: str = "f2 concurrency") -> str:
    res = client.post("/api/v1/orchestrator/plans", json={"goal": goal})
    assert res.status_code == 201, res.text
    return res.json()["planId"]


# ------------------------------------------------- sequential / idempotency


def test_propose_persists_version_zero(db_parts):
    service = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN))
    plan_id = _propose(client)
    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "proposed", "version": 0}


def test_sequential_approve_then_reapprove_same_runid(db_parts):
    """Existing W1-C1 behavior preserved: re-approve is idempotent, same runId."""
    service = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN))
    plan_id = _propose(client, "idempotent walk")
    first = client.post(f"/api/v1/orchestrator/plans/{plan_id}/approve")
    assert first.status_code == 200
    second = client.post(f"/api/v1/orchestrator/plans/{plan_id}/approve")
    assert second.status_code == 200
    assert first.json()["runId"] == second.json()["runId"]
    # Re-approve short-circuits BEFORE the CAS: version moved exactly once.
    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "approved", "version": 1}


def test_approve_happy_walk_still_starts(db_parts):
    service = db_parts["service"]
    client = _client(service, _principal(Role.ENGINEER))
    plan_id = _propose(client, "walk")
    approved = client.post(f"/api/v1/orchestrator/plans/{plan_id}/approve").json()
    started = client.post(f"/api/v1/orchestrator/runs/{approved['runId']}/start")
    assert started.status_code == 200
    assert started.json()["state"] == "executing"


# ------------------------------------------------------------- CAS primitive


def test_cas_success_bumps_and_returns_new_version(db_parts):
    service: OrchestratorService = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN))
    plan_id = _propose(client)
    run_id = next(
        rid for rid, rd in service.runs.items() if rd.get("plan_id") == plan_id
    )
    new_version = _run(
        service._persist_plan_status(run_id, plan_id, "approved", expected_version=0)
    )
    assert new_version == 1
    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "approved", "version": 1}
    # Second decision on the NEW version moves 1 → 2.
    newer = _run(
        service._persist_plan_status(run_id, plan_id, "approved", expected_version=1)
    )
    assert newer == 2


def test_cas_stale_version_raises_and_leaves_row(db_parts):
    service: OrchestratorService = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN))
    plan_id = _propose(client)
    run_id = next(
        rid for rid, rd in service.runs.items() if rd.get("plan_id") == plan_id
    )
    winner = _run(
        service._persist_plan_status(run_id, plan_id, "approved", expected_version=0)
    )
    assert winner == 1
    with pytest.raises(PlanAlreadyDecidedError):
        _run(
            service._persist_plan_status(
                run_id, plan_id, "rejected", expected_version=0
            )
        )
    # Loser wrote nothing: winner's decision stands untouched.
    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "approved", "version": 1}


def test_cas_missing_mirror_row_keeps_legacy_silent_skip(db_parts):
    """Zero-rows because the row never existed ≠ conflict: legacy posture."""
    service: OrchestratorService = db_parts["service"]
    result = _run(
        service._persist_plan_status("run-x", "plan-never-persisted", "approved",
                                     expected_version=0)
    )
    assert result is None  # silent skip, no PlanAlreadyDecidedError


# ------------------------------------------------------- simulated races


def test_race_two_connections_one_stale_version_exactly_one_wins(db_parts):
    """Two admins load the same undecided plan (both see version 0); admin A
    approves through the API; admin B's decision write still carries the STALE
    seen version. Connection B proves the predicate rejects it at the SQL
    layer AND the service guard maps it to PlanAlreadyDecidedError."""
    service: OrchestratorService = db_parts["service"]
    path = db_parts["path"]
    admin_a = _client(service, _principal(Role.ADMIN, user_id="u-a"))
    plan_id = _propose(admin_a)
    run_id = next(
        rid for rid, rd in service.runs.items() if rd.get("plan_id") == plan_id
    )

    async def _race():
        # Two independent connections observe the same undecided version.
        seen = []
        for _ in range(2):
            async with aiosqlite.connect(path) as conn:
                cur = await conn.execute(
                    "SELECT version FROM agent_plans WHERE id = ?", (plan_id,)
                )
                row = await cur.fetchone()
                assert row is not None
                seen.append(row[0])
        assert seen == [0, 0]

        # Admin A wins through the API (durable CAS 0 → 1).
        win = admin_a.post(f"/api/v1/orchestrator/plans/{plan_id}/approve")
        assert win.status_code == 200

        # Admin B's stale connection: raw SQL twin of the guarded write.
        async with aiosqlite.connect(path) as conn_b:
            cur = await conn_b.execute(
                "UPDATE agent_plans SET status = 'rejected' "
                "WHERE id = ? AND version = ?",
                (plan_id, seen[1]),
            )
            assert cur.rowcount == 0  # predicate fails: B's version is stale
            await conn_b.rollback()

        # Same loss through the service guard → typed conflict, row untouched.
        with pytest.raises(PlanAlreadyDecidedError):
            await service._persist_plan_status(
                run_id, plan_id, "rejected", expected_version=seen[1]
            )

    _run(_race())

    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "approved", "version": 1}  # exactly one winner
    assert service.planner.is_approved(plan_id) is True
    # Loser left no memory trace either: B's reject never flipped the machine.
    assert service.runs[run_id]["state_machine"].plan_approved is True


def test_api_race_loser_gets_409_plan_already_decided_envelope(db_parts):
    """Mixed approve/reject race at the HTTP seam: reject wins, late approve
    answers 409 with the frozen error_body envelope."""
    service: OrchestratorService = db_parts["service"]
    winner_client = _client(service, _principal(Role.ADMIN, user_id="u-win"))
    loser_client = _client(service, _principal(Role.ADMIN, user_id="u-lose"))
    plan_id = _propose(winner_client)

    rej = winner_client.post(f"/api/v1/orchestrator/plans/{plan_id}/reject")
    assert rej.status_code == 202

    app = loser_client.post(f"/api/v1/orchestrator/plans/{plan_id}/approve")
    assert app.status_code == 409
    detail = app.json()["detail"]["error"]
    assert set(detail.keys()) == {"code", "message"}
    assert detail["code"] == "PLAN_ALREADY_DECIDED"

    # Winner kept: rejected/1, and the loser mutated nothing in memory.
    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "rejected", "version": 1}
    run_id = next(
        rid for rid, rd in service.runs.items() if rd.get("plan_id") == plan_id
    )
    assert service.runs[run_id]["state_machine"].plan_approved is False
    assert service.planner.is_approved(plan_id) is False


def test_reverse_race_approve_wins_late_reject_is_409(db_parts):
    """Mirror direction: approve wins; a concurrent reject loses with 409."""
    service: OrchestratorService = db_parts["service"]
    winner_client = _client(service, _principal(Role.ENGINEER, user_id="u-win"))
    loser_client = _client(service, _principal(Role.ADMIN, user_id="u-lose"))
    plan_id = _propose(winner_client)

    app = winner_client.post(f"/api/v1/orchestrator/plans/{plan_id}/approve")
    assert app.status_code == 200

    rej = loser_client.post(f"/api/v1/orchestrator/plans/{plan_id}/reject")
    assert rej.status_code == 409
    assert rej.json()["detail"]["error"]["code"] == "PLAN_ALREADY_DECIDED"

    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "approved", "version": 1}
    assert _run(_audit_count(db_parts["factory"], "plan.rejected", plan_id)) == 0


# --------------------------------------------------- rejected-plan consistency


def test_rejected_plan_second_reject_is_409_consistent(db_parts):
    """Reject → reject: first wins, second 409s consistently (no double audit,
    no state churn, envelope-shaped body)."""
    service: OrchestratorService = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN, user_id="u-rej"))
    plan_id = _propose(client, "reject me twice")

    first = client.post(f"/api/v1/orchestrator/plans/{plan_id}/reject")
    assert first.status_code == 202
    assert first.json()["audited"] is True

    second = client.post(f"/api/v1/orchestrator/plans/{plan_id}/reject")
    assert second.status_code == 409
    detail = second.json()["detail"]["error"]
    assert detail["code"] == "PLAN_ALREADY_DECIDED"

    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "rejected", "version": 1}
    assert _run(_audit_count(db_parts["factory"], "plan.rejected", plan_id)) == 1
    run_id = next(
        rid for rid, rd in service.runs.items() if rd.get("plan_id") == plan_id
    )
    assert service.runs[run_id]["state_machine"].plan_approved is False


def test_reject_window_is_durable_not_memory_only(db_parts):
    """A restart-rehydrated run boots IDLE, so the rejection window consults
    the persisted mirror: a plan another node already decided cannot be
    rejected here, but an undecided one still can."""
    service: OrchestratorService = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN))
    plan_id = _propose(client, "durable window")

    # Simulate another node deciding while THIS process still shows AWAITING.
    _run(
        _raw_exec(
            db_parts["path"],
            "UPDATE agent_plans SET status = 'approved', version = 1"
            " WHERE id = ?",
            (plan_id,),
        )
    )
    rej = client.post(f"/api/v1/orchestrator/plans/{plan_id}/reject")
    assert rej.status_code == 409
    assert rej.json()["detail"]["error"]["code"] == "PLAN_ALREADY_DECIDED"
    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "approved", "version": 1}


# ------------------------------------------------- legacy surfaces preserved


def test_legacy_service_approve_survives_lost_race_without_raising(db_parts):
    """In-process service.approve_plan keeps its best-effort posture: losing
    the durable CAS logs instead of raising (legacy callers never see 500s)."""
    service: OrchestratorService = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN))
    plan_id = _propose(client, "legacy best effort")
    run_id = next(
        rid for rid, rd in service.runs.items() if rd.get("plan_id") == plan_id
    )

    # Another principal already decided durably.
    _run(
        service._persist_plan_status(run_id, plan_id, "rejected", expected_version=0)
    )
    # Legacy surface: must NOT raise despite losing the mirror CAS.
    _run(service.approve_plan(run_id, plan_id, "u-legacy"))
    assert service.planner.is_approved(plan_id) is True
    # Winner's durable decision was not overwritten.
    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "rejected", "version": 1}


def test_pre0013_mirror_degrades_to_legacy_decision_writes(legacy_db_parts):
    """Stores whose agent_plans predates 0013 keep working exactly like
    W1-C1: decisions persist, nothing crashes, no phantom 409s."""
    service: OrchestratorService = legacy_db_parts["service"]
    client = _client(service, _principal(Role.ADMIN, user_id="u-old"))
    plan_id = _propose(client, "pre-0013 mirror")

    rej = client.post(f"/api/v1/orchestrator/plans/{plan_id}/reject")
    assert rej.status_code == 202
    assert rej.json()["audited"] is True

    async def _status():
        async with legacy_db_parts["factory"]() as db:
            return (
                await db.execute(
                    text("SELECT status FROM agent_plans WHERE id = :p"),
                    {"p": plan_id},
                )
            ).scalar()

    assert _run(_status()) == "rejected"


# --------------------------------------------------------- role gates intact


def test_viewer_still_denied_on_both_routes(db_parts):
    service: OrchestratorService = db_parts["service"]
    viewer = _client(service, _principal(Role.VIEWER))
    admin = _client(service, _principal(Role.ADMIN))
    plan_id = _propose(admin)
    assert (
        viewer.post(f"/api/v1/orchestrator/plans/{plan_id}/approve").status_code == 403
    )
    assert (
        viewer.post(f"/api/v1/orchestrator/plans/{plan_id}/reject").status_code == 403
    )
    state = _run(_plan_state(db_parts["factory"], plan_id))
    assert state == {"status": "proposed", "version": 0}


def test_unknown_plan_is_404_not_409(db_parts):
    service: OrchestratorService = db_parts["service"]
    client = _client(service, _principal(Role.ADMIN))
    for suffix in ("approve", "reject"):
        res = client.post(f"/api/v1/orchestrator/plans/plan-nope/{suffix}")
        assert res.status_code == 404
        assert res.json()["detail"]["error"]["code"] == "PLAN_NOT_FOUND"
