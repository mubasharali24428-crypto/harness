"""Part 39.008 (lane W): non-active workspaces accept no new runs.

Covers the direct start path (409 WORKSPACE_NOT_ACTIVE), the queue-claim path
(a run queued BEFORE the archive never starts), the archive-vs-start race, the
no-workspace and pre-0040-schema behavior, and the workspace.run_refused audit.
Scratch schema mirrors test_claim_batching.py plus the lane-W tables.
"""

import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.run_queue import get_queue_snapshot
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.workspace_gate import (
    CODE,
    WorkspaceNotActiveError,
    workspace_block,
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

_NO_WS_DDL = _DDL.replace("workspace_id VARCHAR(36)\n", "x INTEGER\n")


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


async def _seed(factory, projects):
    """projects: {project_id: (workspace_id|None, state|None)}"""
    async with factory() as db:
        for pid, (wid, state) in projects.items():
            if wid:
                await db.execute(text("INSERT INTO workspaces (id, state) VALUES (:w, :s)"),
                                 {"w": wid, "s": state})
            await db.execute(text("INSERT INTO projects (id, team_id, workspace_id)"
                                  " VALUES (:p, 'team-a', :w)"), {"p": pid, "w": wid})
        await db.commit()


async def _set_state(factory, wid, state):
    async with factory() as db:
        await db.execute(text("UPDATE workspaces SET state=:s WHERE id=:w"),
                         {"s": state, "w": wid})
        await db.commit()


async def _approve(svc, run_id, project_id):
    await svc.propose_plan(run_id, goal=f"goal {run_id}", project_id=project_id)
    await svc.approve_plan(run_id, svc.runs[run_id]["plan_id"], "u-test")


async def _audit(factory):
    async with factory() as db:
        rows = (await db.execute(text(
            "SELECT target_id, detail FROM audit_log WHERE action='workspace.run_refused'"
        ))).all()
    return [(r.target_id, json.loads(r.detail)) for r in rows]


async def _queued(factory):
    return [r["run_id"] for r in await get_queue_snapshot(factory) if r["state"] == "queued"]


@pytest.fixture
async def env(tmp_path):
    engine, factory = await _make(tmp_path)
    await _seed(factory, {"p-free": (None, None), "p-active": ("ws-active", "active"),
                          "p-paused": ("ws-paused", "paused"),
                          "p-arch": ("ws-arch", "archived"),
                          "p-del": ("ws-del", "deleted")})
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
    assert await workspace_block(f, "p-free") is None
    assert await workspace_block(f, "p-active") is None
    assert await workspace_block(f, "nope") is None
    assert await workspace_block(f, None) is None
    assert await workspace_block(None, "p-arch") is None
    for pid, wid, state in (("p-paused", "ws-paused", "paused"), ("p-arch", "ws-arch", "archived"),
                            ("p-del", "ws-del", "deleted")):
        assert await workspace_block(f, pid) == (wid, state)


@pytest.mark.parametrize("pid", ["p-paused", "p-arch", "p-del"])
async def test_start_refused_with_409_code_and_audited(env, pid):
    svc, f = env
    await _approve(svc, "r1", pid)
    with pytest.raises(WorkspaceNotActiveError) as ei:
        await svc.start_execution("r1")
    assert ei.value.status_code == 409
    assert ei.value.detail["error"]["code"] == CODE == "WORKSPACE_NOT_ACTIVE"
    assert await _queued(f) == []  # never enqueued
    ((target, detail),) = await _audit(f)
    assert detail["runId"] == "r1" and detail["projectId"] == pid and detail["where"] == "start"
    assert target == ei.value.workspace_id


async def test_active_and_workspaceless_projects_start_as_before(env):
    svc, f = env
    for rid, pid in (("ok-1", "p-free"), ("ok-2", "p-active")):
        await _approve(svc, rid, pid)
        await svc.start_execution(rid)
    assert sorted(await _queued(f)) == ["ok-1", "ok-2"]
    assert await _audit(f) == []


async def test_queued_run_cannot_start_after_archive(env):
    svc, f = env
    await _approve(svc, "q1", "p-active")
    await svc.start_execution("q1")
    await _approve(svc, "q2", "p-free")
    await svc.start_execution("q2")
    await _set_state(f, "ws-active", "archived")  # archive AFTER queueing
    assert await svc.poll_once() is None  # FIFO: q1 is claimed first and refused
    assert await svc.poll_once() == "q2"  # the workspace-less project still runs
    assert await _queued(f) == []  # claim row was finished, not left to spin
    states = {r["run_id"]: r["state"] for r in await get_queue_snapshot(f)}
    assert states.get("q1") != "running"
    ((_, detail),) = await _audit(f)
    assert detail["runId"] == "q1" and detail["where"] == "claim"
    await asyncio.sleep(0.05)
    assert "q1" not in svc.engine.calls


async def test_batch_claim_path_also_gated(env):
    svc, f = env
    for i, pid in enumerate(["p-active", "p-free", "p-active"]):
        await _approve(svc, f"b{i}", pid)
        await svc.start_execution(f"b{i}")
    await _set_state(f, "ws-active", "paused")
    assert await svc.poll_once(max_claims=5) == ["b1"]


async def test_restore_reopens_the_gate(env):
    svc, f = env
    await _approve(svc, "re1", "p-arch")
    with pytest.raises(WorkspaceNotActiveError):
        await svc.start_execution("re1")
    await _set_state(f, "ws-arch", "active")
    await svc.start_execution("re1")
    assert await _queued(f) == ["re1"]


async def test_race_archive_vs_start_never_runs_after_archive(tmp_path):
    """Whatever the interleaving, once the archive commits no run may spawn."""
    engine, f = await _make(tmp_path)
    try:
        await _seed(f, {"p-race": ("ws-race", "active")})
        svc = OrchestratorService(engine=StubEngine(), session_factory=f)
        n = 12
        for i in range(n):
            await _approve(svc, f"race-{i}", "p-race")

        async def starts():
            out = []
            for i in range(n):
                try:
                    await svc.start_execution(f"race-{i}")
                    out.append("started")
                except WorkspaceNotActiveError:
                    out.append("refused")
                await asyncio.sleep(0)
            return out

        async def archive():
            await asyncio.sleep(0.01)
            await _set_state(f, "ws-race", "archived")

        results, _ = await asyncio.gather(starts(), archive())
        # after the archive has committed, claiming must admit nothing
        assert await svc.poll_once(max_claims=n) == []
        assert await _queued(f) == []
        await asyncio.sleep(0.05)
        assert svc.engine.calls == []
        assert len(await _audit(f)) >= results.count("refused")
        svc.shutdown()
    finally:
        await engine.dispose()


async def test_schema_without_workspaces_is_unchanged_behavior(tmp_path):
    engine, f = await _make(tmp_path, _NO_WS_DDL.replace(
        "CREATE TABLE workspaces", "CREATE TABLE ws_unused"))
    try:
        async with f() as db:
            await db.execute(text("INSERT INTO projects (id, team_id) VALUES ('p1', 'team-a')"))
            await db.commit()
        assert await workspace_block(f, "p1") is None
        svc = OrchestratorService(engine=StubEngine(), session_factory=f)
        await _approve(svc, "legacy", "p1")
        await svc.start_execution("legacy")
        assert await _queued(f) == ["legacy"]
        svc.shutdown()
    finally:
        await engine.dispose()
