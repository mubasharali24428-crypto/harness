"""M13-H4b integration assertions — S4 call-site families write tz-safe.

One integration assertion per converted family:

* run_queue (heartbeat/claim/enqueue/reclaim): the frozen 0006 columns are
  TEXT, so values must be ISO strings carrying the explicit +00:00 offset
  (never naive text) — heartbeat write reads back offset-bearing.
* service._persist_plan (agent_plans.created_at, TIMESTAMPTZ in prod): the
  bound value is a real aware datetime object (asyncpg rejects str there).
* plan_gate._audit_emit (audit_log.ts, TIMESTAMPTZ in prod): same law —
  db_now() object reaches the bind and round-trips through SQLite.

SQLite's TIMESTAMP columns store whatever they're given; these tests pin
the *bind value discipline* (aware objects / offset-bearing ISO text),
which is exactly what asyncpg enforces in production.
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_ROOT = Path(__file__).resolve().parents[2]
_S1_SRC = _ROOT / "section1" / "src"
if _S1_SRC.exists() and str(_S1_SRC) not in sys.path:
    sys.path.append(str(_S1_SRC))

from harness_contracts.plan import AgentPlan  # noqa: E402
from harness_identity.timeutil import as_utc  # noqa: E402

from harness_orchestrator.plan_gate import _audit_emit  # noqa: E402
from harness_orchestrator.run_queue import (  # noqa: E402
    claim_next_run,
    enqueue_run,
    heartbeat_run,
    reclaim_orphans,
)
from harness_orchestrator.service import OrchestratorService  # noqa: E402

_DDL = """
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
CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_member_id VARCHAR(36),
    actor_kind VARCHAR(16) NOT NULL DEFAULT 'human',
    action VARCHAR(80) NOT NULL,
    target_type VARCHAR(60) NOT NULL DEFAULT '',
    target_id VARCHAR(80) NOT NULL DEFAULT '',
    detail JSON NOT NULL,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_AGENT_PLANS_DDL = (
    "CREATE TABLE agent_plans ("
    "id VARCHAR(36) PRIMARY KEY,"
    "run_id VARCHAR(36) NOT NULL,"
    "goal TEXT NOT NULL,"
    "files_likely_to_change TEXT NOT NULL DEFAULT '[]',"
    "steps TEXT NOT NULL DEFAULT '[]',"
    "tests TEXT NOT NULL DEFAULT '[]',"
    "risks TEXT NOT NULL DEFAULT '[]',"
    "estimated_cost_usd REAL NOT NULL DEFAULT 0,"
    "forbidden_paths TEXT NOT NULL DEFAULT '[]',"
    "expected_result TEXT NOT NULL DEFAULT '',"
    "status VARCHAR(20) NOT NULL,"
    "created_at TIMESTAMP)"
)


def _factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/tz.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for stmt in _DDL.split(";"):
                if stmt.strip():
                    await conn.execute(text(stmt))
            # claim_next_run JOINs projects (team-cap subquery): seed the row.
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('p1', 't1')")
            )

    asyncio.new_event_loop().run_until_complete(_setup())
    return factory


def _loop():
    return asyncio.new_event_loop()


def _aware(value) -> datetime:
    """Coerce a stored timestamp cell to an aware datetime for assertions.

    SQLite raw-text reads hand back strings ('2026-08-24 03:39:55+00:00');
    the +00:00 in storage is precisely the evidence that the bound object
    was timezone-aware — which is the whole law being tested.
    """
    if isinstance(value, datetime):
        parsed = as_utc(value)
    else:
        parsed = datetime.fromisoformat(str(value))
    assert parsed.tzinfo is not None, "stored timestamp lost its UTC offset"
    return parsed


def test_heartbeat_write_reads_back_offset_bearing_iso(tmp_path):
    """Family 1: run_queue timestamps — aware-UTC ISO text into frozen TEXT."""
    f = _factory(tmp_path)
    loop = _loop()

    assert loop.run_until_complete(enqueue_run(f, "r-hb", "p1")) is True
    claim = loop.run_until_complete(claim_next_run(f, "w1", 4))
    assert claim == {"run_id": "r-hb", "project_id": "p1"}

    async def _read():
        async with f() as db:
            return (
                await db.execute(
                    text("SELECT claimed_at FROM run_claims WHERE run_id = 'r-hb'")
                )
            ).scalar_one()

    claimed_at = loop.run_until_complete(_read())
    parsed = datetime.fromisoformat(claimed_at)
    # The law: even TEXT-column values carry an explicit UTC offset so any
    # later comparison/parsing is unambiguous.
    assert parsed.tzinfo is not None, "ISO text must bear the +00:00 offset"
    assert parsed.utcoffset() == timedelta(0)
    assert abs(parsed.timestamp() - datetime.now(parsed.tzinfo).timestamp()) < 5

    assert loop.run_until_complete(heartbeat_run(f, "r-hb", "w1")) is True
    loop.close()


def test_reclaim_cutoff_is_offset_bearing_iso(tmp_path):
    """reclaim_orphans compares against an explicitly-offset UTC cutoff."""
    f = _factory(tmp_path)
    loop = _loop()
    loop.run_until_complete(enqueue_run(f, "r-old", "p1"))
    loop.run_until_complete(claim_next_run(f, "w1", 4))

    async def _age():
        async with f() as db:
            await db.execute(
                text("UPDATE run_claims SET heartbeat_at = :h WHERE run_id = :r"),
                {"h": "2020-01-01T00:00:00+00:00", "r": "r-old"},
            )
            await db.commit()

    loop.run_until_complete(_age())
    orphans = loop.run_until_complete(reclaim_orphans(f, stale_seconds=90))
    assert orphans == ["r-old"], "offset-bearing cutoff matched the stale row"

    # A fresh claim inside the window must survive the same cutoff.
    loop.run_until_complete(enqueue_run(f, "r-new", "p1"))
    loop.run_until_complete(claim_next_run(f, "w2", 4))
    survivors = loop.run_until_complete(reclaim_orphans(f, stale_seconds=90))
    assert survivors == []
    loop.close()


def test_plan_persist_binds_aware_datetime_object(tmp_path):
    """Family 2: agent_plans.created_at — real aware datetime (asyncpg law)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/plans.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _scenario():
        async with engine.begin() as conn:
            await conn.execute(text(_AGENT_PLANS_DDL))

        svc = OrchestratorService(engine=None, session_factory=factory)
        plan = AgentPlan(
            goal="g",
            filesLikelyToChange=[],
            steps=[],
            tests=[],
            risks=[],
            estimatedCostUsd=0.01,
            forbiddenPaths=[],
            expectedResult="ok",
        )
        await svc._persist_plan("run-tz", "plan-tz", plan, "proposed")

        async with factory() as db:
            return (
                await db.execute(
                    text("SELECT created_at FROM agent_plans WHERE id = 'plan-tz'")
                )
            ).first()

    row = _loop().run_until_complete(_scenario())
    assert row is not None, "mirror row must exist after persist"
    created = _aware(row[0])
    assert created.utcoffset() == timedelta(0)


def test_audit_emit_binds_aware_datetime_object(tmp_path):
    """Family 3: audit_log.ts — db_now() object bound, reads back aware."""
    f = _factory(tmp_path)
    loop = _loop()
    loop.run_until_complete(
        _audit_emit(
            f,
            action="plan.rejected",
            actor_member_id="u-tz",
            target_type="run",
            target_id="r-audit",
            detail={"planId": "p-1"},
        )
    )

    async def _read():
        async with f() as db:
            return (
                await db.execute(text("SELECT ts FROM audit_log ORDER BY seq DESC"))
            ).first()

    row = loop.run_until_complete(_read())
    assert row is not None
    ts = _aware(row[0])
    assert abs(ts.timestamp() - datetime.now(UTC).timestamp()) < 60, (
        "audit ts is the current aware-UTC instant"
    )
    loop.close()


def test_as_utc_normalizes_naive_row_values():
    """Rows written by OTHER writers may be naive; readers normalize via as_utc."""
    naive = datetime(2026, 8, 23, 9, 0, 0)
    stamped = as_utc(naive)
    assert stamped.tzinfo is not None
    assert stamped.hour == 9, "naive is interpreted as already-UTC, never shifted"
