"""M23-A (session-2 area 16): plan-approval TTL enforcement tests.

The gap: an approval had no expiry — as long as the run stayed alive, an
approval from any point in the past could be replayed into execution. These
tests pin the fix at both enforcement layers:

* the state machine (in-memory approve→start path), and
* the service (durable check against the PLAN_APPROVED event ts — the
  witness for replica B / post-restart, where the machine never saw the
  approve call).

Posture rules pinned here: stale evidence refuses (PlanNotApprovedError /
InvalidStateTransitionError, clearing the flag so a retry needs a fresh
audit-backed approval); an ABSENT timestamp stays lenient (bare unit mode,
scratch mirrors without a ts column) — the TTL never invents an expiry.
"""

import json
from datetime import timedelta

import pytest
from harness_contracts.events import EventType
from harness_contracts.timeutil import now_utc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.planner import PlanNotApprovedError
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import (
    ENV_PLAN_APPROVAL_TTL_SECONDS,
    AgentState,
    AgentStateMachine,
    InvalidStateTransitionError,
    approval_expired,
    approved_at_from_wire,
)

# agent_events carries the frozen `ts` column here (unlike the older scratch
# fixtures) — the durable TTL check reads it; CURRENT_TIMESTAMP keeps the bus
# double's rows parseable and in-the-present.
DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    budget_usd REAL NULL
);
CREATE TABLE projects (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL
);
CREATE TABLE agent_runs (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL
);
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36) NOT NULL,
    type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE agent_plans (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    goal TEXT NOT NULL,
    files_likely_to_change TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL DEFAULT '[]',
    tests TEXT NOT NULL DEFAULT '[]',
    risks TEXT NOT NULL DEFAULT '[]',
    estimated_cost_usd REAL NOT NULL DEFAULT 0.05,
    forbidden_paths TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL
);
"""

RUN = "run-ttl"
PROJECT = "proj-ttl"


class PersistingBus:
    """S1 delivery contract: persist the envelope BEFORE fan-out."""

    def __init__(self, factory) -> None:
        self._factory = factory

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        async with self._factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_events (run_id, type, payload)"
                    " VALUES (:r, :t, :payload)"
                ),
                {
                    "r": run_id,
                    "t": name,
                    "payload": json.dumps(payload or {}, default=str),
                },
            )
            await db.commit()

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        if not project_id:
            return
        async with self._factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id) VALUES (:r, :p)"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"r": run_id, "p": project_id},
            )
            await db.commit()


async def _make_store(tmp_path, tag: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{tag}.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for statement in DDL.split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(text(stmt))
        await conn.execute(text("INSERT INTO teams (id, name) VALUES ('team-a', 'A')"))
        await conn.execute(
            text("INSERT INTO projects (id, team_id) VALUES (:p, 'team-a')"),
            {"p": PROJECT},
        )
    return engine, factory


def _make_service(factory) -> OrchestratorService:
    def runner(goal: str) -> str:
        return f"ran:{goal}"

    return OrchestratorService(
        engine=DeepSeekHarnessWrapper(backend_runner=runner),
        session_factory=factory,
        run_queue_factory=factory,
        bus=PersistingBus(factory),
    )


async def _seed_approval(factory, run_id: str, *, ts: str, payload: dict) -> None:
    """Hand-seed the durable witnesses: approved mirror row + PLAN_APPROVED
    event with an EXPLICIT ts (legacy payload without approvedAt exercises
    the ts fallback path)."""
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO agent_runs (id, project_id) VALUES (:r, :p)"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"r": run_id, "p": PROJECT},
        )
        await db.execute(
            text(
                "INSERT INTO agent_plans (id, run_id, goal, status, version)"
                " VALUES (:id, :r, 'g', 'approved', 1)"
            ),
            {"id": f"plan-{run_id}", "r": run_id},
        )
        await db.execute(
            text(
                "INSERT INTO agent_events (run_id, type, payload, ts)"
                " VALUES (:r, 'plan_approved', :payload, :ts)"
            ),
            {"r": run_id, "payload": json.dumps(payload), "ts": ts},
        )
        await db.commit()


async def _event_types(factory, run_id: str) -> list[str]:
    async with factory() as db:
        rows = (
            await db.execute(
                text("SELECT type FROM agent_events WHERE run_id = :r ORDER BY seq"),
                {"r": run_id},
            )
        ).fetchall()
    return [row[0] for row in rows]


async def _drain_publish_chain(svc, run_id: str) -> None:
    """Await the service's in-flight publish chain (M19-N1 ordering law):
    lifecycle announcements persist asynchronously after the RAM transition."""
    chain = svc._publish_chains.get(run_id)
    if chain is not None:
        try:
            await chain
        except Exception:  # noqa: BLE001 — chain errors are logged, not raised
            pass


# ------------------------------------------------------------- helpers (pure)


def test_approved_at_from_wire_parses_iso_naive_and_rejects_junk():
    aware = approved_at_from_wire("2026-10-17T01:02:03+00:00")
    assert aware is not None and aware.utcoffset() == timedelta(0)
    naive = approved_at_from_wire("2026-10-17 01:02:03")  # sqlite text form
    assert naive is not None and naive.utcoffset() == timedelta(0)
    assert approved_at_from_wire(now_utc()) is not None
    assert approved_at_from_wire(None) is None
    assert approved_at_from_wire("not-a-time") is None


def test_approval_expired_boundary_and_disable():
    old = now_utc() - timedelta(hours=2)
    assert approval_expired(old, ttl_seconds=3600) is True
    assert approval_expired(now_utc() - timedelta(seconds=59), ttl_seconds=60) is False
    assert approval_expired(old, ttl_seconds=0) is False  # disabled
    assert approval_expired(None, ttl_seconds=1) is False  # no evidence


# ------------------------------------------------------------ state machine


def test_fresh_approval_starts_execution(monkeypatch):
    monkeypatch.delenv(ENV_PLAN_APPROVAL_TTL_SECONDS, raising=False)
    sm = AgentStateMachine(run_id="r1")
    sm.start_planning()
    sm.plan_proposed("p1")
    sm.approve_plan("p1", "u1")
    sm.start_execution()
    assert sm.state is AgentState.EXECUTING


def test_expired_approval_refused_and_cleared(monkeypatch):
    monkeypatch.delenv(ENV_PLAN_APPROVAL_TTL_SECONDS, raising=False)
    sm = AgentStateMachine(run_id="r2")
    sm.start_planning()
    sm.plan_proposed("p2")
    sm.approve_plan("p2", "u2")
    sm.plan_approved_at = now_utc() - timedelta(hours=25)  # default TTL = 24h
    with pytest.raises(InvalidStateTransitionError, match="expired"):
        sm.start_execution()
    assert sm.state is not AgentState.EXECUTING
    assert sm.plan_approved is False, "stale approval must be cleared"
    assert sm.plan_approved_at is None


def test_custom_ttl_env_is_honored(monkeypatch):
    monkeypatch.setenv(ENV_PLAN_APPROVAL_TTL_SECONDS, "60")
    sm = AgentStateMachine(run_id="r3")
    sm.start_planning()
    sm.plan_proposed("p3")
    sm.approve_plan("p3", "u3")
    sm.plan_approved_at = now_utc() - timedelta(seconds=61)
    with pytest.raises(InvalidStateTransitionError, match="expired"):
        sm.start_execution()


def test_ttl_zero_disables_enforcement(monkeypatch):
    monkeypatch.setenv(ENV_PLAN_APPROVAL_TTL_SECONDS, "0")
    sm = AgentStateMachine(run_id="r4")
    sm.start_planning()
    sm.plan_proposed("p4")
    sm.approve_plan("p4", "u4")
    sm.plan_approved_at = now_utc() - timedelta(days=365)
    sm.start_execution()  # documented escape hatch: indate approvals start
    assert sm.state is AgentState.EXECUTING


def test_approval_payload_carries_approved_at_timestamp(monkeypatch):
    monkeypatch.delenv(ENV_PLAN_APPROVAL_TTL_SECONDS, raising=False)
    seen: list[tuple] = []
    sm = AgentStateMachine(run_id="r5", on_event=lambda t, p: seen.append((t, p)))
    sm.start_planning()
    sm.plan_proposed("p5")
    sm.approve_plan("p5", "u5")
    approved = [p for t, p in seen if t is EventType.PLAN_APPROVED]
    assert len(approved) == 1
    parsed = approved_at_from_wire(approved[0]["approvedAt"])
    assert parsed is not None
    assert parsed == sm.plan_approved_at


def test_reject_clears_approval_timestamp():
    sm = AgentStateMachine(run_id="r6")
    sm.start_planning()
    sm.plan_proposed("p6")
    sm.approve_plan("p6", "u6")
    assert sm.plan_approved_at is not None
    sm.reject_plan("p6", "u6")
    assert sm.plan_approved is False and sm.plan_approved_at is None


# ------------------------------------------------------- service (durable log)


async def test_stale_durable_approval_refuses_start(tmp_path, monkeypatch):
    """Replica B / restart case: the machine never saw approve_plan; the
    event log's ts is the only witness — and it is 25h old ⇒ refuse."""
    monkeypatch.delenv(ENV_PLAN_APPROVAL_TTL_SECONDS, raising=False)
    engine, factory = await _make_store(tmp_path, "stale")
    try:
        stale_ts = (now_utc() - timedelta(hours=25)).isoformat()
        await _seed_approval(
            factory, RUN, ts=stale_ts, payload={"planId": f"plan-{RUN}", "runId": RUN}
        )
        svc = _make_service(factory)
        with pytest.raises(PlanNotApprovedError, match="expired"):
            await svc.start_execution(RUN)
        # Nothing was announced, enqueued or damaged by the refusal.
        types = await _event_types(factory, RUN)
        assert "execution_started" not in types
        async with factory() as db:
            claims = (await db.execute(text("SELECT COUNT(*) FROM run_claims"))).scalar()
        assert claims == 0
    finally:
        await engine.dispose()


async def test_fresh_durable_approval_starts_and_stamps_timestamp(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_PLAN_APPROVAL_TTL_SECONDS, raising=False)
    engine, factory = await _make_store(tmp_path, "fresh")
    try:
        fresh_ts = now_utc().isoformat()
        await _seed_approval(
            factory, RUN, ts=fresh_ts, payload={"planId": f"plan-{RUN}", "runId": RUN}
        )
        svc = _make_service(factory)
        await svc.start_execution(RUN)
        await _drain_publish_chain(svc, RUN)
        sm = svc.runs[RUN]["state_machine"]
        assert sm.state is AgentState.EXECUTING
        assert sm.plan_approved_at is not None, "durable ts must stamp the machine"
        assert await _event_types(factory, RUN) == [
            "plan_approved",
            "execution_started",
        ]
    finally:
        await engine.dispose()


async def test_restart_round_trip_approve_then_start_on_fresh_replica(
    tmp_path, monkeypatch
):
    """Full loop: replica A approves (approvedAt rides the persisted event);
    replica B — empty RAM — must accept the fresh approval and refuse a
    backdated one after the clock moves (payload is authoritative)."""
    monkeypatch.delenv(ENV_PLAN_APPROVAL_TTL_SECONDS, raising=False)
    engine, factory = await _make_store(tmp_path, "roundtrip")
    try:
        svc_a = _make_service(factory)
        plan_id = await svc_a.propose_plan(RUN, goal="fix bug", project_id=PROJECT)
        await svc_a.approve_plan(RUN, plan_id, "u1")
        await _drain_publish_chain(svc_a, RUN)

        svc_b = _make_service(factory)
        await svc_b.start_execution(RUN)
        await _drain_publish_chain(svc_b, RUN)
        assert svc_b.runs[RUN]["state_machine"].state is AgentState.EXECUTING

        # Now move the clock: same payload, aged past the TTL ⇒ refused.
        aged_payload = json.dumps(
            {
                "planId": plan_id,
                "runId": RUN,
                "approvedBy": "u1",
                "approvedAt": (now_utc() - timedelta(hours=25)).isoformat(),
            }
        )
        async with factory() as db:
            await db.execute(
                text(
                    "UPDATE agent_events SET payload = :p"
                    " WHERE run_id = :r AND type = 'plan_approved'"
                ),
                {"p": aged_payload, "r": RUN},
            )
            await db.execute(
                text("DELETE FROM agent_events WHERE run_id = :r AND type = 'execution_started'"),
                {"r": RUN},
            )
            await db.execute(
                text("DELETE FROM run_claims WHERE run_id = :r"), {"r": RUN}
            )
            await db.execute(
                text(
                    "UPDATE agent_plans SET status = 'approved'"
                    " WHERE id = :p"
                ),
                {"p": plan_id},
            )
            await db.commit()

        svc_c = _make_service(factory)  # another fresh replica, empty RAM
        with pytest.raises(PlanNotApprovedError, match="expired"):
            await svc_c.start_execution(RUN)
    finally:
        await engine.dispose()
