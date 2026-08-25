"""M13 Wave 3 lane H4a — NUMERIC(12,6) money integrity (H-023: float money dies).

Pins, in order:
1. Every mapped money column is Numeric(12,6) with Decimal typing (no
   ``Mapped[float]`` money survives in Section 1 models);
2. migration 0020 widens every spend column with USING casts (append-only,
   never editing 0001/0010);
3. Decimal values round-trip the ORM exactly;
4. writers bind money as exact base-10 text (never binary floats);
5. the budget guard's decisions agree with exact Decimal arithmetic;
6. ON A LIVE POSTGRES (opt-in via HARNESS_TEST_PG, mirroring the f1 probe
   convention): the full migration chain lands (12,6) columns, 0020 is
   idempotent, and the 0.1+0.2 class bug is impossible — including the
   aggregate level, where SQLite's REAL math physically cannot comply.

The brief's example magnitude (1000000.000001 + 0.000001) OVERFLOWS
NUMERIC(12,6) (max 999999.999999 — verified live); these tests use legal
maximum-scale magnitudes with identical fractional structure.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Numeric, event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:  # pragma: no cover — typing only
    import asyncpg

import harness_identity.billing as billing_mod
import harness_identity.models as models_mod
from harness_identity.billing import BillingPlan
from harness_identity.cost_ledger import record_usage
from harness_identity.models import (
    AgentPlanRecord,
    AgentRun,
    Base,
    CostLedger,
    Project,
    Team,
    uid,
)

# budget_guard lives in section3 (same interpreter-path dance as the atomicity
# suite); deploy/runners supplies the canonical migration splitter.
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "section3" / "src"))
sys.path.insert(0, str(_ROOT / "deploy" / "runners"))

from harness_router.budget_guard import release, reserve  # noqa: E402

# ---------------------------------------------------------------- constants

_MONEY_COLUMNS = [
    # (table, model class, column attribute)
    ("agent_plans", models_mod.AgentPlanRecord, "estimated_cost_usd"),
    ("cost_ledger", models_mod.CostLedger, "cost_usd"),
    ("billing_plans", billing_mod.BillingPlan, "monthly_budget_usd"),
]


def _migration(name: str) -> str:
    return (_ROOT / "section1" / "migrations" / name).read_text()


# ------------------------------------------------- 1. ORM typing sweep


@pytest.mark.parametrize(("table", "cls", "col"), _MONEY_COLUMNS)
def test_money_columns_are_numeric_12_6(table, cls, col):
    column = cls.__table__.columns[col]
    assert isinstance(column.type, Numeric), f"{table}.{col} lost its Numeric type"
    assert column.type.precision == 12, f"{table}.{col} precision"
    assert column.type.scale == 6, f"{table}.{col} scale"


def test_no_float_typed_money_column_survives_in_s1_models():
    """H-023 sweep law: no ``Mapped[float]`` over Numeric remains."""
    for mod in (models_mod, billing_mod):
        src = Path(str(mod.__file__)).read_text()
        assert "Mapped[float] = mapped_column(Numeric" not in src, (
            f"{mod.__name__} still maps a money column as float"
        )


def test_migration_0020_targets_every_spend_column_with_using_cast():
    body = _migration("0020_money_numeric.sql")
    for table, col in [
        ("agent_plans", "estimated_cost_usd"),
        ("cost_ledger", "cost_usd"),
        ("teams", "budget_usd"),
        ("billing_plans", "monthly_budget_usd"),
    ]:
        assert f"ALTER TABLE {table}" in body, f"0020 misses {table}"
        assert col in body, f"0020 misses {table}.{col}"
    assert body.count("USING") >= 4, "every widening must carry a USING cast"
    assert "NUMERIC(12,2)" not in body


def test_frozen_migrations_still_declare_original_types():
    """0010 keeps its historical (12,2) text — 0020 supersedes, never rewrites."""
    assert "monthly_budget_usd NUMERIC(12,2)" in _migration("0010_billing_plans.sql")
    assert "budget_usd NUMERIC(12,2)" in _migration("0007_team_budget.sql")


# ------------------------------------------- 2. Decimal round-trips (sqlite)


@pytest.fixture
async def money_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_attribution(factory) -> tuple[str, str]:
    """team -> project -> run chain; returns (team_id, run_id)."""
    team_id, run_id = uid(), uid()
    async with factory() as db:
        db.add(Team(id=team_id, name="money-team"))
        await db.flush()
        project = Project(team_id=team_id, name="p")
        db.add(project)
        await db.flush()
        db.add(AgentRun(id=run_id, project_id=project.id, state="running"))
        await db.commit()
    return team_id, run_id


async def test_cost_ledger_decimal_round_trips_exactly(money_factory):
    _, run_id = await _seed_attribution(money_factory)
    async with money_factory() as db:
        db.add(
            CostLedger(
                run_id=run_id,
                agent_role="primary",
                model="deepseek/deepseek-chat",
                input_tokens=1,
                output_tokens=1,
                cost_usd=Decimal("167777.777777"),
                latency_ms=1,
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()

    # ORM-typed read: Numeric coerces back to an exact Decimal.
    async with money_factory() as db:
        obj = (await db.execute(select(CostLedger))).scalars().first()
        assert obj is not None
        assert isinstance(obj.cost_usd, Decimal)
        assert obj.cost_usd == Decimal("167777.777777")
        # Raw-SQL read may come back as whatever SQLite stored; the VALUE must
        # be exact either way.
        raw = (await db.execute(text("SELECT cost_usd FROM cost_ledger"))).scalar_one()
        assert Decimal(str(raw)) == Decimal("167777.777777")


async def test_agent_plan_estimated_cost_round_trips_as_decimal(money_factory):
    team_id, run_id = await _seed_attribution(money_factory)
    assert team_id and run_id
    async with money_factory() as db:
        db.add(
            AgentPlanRecord(
                run_id=run_id,
                goal="g",
                estimated_cost_usd=Decimal("0.123456"),
            )
        )
        await db.commit()
    async with money_factory() as db:
        got = (
            await db.execute(text("SELECT estimated_cost_usd FROM agent_plans"))
        ).scalar_one()
        # Raw-SQL reads may surface SQLite's stored REAL; the VALUE is what
        # must be exact.
        assert Decimal(str(got)) == Decimal("0.123456")


# ------------------------------- 3. Writers bind exact base-10 text (no float)


@pytest.fixture
async def bind_capture_factory():
    """Engine that records the RAW bind parameters of every ledger INSERT."""
    engine = create_async_engine("sqlite+aiosqlite://")
    captured: list[dict] = []

    def _capture(_conn, _cursor, statement, parameters, _context, _executemany):
        if "INSERT INTO cost_ledger" not in statement:
            return
        cols = ["run_id", "agent_role", "model", "input_tokens",
                "output_tokens", "cost_usd", "latency_ms"]
        if isinstance(parameters, (list, tuple)):
            if not parameters:
                return
            inner = parameters[0]
            if isinstance(inner, dict):
                captured.append(dict(inner))
            else:
                # positional (compiled) form: cost_usd is the 6th of 7 columns
                captured.append({k: v for k, v in zip(cols, inner, strict=False)})
        elif isinstance(parameters, dict):
            captured.append(dict(parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    factory._captured = captured  # type: ignore[attr-defined]
    yield factory
    event.remove(engine.sync_engine, "before_cursor_execute", _capture)
    await engine.dispose()


async def test_s1_writer_binds_cost_usd_as_exact_six_digit_text():
    """The bind-builder is the contract: money leaves Python as exact text."""
    from harness_identity.cost_ledger import usage_bind_params

    params = usage_bind_params(
        run_id="run-1",
        agent_role="primary",
        model="deepseek/deepseek-chat",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        latency_ms=3,
        cost_usd=Decimal("0.420000"),
    )
    bound = params["cost_usd"]
    assert isinstance(bound, str), f"money must bind as text, got {type(bound)}"
    assert bound == "0.420000"
    # float inputs normalize through repr, never binary expansion
    assert usage_bind_params(
        run_id=None, agent_role="primary", model="m", input_tokens=1,
        output_tokens=1, latency_ms=1, cost_usd=0.1,
    )["cost_usd"] == "0.100000"

    # And the writer end-to-end still lands the same value in the ledger.
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        team_id, run_id = await _seed_attribution(factory)
        written = await record_usage(
            factory, team_id=team_id, model="deepseek/deepseek-chat",
            input_tokens=1_000_000, output_tokens=1_000_000,
            cost_usd=Decimal("0.420000"),
        )
        assert written is not None
        assert written.cost_usd == Decimal("0.420000")
        async with factory() as db:
            stored = (
                await db.execute(text("SELECT cost_usd FROM cost_ledger"))
            ).scalar_one()
        assert Decimal(str(stored)) == Decimal("0.420000")
    finally:
        await engine.dispose()


async def test_writer_never_propagates_float_artifacts_into_the_ledger(money_factory):
    """The classic drift pair, through the public writer, read back exact."""
    team_id, run_id = await _seed_attribution(money_factory)
    a = await record_usage(
        money_factory, team_id=team_id, model="m", input_tokens=1,
        output_tokens=1, cost_usd=0.1, run_id=run_id,
    )
    b = await record_usage(
        money_factory, team_id=team_id, model="m", input_tokens=1,
        output_tokens=1, cost_usd=0.2, run_id=run_id,
    )
    assert a is not None and b is not None
    assert a.cost_usd == Decimal("0.100000")
    assert b.cost_usd == Decimal("0.200000")

    async with money_factory() as db:
        rows = (
            await db.execute(text("SELECT cost_usd FROM cost_ledger ORDER BY seq"))
        ).all()
    total = sum((Decimal(str(r[0])) for r in rows), Decimal("0"))
    assert total == Decimal("0.300000")


# --------------------------------- 4. Guard decisions match Decimal arithmetic


_DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    -- M18-C2: the cap resolver reads teams.budget_usd (migration 0007);
    -- scratch mirrors carry the frozen column like every real schema.
    budget_usd NUMERIC,
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


class _Status:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _RowMap:
    """Keyed view over a SQLAlchemy Row (budget_guard indexes by name)."""

    def __init__(self, row) -> None:
        self._mapping = row._mapping

    def __getitem__(self, key: str):
        return self._mapping[key]


class _SessionPool:
    """Adapts an async session_factory to budget_guard's duck-typed pool."""

    def __init__(self, factory) -> None:
        self._factory = factory

    async def fetchrow(self, sql: str, params: dict):
        async with self._factory() as db:
            result = await db.execute(text(sql), dict(params))
            row = result.first()
            await db.commit()
            return None if row is None else _RowMap(row)

    async def execute(self, sql: str, params: dict):
        async with self._factory() as db:
            result = await db.execute(text(sql), dict(params))
            await db.commit()
            return _Status(result.rowcount)


@pytest.fixture
async def guard_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    event.listen(engine.sync_engine, "connect", _register_sqlite_funcs)
    async with engine.begin() as conn:
        for stmt in _split_ddl(_DDL):
            await conn.exec_driver_sql(stmt)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_guard_team(factory, *, budget: Decimal) -> tuple[str, str]:
    """Returns (team_id, run_id) — reserves need a real run for the
    cost_ledger -> agent_runs -> projects.team_id attribution join."""
    team_id, project_id, run_id, plan_id = (str(uuid.uuid4()) for _ in range(4))
    async with factory() as db:
        await db.execute(
            text("INSERT INTO teams (id, name) VALUES (:i, :n)"),
            {"i": team_id, "n": "guard"},
        )
        await db.execute(
            text("INSERT INTO projects (id, team_id, name) VALUES (:i, :t, :n)"),
            {"i": project_id, "t": team_id, "n": "p"},
        )
        await db.execute(
            text(
                "INSERT INTO agent_runs (id, project_id, state) "
                "VALUES (:i, :p, 'running')"
            ),
            {"i": run_id, "p": project_id},
        )
        await db.execute(
            text(
                "INSERT INTO billing_plans (id, team_id, plan, monthly_budget_usd) "
                "VALUES (:i, :t, 'pro', :b)"
            ),
            {"i": plan_id, "t": team_id, "b": str(budget)},  # exact base-10 bind
        )
        await db.commit()
    return team_id, run_id


_BASE = {
    "agent_role": "primary",
    "model": "gpt-test",
    "input_tokens": 1,
    "output_tokens": 1,
    "latency_ms": 1,
}


async def test_guard_boundary_is_exact_not_float(guard_factory):
    """$1 budget, five $0.25 claims: exactly four win, the fifth is rejected.

    Binary-friendly fractions keep SQLite's REAL aggregation honest too —
    this pins inclusive-boundary semantics on both engines.
    """
    team_id, run_id = await _seed_guard_team(guard_factory, budget=Decimal("1.00"))
    pool = _SessionPool(guard_factory)
    allowed = 0
    for _ in range(5):
        result = await reserve(
            pool, team_id=team_id, estimated_cost_usd=Decimal("0.25"),
            run_id=run_id, env_budget_usd=None, **_BASE,
        )
        allowed += bool(result.allowed)
    assert allowed == 4
    async with guard_factory() as db:
        total = (
            await db.execute(text("SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger"))
        ).scalar_one()
    assert Decimal(str(total)) == Decimal("1.000000")


async def test_release_keeps_ledger_sum_exact(guard_factory):
    team_id, run_id = await _seed_guard_team(guard_factory, budget=Decimal("5.00"))
    pool = _SessionPool(guard_factory)
    ok = await reserve(pool, team_id=team_id, estimated_cost_usd=Decimal("2.50"),
                       run_id=run_id, env_budget_usd=None, **_BASE)
    assert ok.allowed
    assert await release(pool, ok.entry_id) is True
    async with guard_factory() as db:
        total = (
            await db.execute(text("SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger"))
        ).scalar_one()
    assert Decimal(str(total)) == Decimal("0.000000")


# ------------------------------------------------- 5. wire format unchanged


def test_money_wire_serializer_keeps_json_numbers_and_cents_rounding():
    """Contract M10-C3: quota fields serialize as JSON numbers (floats), cents
    scale — the HTTP surface itself stays pinned by test_billing.py."""
    assert billing_mod._money_wire(Decimal("25.555")) == 25.56
    assert billing_mod._money_wire(Decimal("25.554")) == 25.55
    assert billing_mod._money_wire(Decimal("0")) == 0.0
    assert isinstance(billing_mod._money_wire(Decimal("3")), float)
    # ORM defaults are Decimals now.
    assert BillingPlan.monthly_budget_usd.default.arg == Decimal("0")


# ============================== LIVE POSTGRES (opt-in) ===================================

_pg_reason = "HARNESS_TEST_PG not set; live-PG money-migration probe skipped"


def _pg_dsn() -> str:
    """HARNESS_TEST_PG semantics follow this repo's two conventions:
    '1'/'true' => flag, use the compose PG on localhost:5499; any other
    non-empty value is taken as an explicit DSN."""
    raw = os.environ.get("HARNESS_TEST_PG")
    if not raw or raw.strip().lower() in {"1", "true", "yes", "on"}:
        return "postgresql://harness:harness@localhost:5499/harness"
    dsn = raw
    if "://" not in dsn:
        dsn = f"postgresql://{dsn}"
    return dsn


def _split_sql(sql_text: str) -> list[str]:
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    out: list[str] = []
    for stmt in "\n".join(lines).split(";"):
        s = stmt.strip()
        if s:
            out.append(s)
    return out


def _split_ddl(ddl: str) -> list[str]:
    """Split a bare multi-statement DDL script for SQLite (comments stripped)."""
    lines = [ln for ln in ddl.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


async def _fresh_pg_db() -> tuple[asyncpg.Connection, str]:
    """Create a uniquely-named scratch database; return (admin_conn, dbname)."""
    import asyncpg

    dbname = f"harness_h4a_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_pg_dsn())
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    return admin, dbname


@pytest.mark.skipif(not os.environ.get("HARNESS_TEST_PG"), reason=_pg_reason)
class TestLivePostgresMoneyMigration:
    """The full migration chain, probed on a throwaway Postgres database."""

    async def test_migration_chain_lands_12_6_and_is_exact(self):
        import asyncpg

        admin, dbname = await _fresh_pg_db()
        try:
            conn = await asyncpg.connect(dsn=f"{_pg_dsn().rsplit('/', 1)[0]}/{dbname}")
            try:
                chain = [
                    "0001_initial.sql",
                    "0007_team_budget.sql",
                    "0010_billing_plans.sql",
                    "0020_money_numeric.sql",
                ]
                for name in chain:
                    for stmt in _split_sql(_migration(name)):
                        await conn.execute(stmt)
                # Idempotency: applying 0020 twice must be a no-op recast.
                for stmt in _split_sql(_migration("0020_money_numeric.sql")):
                    await conn.execute(stmt)

                rows = await conn.fetch(
                    """
                    SELECT table_name, column_name, numeric_precision, numeric_scale
                    FROM information_schema.columns
                    WHERE (table_name, column_name) IN (
                        ('agent_plans', 'estimated_cost_usd'),
                        ('cost_ledger', 'cost_usd'),
                        ('teams', 'budget_usd'),
                        ('billing_plans', 'monthly_budget_usd')
                    )
                    """
                )
                seen = {
                    (r["table_name"], r["column_name"]): (
                        r["numeric_precision"],
                        r["numeric_scale"],
                    )
                    for r in rows
                }
                assert len(seen) == 4, f"missing columns: {seen}"
                for key, (prec, scale) in seen.items():
                    assert (prec, scale) == (12, 6), f"{key} landed as ({prec},{scale})"

                # --- flagship scenario at maximum fractional resolution ---
                team_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO teams (id, name, budget_usd) VALUES ($1, $2, $3::numeric)",
                    team_id, "flagship", "99999.999999",
                )
                project_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO projects (id, team_id, name) VALUES ($1, $2, $3)",
                    project_id, team_id, "p",
                )
                run_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO agent_runs (id, project_id, state, updated_at, created_at) "
                    "VALUES ($1, $2, 'running', now(), now())",
                    run_id, project_id,
                )
                plan_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO billing_plans (id, team_id, plan, monthly_budget_usd, "
                    "updated_at) VALUES ($1, $2, 'pro', $3::numeric, now())",
                    plan_id, team_id, "100000.000001",
                )
                # Spend up to 0.000001 under the plan cap...
                await conn.execute(
                    "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens, "
                    "output_tokens, cost_usd, latency_ms) "
                    "VALUES ($1, 'primary', 'm', 1, 1, $2::numeric, 0)",
                    run_id, "100000.000000",
                )

                # float arithmetic CANNOT represent this sum exactly...
                fl = await conn.fetchval(
                    "SELECT 100000.000001::float8 + 0.000001::float8"
                )
                assert Decimal(str(fl)) == Decimal("100000.00000199999")
                assert Decimal(str(fl)) != Decimal("100000.000002")
                # ...but NUMERIC can:
                ex = await conn.fetchval(
                    "SELECT 100000.000001::numeric + 0.000001::numeric"
                )
                assert ex == Decimal("100000.000002")

                # Guard decision on the exact boundary: fits exactly once,
                # then over budget — float math would double-allow.
                from harness_router import budget_guard as bg

                sql, args = bg.to_positional_params(
                    bg.RESERVE_SQL,
                    {
                        "run_id": run_id, "agent_role": "primary", "model": "m",
                        "input_tokens": 1, "output_tokens": 1,
                        "cost_usd": "0.000001", "latency_ms": 0, "team_id": team_id,
                    },
                )
                first = await conn.fetchrow(sql, *args)
                assert first is not None, "exact-fit reservation must be allowed"
                second = await conn.fetchrow(sql, *args)
                assert second is None, "over-budget reservation must be rejected"

                # Aggregate stays EXACT after it all (SQLite cannot do this).
                total = await conn.fetchval(
                    "SELECT COALESCE(SUM(l.cost_usd), 0) FROM cost_ledger l "
                    "JOIN agent_runs r ON r.id = l.run_id "
                    "JOIN projects p ON p.id = r.project_id WHERE p.team_id = $1",
                    team_id,
                )
                assert total == Decimal("100000.000001"), f"drifted: {total!r}"

                # 0.1 + 0.2 class bug: dead on NUMERIC, alive on float8.
                assert await conn.fetchval(
                    "SELECT 0.1::numeric + 0.2::numeric"
                ) == Decimal("0.3")
                assert await conn.fetchval(
                    "SELECT 0.1::float8 + 0.2::float8"
                ) != 0.3
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            await admin.close()


# ============================== M17-B1 (F-1/F-2/F-4 regressions) ==========================

from harness_router.budget_guard import MONEY_MAX as _BG_MONEY_MAX  # noqa: E402
from harness_router.ledger import CostOutOfRangeError as _S3OutOfRange  # noqa: E402

from harness_identity.cost_ledger import (  # noqa: E402
    CostOutOfRangeError,
    _to_money,
)


def test_to_money_rejects_magnitude_beyond_numeric_12_6():
    """F-4: overflow must be REJECTED loudly at normalization time, never
    left to die as a swallowed driver error downstream."""
    assert _to_money(Decimal("999999.999999")) == Decimal("999999.999999")
    assert _to_money(Decimal("-999999.999999")) == Decimal("-999999.999999")
    with pytest.raises(CostOutOfRangeError):
        _to_money(Decimal("1000000.000000"))  # 7 integer digits > precision 12
    with pytest.raises(CostOutOfRangeError):
        _to_money("1234567.891234")  # same shape as the writer-level rejection
    with pytest.raises(CostOutOfRangeError):
        _to_money(-1000000.0)


def test_s3_guard_and_ledger_share_the_same_money_envelope():
    """One envelope across sections: the S3 writer and the S3 guard agree
    with Section 1's writer on what fits NUMERIC(12,6)."""
    assert _BG_MONEY_MAX == Decimal("999999.999999")
    with pytest.raises(_S3OutOfRange):
        __import__("harness_router.ledger", fromlist=["to_money"]).to_money(
            Decimal("1000000.000001")
        )


async def test_record_usage_rejects_out_of_range_without_silent_drop(
    money_factory, caplog
):
    """F-4 honest envelope: an overflowing estimate returns None (fire-and-
    forget contract preserved) but logs an explicit DATA rejection — and
    writes NOTHING to the ledger."""
    team_id, run_id = await _seed_attribution(money_factory)
    with caplog.at_level("WARNING", logger="harness_identity.cost_ledger"):
        written = await record_usage(
            money_factory, team_id=team_id, model="m", input_tokens=1,
            output_tokens=1, cost_usd=Decimal("1234567.891234"), run_id=run_id,
        )
    assert written is None
    assert any("REJECTED" in r.message for r in caplog.records), (
        "out-of-envelope spend must be called out as DATA rejection, "
        "not drowned in generic outage warnings"
    )
    async with money_factory() as db:
        rows = (await db.execute(text("SELECT COUNT(*) FROM cost_ledger"))).scalar_one()
    assert rows == 0


@pytest.mark.skipif(not os.environ.get("HARNESS_TEST_PG"), reason=_pg_reason)
class TestAsyncpgReserveSerialization:
    """M17-B1: the REAL production path — raw asyncpg pool, binds translated,
    advisory lock held — races 8 x $1 against a $5 plan and MUST let exactly
    five win. This is the in-suite twin of /tmp/malpha_probe/probe_pg_race.py,
    minus the fixture BEGIN IMMEDIATE that used to hide the bug."""

    async def test_eight_concurrent_reserves_against_five_dollar_plan(self):
        import asyncio as _asyncio

        import asyncpg
        from harness_router.budget_guard import reserve

        admin, dbname = await _fresh_pg_db()
        try:
            dsn = f"{_pg_dsn().rsplit('/', 1)[0]}/{dbname}"
            conn = await asyncpg.connect(dsn)
            try:
                for name in (
                    "0001_initial.sql",
                    "0007_team_budget.sql",
                    "0010_billing_plans.sql",
                    "0020_money_numeric.sql",
                ):
                    for stmt in _split_sql(_migration(name)):
                        await conn.execute(stmt)
                team_id = str(uuid.uuid4())
                project_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO teams (id, name) VALUES ($1, 'b1')", team_id
                )
                await conn.execute(
                    "INSERT INTO projects (id, team_id, name) VALUES ($1, $2, 'p')",
                    project_id, team_id,
                )
                run_ids = [str(uuid.uuid4()) for _ in range(8)]
                for rid in run_ids:
                    await conn.execute(
                        "INSERT INTO agent_runs (id, project_id, state) "
                        "VALUES ($1, $2, 'running')",
                        rid, project_id,
                    )
                await conn.execute(
                    "INSERT INTO billing_plans (id, team_id, plan, monthly_budget_usd) "
                    "VALUES ($1, $2, 'pro', 5.0)",
                    str(uuid.uuid4()), team_id,
                )
            finally:
                await conn.close()

            pool = await asyncpg.create_pool(dsn, min_size=8, max_size=8)
            results = await _asyncio.gather(
                *[
                    reserve(
                        pool,
                        team_id=team_id,
                        estimated_cost_usd=Decimal("1.0"),
                        run_id=rid,
                        agent_role="primary",
                        model="gpt-test",
                        input_tokens=100,
                        output_tokens=10,
                        latency_ms=5,
                    )
                    for rid in run_ids
                ]
            )

            wins = [r for r in results if r.allowed]
            overs = [r for r in results if not r.allowed and r.reason == "over budget"]
            errs = [r for r in results if not r.allowed and r.reason != "over budget"]
            assert len(wins) == 5, f"expected exactly 5 winners, got {results}"
            assert len(overs) == 3
            assert not errs, f"no claim may fail closed on the happy path: {errs}"
            assert all(r.entry_id is not None for r in wins)

            conn = await asyncpg.connect(dsn)
            try:
                total = await conn.fetchval(
                    "SELECT COALESCE(SUM(l.cost_usd), 0) FROM cost_ledger l "
                    "JOIN agent_runs ar ON ar.id = l.run_id "
                    "JOIN projects p ON p.id = ar.project_id WHERE p.team_id = $1",
                    team_id,
                )
            finally:
                await conn.close()
            assert total == Decimal("5.000000"), f"overspend survived: {total!r}"

            # Compensation on the asyncpg path: release parses the plain
            # "DELETE n" status string; freed headroom becomes reservable again.
            assert await release(pool, wins[0].entry_id) is True
            assert await release(pool, wins[0].entry_id) is False
            again = await reserve(
                pool, team_id=team_id, estimated_cost_usd=Decimal("1.0"),
                run_id=run_ids[0], agent_role="primary", model="gpt-test",
                input_tokens=100, output_tokens=10, latency_ms=5,
            )
            assert again.allowed
            await pool.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            await admin.close()

