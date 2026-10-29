"""W15 slice 1 — the claim statement must not let one team hold the whole pool.

Today a team's queued work is ordered globally by age, so a team with four
queued runs takes all four worker slots before a second team's older-by-one
run is even considered. Fairness is ordering, not preemption: among the
candidates that fit their team's cap, a team holding fewer running claims goes
first.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.run_queue import claim_next_run

DDL_PLAIN = (
    "CREATE TABLE projects (id TEXT PRIMARY KEY, team_id TEXT NOT NULL)",
    "CREATE TABLE run_claims (run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
    "state TEXT NOT NULL, claimed_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, "
    "worker_id TEXT NOT NULL)",
)
DDL_CONFIGURABLE = (
    *DDL_PLAIN,
    "CREATE TABLE billing_plans (id TEXT PRIMARY KEY, team_id TEXT UNIQUE, "
    "concurrent_runs_override INTEGER)",
)


async def _factory(ddl, rows):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for statement in ddl:
            await conn.execute(text(statement))
        await conn.execute(text("INSERT INTO projects VALUES ('p1','t1'), ('p2','t2')"))
        for run_id, project_id, state, at in rows:
            await conn.execute(
                text(
                    "INSERT INTO run_claims VALUES "
                    "(:r, :p, :s, :at, :at, 'enqueuer')"
                ),
                {"r": run_id, "p": project_id, "s": state, "at": at},
            )
    return async_sessionmaker(engine, expire_on_commit=False), engine


def _queued_busy_then_idle():
    """Five queued for t1 (oldest), one queued for t2 (newest)."""
    rows = [
        (f"a{i}", "p1", "queued", f"2026-10-29T00:00:0{i}+00:00") for i in range(1, 6)
    ]
    rows.append(("b1", "p2", "queued", "2026-10-29T00:00:09+00:00"))
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("ddl", [DDL_PLAIN, DDL_CONFIGURABLE], ids=["plain", "configurable"])
async def test_second_team_is_not_starved_by_a_long_queue(ddl):
    factory, engine = await _factory(ddl, _queued_busy_then_idle())
    try:
        claims = [await claim_next_run(factory, "w", max_per_team=4) for _ in range(4)]
        claimed = [c["run_id"] for c in claims]
        assert claimed == ["a1", "b1", "a2", "a3"], claimed
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("ddl", [DDL_PLAIN, DDL_CONFIGURABLE], ids=["plain", "configurable"])
async def test_per_team_cap_still_blocks_a_saturated_team(ddl):
    rows = [
        (f"run{i}", "p1", "running", f"2026-10-29T00:00:0{i}+00:00") for i in range(4)
    ]
    rows += [
        ("a-next", "p1", "queued", "2026-10-29T00:01:00+00:00"),
        ("b-next", "p2", "queued", "2026-10-29T00:01:01+00:00"),
    ]
    factory, engine = await _factory(ddl, rows)
    try:
        claim = await claim_next_run(factory, "w", max_per_team=4)
        assert claim["run_id"] == "b-next"
    finally:
        await engine.dispose()
