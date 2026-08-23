import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.run_queue import (
    claim_next_run,
    enqueue_run,
    finish_run,
    get_queue_snapshot,
    heartbeat_run,
    reclaim_orphans,
)
from harness_orchestrator.service import OrchestratorService

DDL = """
CREATE TABLE projects (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL
);
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL
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

_ENGINE = None
_FACTORY = None


@pytest.fixture
def db_factory(tmp_path):
    global _ENGINE, _FACTORY

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rq.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for statement in DDL.split(";"):
                stmt = statement.strip()
                if stmt:
                    await conn.execute(text(stmt))
            await conn.execute(
                text(
                    "INSERT INTO projects (id, team_id) VALUES "
                    "('proj-a', 'team-a'), ('proj-b', 'team-b')"
                )
            )

    run(_setup())
    _ENGINE = engine
    _FACTORY = factory
    return factory


_LOOP = None


def run(coro):
    global _LOOP
    if _LOOP is None:
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


def test_enqueue_claim_roundtrip(db_factory):
    f = db_factory
    assert run(enqueue_run(f, "r1", "proj-a")) is True
    assert run(enqueue_run(f, "r1", "proj-a")) is False
    claim = run(claim_next_run(f, "w1", max_per_team=4))
    assert claim == {"run_id": "r1", "project_id": "proj-a"}
    assert run(get_queue_snapshot(f))[0]["state"] == "running"


def test_fifo_order(db_factory):
    f = db_factory
    run(enqueue_run(f, "old", "proj-a"))
    import time

    time.sleep(0.01)
    run(enqueue_run(f, "new", "proj-a"))
    first = run(claim_next_run(f, "w1", 4))
    second = run(claim_next_run(f, "w1", 4))
    assert [first["run_id"], second["run_id"]] == ["old", "new"]


def test_team_cap(db_factory):
    f = db_factory
    for rid in ("a1", "a2"):
        run(enqueue_run(f, rid, "proj-a"))
        run(claim_next_run(f, "w1", max_per_team=2))
    run(enqueue_run(f, "a3", "proj-a"))
    run(enqueue_run(f, "b1", "proj-b"))
    assert run(claim_next_run(f, "w1", max_per_team=2))["run_id"] == "b1"
    assert [row for row in run(get_queue_snapshot(f)) if row["state"] == "queued"][
        0
    ]["run_id"] == "a3"


def test_heartbeat_worker_guard(db_factory):
    f = db_factory
    run(enqueue_run(f, "r1", "proj-a"))
    run(claim_next_run(f, "w1", 4))
    assert run(heartbeat_run(f, "r1", "wrong")) is False
    before = run(get_queue_snapshot(f))[0]["heartbeat_at"]
    assert run(heartbeat_run(f, "r1", "w1")) is True
    after = run(get_queue_snapshot(f))[0]["heartbeat_at"]
    assert after >= before


def test_finish_deletes(db_factory):
    f = db_factory
    run(enqueue_run(f, "r1", "proj-a"))
    run(finish_run(f, "r1"))
    assert run(get_queue_snapshot(f)) == []


def test_reclaim_orphans(db_factory):
    f = db_factory
    run(enqueue_run(f, "stale-run", "proj-a"))
    run(claim_next_run(f, "w1", 4))
    async def _age():
        from sqlalchemy import text as t

        async with f() as db:
            await db.execute(
                t("UPDATE run_claims SET heartbeat_at = :h WHERE run_id = :r"),
                {"h": "2020-01-01T00:00:00+00:00", "r": "stale-run"},
            )
            await db.commit()

    asyncio.new_event_loop().run_until_complete(_age())
    run(enqueue_run(f, "fresh-run", "proj-a"))
    run(claim_next_run(f, "w1", 4))
    orphans = run(reclaim_orphans(f, stale_seconds=90))
    assert orphans == ["stale-run"]
    states = {row["run_id"]: row["state"] for row in run(get_queue_snapshot(f))}
    assert states["stale-run"] == "orphaned"
    assert states["fresh-run"] == "running"
    assert run(enqueue_run(f, "stale-run", "proj-a")) is True
    assert run(claim_next_run(f, "w2", 4))["run_id"] == "stale-run"


def test_service_enqueue_then_poll_completes(db_factory):
    f = db_factory

    class StubEngine:
        def execute_goal(self, goal=None, session_id=None):
            return {
                "sessionId": session_id,
                "engine": "stub",
                "status": "completed",
                "finalResponse": "done",
            }

    svc = OrchestratorService(engine=StubEngine(), session_factory=f)
    run(svc.propose_plan("svc-run", project_id="proj-a"))
    plan_id = svc.runs["svc-run"]["plan_id"]
    run(svc.approve_plan("svc-run", plan_id, "u-test"))
    run(svc.start_execution("svc-run"))
    snap = run(get_queue_snapshot(f))
    assert len(snap) == 1 and snap[0]["state"] == "queued"
    assert svc.runs["svc-run"].get("task") is None
    async def _wait():
        await asyncio.sleep(0.1)
        claimed = await svc.poll_once()
        assert claimed == "svc-run"
        task = svc.runs["svc-run"]["task"]
        if task is not None and not task.done():
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
    run(_wait())
    assert run(get_queue_snapshot(f)) == []
    assert svc.runs["svc-run"]["engine_result"]["status"] == "completed"


def test_bare_mode_still_spawns_directly():
    class StubEngine:
        def execute_goal(self, goal=None, session_id=None):
            return {"status": "completed", "finalResponse": "ok", "engine": "stub"}

    svc = OrchestratorService(engine=StubEngine())
    run(svc.propose_plan("bare-run"))
    run(svc.approve_plan("bare-run", svc.runs["bare-run"]["plan_id"], "u-test"))
    run(svc.start_execution("bare-run"))
    task = svc.runs["bare-run"]["task"]
    run(asyncio.wait_for(task, timeout=5))
    assert task is not None and task.done()
