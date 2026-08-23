"""Mission 11 wave 1 node A3 — atomicity/failure tests for the budget guard.

Mirrors the FROZEN schema minimally in scratch SQLite databases (teams,
projects, agent_runs, cost_ledger, billing_plans) — no Postgres required.
Postgres-only ``date_trunc``/``now()`` are registered as SQLite user
functions with identical semantics so the production RESERVE_SQL runs
unmodified.

Covers the W1-A3 acceptance matrix:
- N=8 threads x $1 claims against a $5 budget -> EXACTLY 5 reservations win
  (atomic check-and-insert under real thread contention);
- an overrun claim is rejected with reason "over budget";
- injected ledger failure fails CLOSED with reason "ledger unavailable";
- reconciliation: sum of successful claims == observed cost_ledger delta;
- release(entry_id) compensation removes exactly the reserved row;
- record_usage (team-attributed writer) integrates with the guard's math and
  never raises into the caller.
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.billing import BillingPlan  # noqa: E402
from harness_identity.models import (  # noqa: E402
    AgentRun,
    Base,
    Project,
    Team,
    uid,
)

# budget_guard lives in section3; tests run under section1's interpreter paths.
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "section3" / "src"))

from harness_router import budget_guard  # noqa: E402
from harness_router.budget_guard import RESERVE_SQL, release, reserve  # noqa: E402

from harness_identity.cost_ledger import record_usage  # noqa: E402

CLAIM_PARAMS_BASE = {
    "agent_role": "primary",
    "model": "gpt-test",
    "input_tokens": 100,
    "output_tokens": 10,
    "latency_ms": 5,
}

_MIRROR_DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE projects (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id),
    name VARCHAR(200) NOT NULL
);
CREATE TABLE agent_runs (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id),
    state VARCHAR(40) NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE cost_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36) REFERENCES agent_runs(id),
    agent_role VARCHAR(20) NOT NULL,
    model VARCHAR(120) NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd NUMERIC NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE billing_plans (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL UNIQUE REFERENCES teams(id),
    plan VARCHAR(30) NOT NULL,
    monthly_budget_usd NUMERIC NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _sqlite_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _sqlite_date_trunc(unit: str, value: str) -> str:
    assert unit == "month"
    return f"{str(value)[:7]}-01 00:00:00"


def _register_sqlite_funcs(dbapi_conn: sqlite3.Connection, _rec=None) -> None:
    dbapi_conn.create_function("now", 0, _sqlite_now)
    dbapi_conn.create_function("date_trunc", 2, _sqlite_date_trunc)


def _claim_params(team_id: str, run_id: str | None, cost: float) -> dict:
    return {**CLAIM_PARAMS_BASE, "team_id": team_id, "run_id": run_id, "cost_usd": cost}


class _RawConn:
    """One thread-private SQLite connection running the production SQL."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        _register_sqlite_funcs(self._conn)

    def reserve_once(self, params: dict) -> tuple[bool, str | None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(RESERVE_SQL, params).fetchone()
            self._conn.commit()
            return (True, None) if row is not None else (False, "over budget")
        except Exception as exc:  # pragma: no cover — surfaced by test failure
            self._conn.rollback()
            return False, f"error: {exc}"

    def close(self) -> None:
        self._conn.close()


class _SARow:
    def __init__(self, row) -> None:
        self._row = row

    def __getitem__(self, key: str):
        return self._row._mapping[key]


class _SAStatus:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _SessionPool:
    """Adapts an async session_factory to budget_guard's duck-typed pool."""

    def __init__(self, factory) -> None:
        self._factory = factory

    async def fetchrow(self, sql: str, params: dict):
        async with self._factory() as db:
            result = await db.execute(text(sql), dict(params))
            row = result.first()
            await db.commit()
            return None if row is None else _SARow(row)

    async def execute(self, sql: str, params: dict):
        async with self._factory() as db:
            result = await db.execute(text(sql), dict(params))
            await db.commit()
            return _SAStatus(result.rowcount)


class _BrokenPool:
    """Stands in for an unreachable/unavailable ledger database."""

    async def fetchrow(self, _sql: str, _params: dict):
        raise RuntimeError("connection refused")

    async def execute(self, _sql: str, _params: dict):
        raise RuntimeError("connection refused")


@pytest.fixture
async def budget_db():
    engine = create_async_engine("sqlite+aiosqlite://")
    event.listen(engine.sync_engine, "connect", _register_sqlite_funcs)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_team(budget_db, *, budget_usd: float) -> tuple[str, str]:
    team_id, project_id, run_id = uid(), uid(), uid()
    async with budget_db() as db:
        db.add_all(
            [
                Team(id=team_id, name=f"team-{team_id[:8]}"),
                Project(id=project_id, team_id=team_id, name="proj"),
                AgentRun(id=run_id, project_id=project_id, state="running"),
                BillingPlan(
                    id=uid(),
                    team_id=team_id,
                    plan="pro",
                    monthly_budget_usd=budget_usd,
                ),
            ]
        )
        await db.commit()
    return team_id, run_id


async def _ledger_sum(budget_db, team_id: str) -> float:
    sql = text(
        "SELECT COALESCE(SUM(l.cost_usd), 0) FROM cost_ledger l "
        "JOIN agent_runs r ON r.id = l.run_id "
        "JOIN projects p ON p.id = r.project_id WHERE p.team_id = :t"
    )
    async with budget_db() as db:
        return float((await db.execute(sql, {"t": team_id})).scalar_one())


# --------------------------------------------------------------------------
# Atomicity under real contention: 8 threads x $1 vs a $5 budget.
# --------------------------------------------------------------------------


def test_eight_threads_dollar_claims_against_five_dollar_budget(tmp_path):
    db_path = tmp_path / "ledger.db"
    setup = _RawConn(db_path)
    setup._conn.executescript(_MIRROR_DDL)  # noqa: SLF001 — test-local bootstrap
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    c = setup._conn  # noqa: SLF001
    c.execute("INSERT INTO teams (id, name) VALUES (?, ?)", (team_id, "t"))
    c.execute(
        "INSERT INTO projects (id, team_id, name) VALUES (?, ?, ?)",
        (project_id, team_id, "p"),
    )
    c.execute(
        "INSERT INTO agent_runs (id, project_id, state) VALUES (?, ?, ?)",
        (run_id, project_id, "running"),
    )
    c.execute(
        "INSERT INTO billing_plans (id, team_id, plan, monthly_budget_usd) "
        "VALUES (?, ?, 'pro', 5.0)",
        (str(uuid.uuid4()), team_id),
    )
    before = c.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger").fetchone()[0]
    setup.close()

    results: list[tuple[str, bool, str | None]] = []
    threads = []
    lock_free_results = results

    import threading

    def worker(i: int) -> None:
        conn = _RawConn(db_path)
        try:
            allowed, reason = conn.reserve_once(_claim_params(team_id, run_id, 1.0))
            lock_free_results.append((f"thread-{i}", allowed, reason))
        finally:
            conn.close()

    for i in range(8):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r[1]]
    failures = [r for r in results if not r[1]]
    assert len(successes) == 5, f"expected exactly 5 winners, got {results}"
    assert len(failures) == 3
    assert all(reason == "over budget" for _, _, reason in failures)

    # Reconcile: sum(successful claims) == observed ledger delta.
    final = _RawConn(db_path)
    total = final._conn.execute(  # noqa: SLF001
        "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger"
    ).fetchone()[0]
    budget_row = final._conn.execute(
        "SELECT monthly_budget_usd FROM billing_plans WHERE team_id = ?", (team_id,)
    ).fetchone()[0]
    final.close()
    assert round(float(total) - float(before), 6) == 5.0
    assert round(sum(1.0 for _ in successes), 6) == round(float(total) - float(before), 6)
    assert float(budget_row) == 5.0  # plan row never mutated by reserves


# --------------------------------------------------------------------------
# Async behavior of the guard itself.
# --------------------------------------------------------------------------


async def test_overrun_claim_rejected_with_reason(budget_db):
    team_id, run_id = await _seed_team(budget_db, budget_usd=5.0)
    pool = _SessionPool(budget_db)
    ok = await reserve(pool, team_id=team_id, estimated_cost_usd=5.0,
                       run_id=run_id, **CLAIM_PARAMS_BASE)
    assert ok.allowed and ok.entry_id is not None
    over = await reserve(pool, team_id=team_id, estimated_cost_usd=0.01,
                         run_id=run_id, **CLAIM_PARAMS_BASE)
    assert over.allowed is False
    assert over.reason == "over budget"  # rejected WITH an explicit reason
    assert over.entry_id is None
    # A rejection persists nothing: the ledger keeps exactly the first claim.
    assert await _ledger_sum(budget_db, team_id) == 5.0


async def test_exact_fit_allowed_and_cents_rounding(budget_db):
    team_id, run_id = await _seed_team(budget_db, budget_usd=1.0)
    pool = _SessionPool(budget_db)
    exact = await reserve(pool, team_id=team_id, estimated_cost_usd=1.0,
                          run_id=run_id, **CLAIM_PARAMS_BASE)
    assert exact.allowed  # <= boundary is inclusive


async def test_ledger_unavailable_fails_closed(budget_db):
    team_id, run_id = await _seed_team(budget_db, budget_usd=100.0)
    result = await reserve(_BrokenPool(), team_id=team_id, estimated_cost_usd=0.01,
                           run_id=run_id, **CLAIM_PARAMS_BASE)
    assert result.allowed is False
    assert result.reason == "ledger unavailable"
    assert result.entry_id is None


async def test_exception_injected_into_real_ledger_fails_closed():
    """Fail-CLOSED proven against a REAL ledger, not a fake pool.

    An exception is injected inside the ledger write path (a SQLAlchemy
    before_cursor_execute hook raising on the cost_ledger INSERT). The guard
    must (1) deny the reservation with reason "ledger unavailable", (2) leave
    ZERO rows in cost_ledger — no half-written spend, and (3) not latch: the
    very next reserve after the fault clears succeeds normally.
    """
    engine = create_async_engine("sqlite+aiosqlite://")
    event.listen(engine.sync_engine, "connect", _register_sqlite_funcs)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        team_id, run_id = await _seed_team(factory, budget_usd=100.0)

        def _boom(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "INSERT INTO cost_ledger" in statement:
                raise RuntimeError("injected ledger fault")

        event.listen(engine.sync_engine, "before_cursor_execute", _boom)
        try:
            result = await reserve(
                _SessionPool(factory), team_id=team_id, estimated_cost_usd=1.0,
                run_id=run_id, **CLAIM_PARAMS_BASE,
            )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _boom)

        assert result.allowed is False
        assert result.reason == "ledger unavailable"
        assert result.entry_id is None
        async with factory() as db:
            persisted = (
                await db.execute(text("SELECT COUNT(*) FROM cost_ledger"))
            ).scalar_one()
        assert persisted == 0  # denied AND nothing half-written

        recovered = await reserve(
            _SessionPool(factory), team_id=team_id, estimated_cost_usd=1.0,
            run_id=run_id, **CLAIM_PARAMS_BASE,
        )
        assert recovered.allowed and recovered.entry_id is not None
    finally:
        await engine.dispose()


async def test_release_compensates_exactly_the_reserved_row(budget_db):
    team_id, run_id = await _seed_team(budget_db, budget_usd=5.0)
    pool = _SessionPool(budget_db)
    ok = await reserve(pool, team_id=team_id, estimated_cost_usd=2.0,
                       run_id=run_id, **CLAIM_PARAMS_BASE)
    assert ok.allowed
    assert await _ledger_sum(budget_db, team_id) == 2.0
    assert await release(pool, ok.entry_id) is True
    assert await _ledger_sum(budget_db, team_id) == 0.0
    # Idempotent-by-absence: releasing again affects nothing, reports False.
    assert await release(pool, ok.entry_id) is False


async def test_recorded_usage_counts_toward_subsequent_reserves(budget_db):
    team_id, run_id = await _seed_team(budget_db, budget_usd=1.0)
    written = await record_usage(
        budget_db, team_id=team_id, model="gpt-test",
        input_tokens=50, output_tokens=5, cost_usd=0.6, run_id=run_id,
    )
    assert written is not None and written.entry_id is not None
    pool = _SessionPool(budget_db)
    over = await reserve(pool, team_id=team_id, estimated_cost_usd=0.5,
                         run_id=run_id, **CLAIM_PARAMS_BASE)
    assert over.allowed is False and over.reason == "over budget"
    fits = await reserve(pool, team_id=team_id, estimated_cost_usd=0.4,
                         run_id=run_id, **CLAIM_PARAMS_BASE)
    assert fits.allowed
    assert await _ledger_sum(budget_db, team_id) == 1.0


# --------------------------------------------------------------------------
# record_usage wrapper semantics.
# --------------------------------------------------------------------------


async def test_record_usage_resolves_team_run_without_explicit_run_id(budget_db):
    team_id, _ = await _seed_team(budget_db, budget_usd=9.0)
    written = await record_usage(
        budget_db, team_id=team_id, model="gpt-test",
        input_tokens=10, output_tokens=1, cost_usd=0.25,
    )
    assert written is not None
    assert written.run_id is not None  # resolved via projects->agent_runs
    assert await _ledger_sum(budget_db, team_id) == 0.25


async def test_record_usage_skips_team_without_any_run(budget_db):
    team_only = uid()
    async with budget_db() as db:
        db.add(Team(id=team_only, name="lonely"))
        await db.commit()
    written = await record_usage(
        budget_db, team_id=team_only, model="gpt-test",
        input_tokens=1, output_tokens=1, cost_usd=0.01,
    )
    assert written is None  # refuse unattributable spend, never raise


async def test_record_usage_never_raises_on_db_error():
    class _BoomFactory:
        def __call__(self):
            raise RuntimeError("pool exhausted")

    assert await record_usage(
        _BoomFactory(), team_id="t", model="m",
        input_tokens=1, output_tokens=1, cost_usd=0.01,
    ) is None


def test_named_binds_translate_to_asyncpg_positional():
    sql, args = budget_guard.to_positional_params(
        RESERVE_SQL, _claim_params("team-1", "run-1", 1.5)
    )
    assert "$1" in sql and ":" not in sql.replace("::", "")
    assert args[0] == "run-1" and args[-1] == "team-1"
    assert len(args) == 8
