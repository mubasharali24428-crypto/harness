"""Per-team administrator concurrency limits feed the atomic claim statement."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.run_queue import claim_next_run


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for ddl in (
            "CREATE TABLE projects (id TEXT PRIMARY KEY, team_id TEXT NOT NULL)",
            "CREATE TABLE billing_plans (id TEXT PRIMARY KEY, team_id TEXT UNIQUE, "
            "concurrent_runs_override INTEGER)",
            "CREATE TABLE run_claims (run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
            "state TEXT NOT NULL, claimed_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, "
            "worker_id TEXT NOT NULL)",
        ):
            await conn.execute(text(ddl))
        await conn.execute(text("INSERT INTO projects VALUES ('p1','t1')"))
        await conn.execute(text("INSERT INTO billing_plans VALUES ('b1','t1',1)"))
        await conn.execute(
            text(
                "INSERT INTO run_claims VALUES "
                "('r-running','p1','running','2026-01-01','2026-01-01','w1'), "
                "('r-queued','p1','queued','2026-01-02','2026-01-02','enqueuer')"
            )
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_team_override_limits_claims_and_admin_increase_takes_effect(factory):
    assert await claim_next_run(factory, "worker", max_per_team=4) is None
    async with factory() as db:
        await db.execute(
            text("UPDATE billing_plans SET concurrent_runs_override=2 WHERE team_id='t1'")
        )
        await db.commit()
    claim = await claim_next_run(factory, "worker", max_per_team=4)
    assert claim == {"run_id": "r-queued", "project_id": "p1"}
