import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.budget import reserve_daily_run


async def _exercise(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for ddl in (
            "CREATE TABLE teams (id TEXT PRIMARY KEY)",
            "CREATE TABLE projects (id TEXT PRIMARY KEY, team_id TEXT NOT NULL)",
            "CREATE TABLE agent_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL)",
            "CREATE TABLE billing_plans (team_id TEXT PRIMARY KEY, plan TEXT NOT NULL, "
            "runs_per_day_override INTEGER, context_index_max_chunks_override INTEGER)",
            "CREATE TABLE team_daily_run_reservations (team_id TEXT NOT NULL, run_id TEXT "
            "PRIMARY KEY, reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        ):
            await conn.execute(text(ddl))
        await conn.execute(text("INSERT INTO teams VALUES ('t')"))
        await conn.execute(text("INSERT INTO projects VALUES ('p','t')"))
        await conn.execute(text("INSERT INTO billing_plans VALUES ('t','free',1,NULL)"))
        await conn.execute(text("INSERT INTO agent_runs VALUES ('r1','p'),('r2','p')"))

    first = await reserve_daily_run(factory, "t", "r1")
    duplicate = await reserve_daily_run(factory, "t", "r1")
    second = await reserve_daily_run(factory, "t", "r2")
    await engine.dispose()
    return first, duplicate, second


def test_daily_cap_reservation_is_atomic_and_idempotent(tmp_path):
    first, duplicate, second = asyncio.run(_exercise(tmp_path / "daily.sqlite"))
    assert first.allowed is True
    assert duplicate.allowed is True
    assert second.allowed is False
    assert second.capHit["upgrade"]["cap"] == 1
