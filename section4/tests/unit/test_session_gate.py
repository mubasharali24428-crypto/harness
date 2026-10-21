"""Part 40.005 (lane S): runs of a non-active session accept no new start.

Covers the direct start path (409 SESSION_NOT_ACTIVE), the queue-claim path (a run queued
BEFORE the session was paused/archived never starts), the pause-vs-start race, runs that
belong to no session, pre-0041 schemas, fail-closed behaviour on other database errors and
the session.run_refused audit row. Scratch schema mirrors test_workspace_gate.py with the
lane-S tables.
"""

import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.run_queue import get_queue_snapshot
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.session_gate import (
    CODE,
    SessionNotActiveError,
    session_block,
)

_DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    budget_usd REAL NULL
);
CREATE TABLE projects (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    workspace_id VARCHAR(36)
);
CREATE TABLE workspaces (
    id VARCHAR(36) PRIMARY KEY,
    state VARCHAR(16) NOT NULL DEFAULT 'active'
);
CREATE TABLE collab_sessions (
    id VARCHAR(36) PRIMARY KEY,
    state VARCHAR(16) NOT NULL DEFAULT 'active'
);
CREATE TABLE session_runs (
    run_id VARCHAR(36) PRIMARY KEY,
    session_id VARCHAR(36) NOT NULL
);
CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_member_id VARCHAR(36),
    actor_kind VARCHAR(16) NOT NULL,
    action VARCHAR(80) NOT NULL,
    target_type VARCHAR(40) NOT NULL DEFAULT '',
    target_id VARCHAR(80) NOT NULL DEFAULT '',
    detail TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL
);
CREATE TABLE cost_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36),
    agent_role VARCHAR(20) NOT NULL DEFAULT 'primary',
    model VARCHAR(120) NOT NULL DEFAULT 'stub',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12,6) NOT NULL,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36) NOT NULL,
    type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
CREATE TABLE agent_runs (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL
);
CREATE TABLE agent_plans (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    goal TEXT NOT NULL,
    files_likely_to_change TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL DEFAULT '[]',
    tests TEXT NOT NULL DEFAULT '[]',
    risks TEXT NOT NULL DEFAULT '[]',
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    forbidden_paths TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
"""

_NO_SESSIONS_DDL = _DDL.replace("collab_sessions", "unused_a").replace("session_runs", "unused_b")


class StubEngine:
    def __init__(self):
        self.calls = []

    def execute_goal(self, goal=None, session_id=None):
        self.calls.append(session_id)
        return {"sessionId": session_id, "engine": "stub", "status": "completed",
                "finalResponse": "done"}


async def _make(tmp_path, ddl=_DDL):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/gate.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for stmt in ddl.split(";"):
            if stmt.strip():
                await conn.execute(text(stmt.strip()))
    return engine, factory


async def _session(factory, sid, state, *runs):
    async with factory() as db:
        await db.execute(text("INSERT INTO collab_sessions (id, state) VALUES (:s, :st)"),
                         {"s": sid, "st": state})
        for r in runs:
            await db.execute(text("INSERT INTO session_runs (run_id, session_id) VALUES (:r, :s)"),
                             {"r": r, "s": sid})
        await db.commit()


async def _set_state(factory, sid, state):
    async with factory() as db:
        await db.execute(text("UPDATE collab_sessions SET state=:s WHERE id=:i"),
                         {"s": state, "i": sid})
        await db.commit()


async def _approve(svc, run_id, project_id=None):
    project_id = project_id or f"proj-{run_id}"  # one project per run: the queue is per project
    async with svc.session_factory() as db:
        await db.execute(text("INSERT INTO projects (id, team_id) VALUES (:p, 'team-a')"),
                         {"p": project_id})
        await db.commit()
    await svc.propose_plan(run_id, goal=f"goal {run_id}", project_id=project_id)
    await svc.approve_plan(run_id, svc.runs[run_id]["plan_id"], "u-test")


async def _audit(factory):
    async with factory() as db:
        rows = (await db.execute(text(
            "SELECT target_id, detail FROM audit_log WHERE action='session.run_refused'"
        ))).all()
    return [(r.target_id, json.loads(r.detail)) for r in rows]


async def _queued(factory):
    return [r["run_id"] for r in await get_queue_snapshot(factory) if r["state"] == "queued"]


@pytest.fixture
async def env(tmp_path):
    engine, factory = await _make(tmp_path)
    svc = OrchestratorService(engine=StubEngine(), session_factory=factory)
    yield svc, factory
    # Spawned engine tasks still hold sqlite connections; dispose() would stall (10 s, or forever).
    await asyncio.gather(
        *[r["task"] for r in svc.runs.values() if r.get("task") is not None],
        return_exceptions=True,
    )
    svc.shutdown()
    await engine.dispose()


async def test_block_lookup_states(env):
    _, f = env
    await _session(f, "s-active", "active", "r-active")
    for state in ("paused", "completed", "archived", "deleted"):
        await _session(f, f"s-{state}", state, f"r-{state}")
        assert await session_block(f, f"r-{state}") == (f"s-{state}", state)
    assert await session_block(f, "r-active") is None
    assert await session_block(f, "unlinked") is None
    assert await session_block(f, None) is None
    assert await session_block(None, "r-paused") is None


@pytest.mark.parametrize("state", ["paused", "completed", "archived", "deleted"])
async def test_start_refused_with_409_code_and_audited(env, state):
    svc, f = env
    await _session(f, "s1", state, "r1")
    await _approve(svc, "r1")
    with pytest.raises(SessionNotActiveError) as ei:
        await svc.start_execution("r1")
    assert ei.value.status_code == 409
    assert ei.value.detail["error"]["code"] == CODE == "SESSION_NOT_ACTIVE"
    assert await _queued(f) == []  # never enqueued
    ((target, detail),) = await _audit(f)
    assert target == "s1" == ei.value.session_id
    assert detail == {"runId": "r1", "sessionId": "s1", "state": state, "where": "start"}
    await asyncio.sleep(0.05)
    assert svc.engine.calls == []


async def test_active_session_and_sessionless_runs_start_as_before(env):
    svc, f = env
    await _session(f, "s1", "active", "ok-1")
    for rid in ("ok-1", "ok-2"):  # ok-2 belongs to no session
        await _approve(svc, rid)
        await svc.start_execution(rid)
    assert sorted(await _queued(f)) == ["ok-1", "ok-2"]
    assert await _audit(f) == []


async def test_queued_run_cannot_start_after_session_is_paused_or_archived(env):
    svc, f = env
    await _session(f, "s1", "active", "q1")
    for rid in ("q1", "q2"):
        await _approve(svc, rid)
        await svc.start_execution(rid)
    await _set_state(f, "s1", "archived")  # archived AFTER queueing
    assert await svc.poll_once() is None  # FIFO: q1 is claimed first and refused
    assert await svc.poll_once() == "q2"  # the sessionless run still runs
    assert await _queued(f) == []  # the claim row was finished, not left to spin
    states = {r["run_id"]: r["state"] for r in await get_queue_snapshot(f)}
    assert states.get("q1") != "running"
    ((_, detail),) = await _audit(f)
    assert detail["runId"] == "q1" and detail["where"] == "claim" and detail["state"] == "archived"
    await asyncio.sleep(0.05)
    assert "q1" not in svc.engine.calls


async def test_batch_claim_path_also_gated(env):
    svc, f = env
    await _session(f, "s1", "active", "b0", "b2")
    for i in range(3):
        await _approve(svc, f"b{i}")
        await svc.start_execution(f"b{i}")
    await _set_state(f, "s1", "paused")
    assert await svc.poll_once(max_claims=5) == ["b1"]


async def test_restore_reopens_the_gate(env):
    svc, f = env
    await _session(f, "s1", "paused", "re1")
    await _approve(svc, "re1")
    with pytest.raises(SessionNotActiveError):
        await svc.start_execution("re1")
    await _set_state(f, "s1", "active")
    await svc.start_execution("re1")
    assert await _queued(f) == ["re1"]


async def test_unlinking_a_run_lifts_the_gate(env):
    svc, f = env
    await _session(f, "s1", "archived", "u1")
    await _approve(svc, "u1")
    with pytest.raises(SessionNotActiveError):
        await svc.start_execution("u1")
    async with f() as db:
        await db.execute(text("DELETE FROM session_runs WHERE run_id='u1'"))
        await db.commit()
    await svc.start_execution("u1")
    assert await _queued(f) == ["u1"]


async def test_race_pause_vs_start_never_runs_after_pause(tmp_path):
    """Whatever the interleaving, once the pause commits no run may spawn."""
    engine, f = await _make(tmp_path)
    try:
        n = 12
        await _session(f, "s-race", "active", *[f"race-{i}" for i in range(n)])
        svc = OrchestratorService(engine=StubEngine(), session_factory=f)
        for i in range(n):
            await _approve(svc, f"race-{i}")

        async def starts():
            out = []
            for i in range(n):
                try:
                    await svc.start_execution(f"race-{i}")
                    out.append("started")
                except SessionNotActiveError:
                    out.append("refused")
                await asyncio.sleep(0)
            return out

        async def pause():
            await asyncio.sleep(0.01)
            await _set_state(f, "s-race", "paused")

        results, _ = await asyncio.gather(starts(), pause())
        assert await svc.poll_once(max_claims=n) == []  # after the pause nothing is admitted
        assert await _queued(f) == []
        await asyncio.sleep(0.05)
        assert svc.engine.calls == []
        assert len(await _audit(f)) >= results.count("refused")
        svc.shutdown()
    finally:
        await engine.dispose()


async def test_schema_without_session_tables_is_unchanged_behavior(tmp_path):
    engine, f = await _make(tmp_path, _NO_SESSIONS_DDL)
    try:
        assert await session_block(f, "r1") is None
        svc = OrchestratorService(engine=StubEngine(), session_factory=f)
        await _approve(svc, "legacy")
        await svc.start_execution("legacy")
        assert await _queued(f) == ["legacy"]
        svc.shutdown()
    finally:
        await engine.dispose()


async def test_unexpected_database_error_fails_closed():
    class Broken:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("connection reset by peer")

        async def __aexit__(self, *a):
            return False

    with pytest.raises(RuntimeError):
        await session_block(Broken(), "r1")


# ---- A6: runs already executing follow the session's state --------------------------------


async def _claim_running(factory, run_id):
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at,"
            " worker_id) VALUES (:r, 'p', 'running', 't', 't', 'other-worker')"), {"r": run_id})
        await db.commit()


async def test_sweep_pauses_for_a_paused_session_and_stops_for_an_archived_one(env):
    from harness_orchestrator.session_gate import sweep_inactive_session_runs
    from harness_orchestrator.state_machine import AgentState

    svc, f = env
    layout = {"sp": ("paused", "r-pause"), "sa": ("archived", "r-arch"),
              "sc": ("completed", "r-comp"), "sk": ("active", "r-ok")}
    for sid, (state, rid) in layout.items():
        await _session(f, sid, state, rid)
        await _approve(svc, rid)
        await _claim_running(f, rid)
    svc.runs["r-pause"]["state_machine"].state = AgentState.EXECUTING  # a live claim means running
    acted = await sweep_inactive_session_runs(svc)
    assert sorted(acted) == ["r-arch", "r-comp", "r-pause"]
    assert svc.runs["r-pause"]["state_machine"].state is AgentState.PAUSED  # resumable
    assert svc.runs["r-arch"]["state_machine"].state is AgentState.DONE
    assert svc.runs["r-comp"]["state_machine"].state is AgentState.DONE
    assert svc.runs["r-ok"]["state_machine"].state is not AgentState.DONE
    assert await sweep_inactive_session_runs(svc) == []  # claims released
    async with f() as db:
        rows = (await db.execute(text(
            "SELECT action, target_id FROM audit_log WHERE action LIKE 'session.run_%ed'"
            " AND action <> 'session.run_refused' ORDER BY action, target_id"))).all()
    assert [(r.action, r.target_id) for r in rows] == [
        ("session.run_paused", "sp"), ("session.run_stopped", "sa"), ("session.run_stopped", "sc")]


def test_step_wire_tells_a_stop_from_a_pause_and_carries_a_failure_reason():
    step = OrchestratorService._step_wire
    row = lambda t, p: {"seq": 1, "type": t, "ts": "x", "payload": p}  # noqa: E731
    stop = step(row("paused", '{"reason": "user_requested_stop"}'))
    assert stop["reason"] == "user_requested_stop"
    assert step(row("paused", {"reason": "user_requested"}))["reason"] == "user_requested"
    assert step(row("engine_failed", '{"reason": "timeout: 600s"}'))["reason"] == "timeout: 600s"
    assert "reason" not in step(row("resumed", "not json"))
    assert "reason" not in step(row("resumed", {"runId": "r"}))
