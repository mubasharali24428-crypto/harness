"""M18-C3 live-PG proofs (opt-in: HARNESS_TEST_PG, money-suite convention).

Aggregate-exactness is ONLY provable on real Postgres (SQLite SUM() over
numeric-affinity columns returns REAL; H-023 lesson), and the authoritative
EXPLAIN proof needs a real planner. Everything here is skipped unless
HARNESS_TEST_PG is set; the hermetic lane lives in test_cost_rollup.py.

Mechanics mirror section1/tests/test_fk_deletion_story.py: a throwaway
database gets the ENTIRE 0001->0027 chain applied (statement split via
deploy/runners/migrate.py's own dollar-quote-aware splitter -- production
fidelity), probed over asyncpg/SQLAlchemy on ONE event loop (function-scoped
async fixtures; sync fixtures would hand asyncpg connections across loops),
then dropped WITH (FORCE).
"""

import importlib.util
import json
import os
import sys as _sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

# Sibling section src trees resolve at RUNTIME (pyproject pythonpath carries
# only this section + contracts) — mirrors tests/unit/test_run_metering.py.
_ROOT = Path(__file__).resolve().parents[3]
for _seam in ("section1", "section3"):
    _src = str(_ROOT / _seam / "src")
    if _src not in _sys.path:
        _sys.path.append(_src)

_MIGRATIONS = _ROOT / "section1" / "migrations"

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG cost-rollup proofs skipped",
)

_spec = importlib.util.spec_from_file_location(
    "harness_migrate", _ROOT / "deploy" / "runners" / "migrate.py"
)
assert _spec is not None and _spec.loader is not None  # noqa: S101 — structural
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

_RUNNER_MODULE = None


def _load_runner():
    """Import deploy/runners/cost_rollup.py by path, once (cached module)."""
    global _RUNNER_MODULE
    if _RUNNER_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "cost_rollup_runner", _ROOT / "deploy" / "runners" / "cost_rollup.py"
        )
        assert spec is not None and spec.loader is not None  # noqa: S101
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _RUNNER_MODULE = module
    return _RUNNER_MODULE


def _server_dsn() -> str:
    """HARNESS_TEST_PG carries a DSN (f1 convention) or acts as a flag
    (fk_deletion convention -> compose mapping on 127.0.0.1:5499)."""
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


def _db_dsn(dbname: str) -> str:
    return f"{_server_dsn().rsplit('/', 1)[0]}/{dbname}"


def _sa_url(dbname: str) -> str:
    return _db_dsn(dbname).replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture
async def pg():
    """Throwaway database with the whole 0001->0027 chain applied."""
    try:
        import asyncpg
    except ImportError:  # pragma: no cover — f1 convention: skip w/o driver
        pytest.skip("asyncpg unavailable in this venv; live-PG probe skipped")

    dbname = f"harness_c3_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()

    conn = await asyncpg.connect(_db_dsn(dbname))
    try:
        for path in sorted(_MIGRATIONS.glob("*.sql")):
            for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                stmt = stmt.strip()
                if stmt:
                    await conn.execute(stmt)
        # migrate() rolls the partition window forward; this hand-split loop
        # does not, so replicate that post-migration step or current-month
        # agent_events inserts fail.
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        _engine = create_async_engine(_sa_url(dbname))
        try:
            await migrate_mod.ensure_forward_partitions(
                async_sessionmaker(_engine, expire_on_commit=False)
            )
        finally:
            await _engine.dispose()
        yield {"dbname": dbname, "conn": conn}
    finally:
        await conn.close()
        admin = await asyncpg.connect(_server_dsn())
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        finally:
            await admin.close()


@pytest.fixture
async def sa_factory(pg):
    """Async session factory onto the scratch DB (rollup_once/check_budget duck-type)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_sa_url(pg["dbname"]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_chain(sa_factory, tag: str, *, bulk_rows: int = 0) -> tuple[str, str]:
    """teams/projects/agent_run (+ optional bulk CLOSED-day ledger seeding)."""
    from sqlalchemy import text

    team_id, project_id, run_id = f"c3-team-{tag}", f"c3-proj-{tag}", f"c3-run-{tag}"
    async with sa_factory() as db:
        await db.execute(text("INSERT INTO teams (id, name) VALUES (:t, :n)"),
                         {"t": team_id, "n": f"c3-{tag}"})
        await db.execute(
            text("INSERT INTO projects (id, team_id, name) VALUES (:p, :t, :n)"),
            {"p": project_id, "t": team_id, "n": f"c3-p-{tag}"},
        )
        await db.execute(
            text("INSERT INTO agent_runs (id, project_id, state) VALUES (:r, :p, 'running')"),
            {"r": run_id, "p": project_id},
        )
        if bulk_rows:
            # generate_series seeding: strictly closed days (>= 1 day back),
            # exact-scale money, spread wide so the today-slice stays selective.
            await db.execute(
                text(
                    "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens, "
                    "output_tokens, cost_usd, latency_ms, created_at) "
                    "SELECT :r, 'primary', 'stub', 0, 0, "
                    "((((g * 37) % 997) + 1)::numeric / 100)::numeric(12,6), 0, "
                    "now() - (((g % 30) + 1) * interval '1 day' "
                    "+ ((g * 7) % 23) * interval '1 hour') "
                    "FROM generate_series(1, :n) AS g"
                ),
                {"r": run_id, "n": bulk_rows},
            )
        await db.commit()
    return team_id, run_id


async def _rolled_decimal(sa_factory, team_id: str) -> Decimal:
    """SUM of cost_ledger_daily for the team, THE M18-C2 month window
    (matching what the gate reads); the table itself legitimately holds ALL
    closed history."""
    from harness_contracts.budget import MONTH_WINDOW_SQL
    from harness_router.ledger import to_money
    from sqlalchemy import text

    async with sa_factory() as db:
        rolled = (
            await db.execute(
                text(
                    "SELECT COALESCE(SUM(total_usd), 0) FROM cost_ledger_daily "
                    "WHERE team_id = :t AND day >= date_trunc('month', now())"
                    f" -- {MONTH_WINDOW_SQL}"
                ),
                {"t": team_id},
            )
        ).scalar_one()
    return to_money(rolled)


async def _direct_sum(conn, team_id: str, *, whole_ledger: bool = False) -> Decimal:
    """Mirror SUM through the attribution chain. Default window is THE M18-C2
    month definition (what the gate evaluates); ``whole_ledger=True`` removes
    the window for feed-level checks."""
    from harness_contracts.budget import MONTH_WINDOW_SQL
    from harness_router.ledger import to_money

    window = "" if whole_ledger else f" AND l.{MONTH_WINDOW_SQL}"
    row = await conn.fetchrow(
        f"""
        SELECT COALESCE(SUM(l.cost_usd), 0) AS s
        FROM cost_ledger l
        JOIN agent_runs r ON r.id = l.run_id
        JOIN projects p ON p.id = r.project_id
        WHERE p.team_id = $1{window}
        """,
        team_id,
    )
    return to_money(row["s"])


# --------------------------------------------------------------- object + plan


async def test_live_pg_0027_objects_and_index_only_plan(pg):
    conn = pg["conn"]
    idx = await conn.fetchval(
        "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_cost_ledger_created_at'"
    )
    tbl = await conn.fetchval(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'cost_ledger_daily'"
    )
    pk_cols = await conn.fetch(
        """
        SELECT a.attname FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey::int2[])
        WHERE c.relname = 'cost_ledger_daily' AND i.indisprimary
        ORDER BY a.attname
        """
    )
    assert idx == 1 and tbl == 1
    assert sorted(r["attname"] for r in pk_cols) == ["day", "team_id"]

    # Real planner, SELECTIVE today-slice: 20k rows spread over two years so
    # an index range on our index beats any full scan (a near-empty table
    # would legitimately plan a Seq Scan; that proves nothing either way).
    await conn.execute(
        """
        INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens,
                                 output_tokens, cost_usd, latency_ms, created_at)
        SELECT NULL, 'primary', 'eqp-probe', 0, 0, 1::numeric(12,6), 0,
               date_trunc('day', now()) - ((g % 730) * interval '1 day')
               - ((g % 23) * interval '1 hour')
        FROM generate_series(1, 20000) AS g
        """
    )
    await conn.execute("ANALYZE cost_ledger")
    rows = await conn.fetch(
        """
        EXPLAIN (FORMAT JSON) SELECT COALESCE(SUM(l.cost_usd), 0)
        FROM cost_ledger l
        JOIN agent_runs r ON l.run_id = r.id
        JOIN projects p ON r.project_id = p.id
        WHERE p.team_id = 'no-such-team'
          AND l.created_at >= date_trunc('day', now())
        """
    )
    doc = json.loads(rows[0][0])

    def _walk(node):
        yield node
        for child in node.get("Plans", []):
            yield from _walk(child)

    nodes = [n for plan_root in doc for n in _walk(plan_root["Plan"])]
    ledger_scans = [
        n for n in nodes
        if n.get("Node Type") == "Seq Scan" and n.get("Relation Name") == "cost_ledger"
    ]
    scan_names = [n.get("Index Name") for n in nodes if n.get("Index Name")]
    assert not ledger_scans, f"seq scan on cost_ledger survived: {doc}"
    assert "idx_cost_ledger_created_at" in scan_names, f"plan indexes: {scan_names}"


# --------------------------------------------------- exactness + feed behavior


async def test_live_pg_rollup_matches_direct_sum_exactly(pg, sa_factory):
    """THE money proof (NUMERIC-exact, live PG only): rollup totals equal a
    direct whole-ledger SUM as Decimals; today never lands in the table; the
    gate reports the exact figure through rollup(closed)+live(today)."""
    from sqlalchemy import text

    from harness_orchestrator.budget import check_budget

    runner = _load_runner()
    tag = uuid.uuid4().hex[:8]
    team_id, run_id = await _seed_chain(sa_factory, tag, bulk_rows=250)
    async with sa_factory() as db:  # open-day row: must stay OUT of the rollup
        await db.execute(
            text(
                "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens, "
                "output_tokens, cost_usd, latency_ms, created_at) "
                "VALUES (:r, 'primary', 'stub', 0, 0, 0.500000::numeric(12,6), 0, now())"
            ),
            {"r": run_id},
        )
        await db.commit()

    upserted = await runner.rollup_once(sa_factory)
    assert upserted >= 8  # distinct closed days actually materialized

    days = await sa_factory_bind_days(sa_factory, team_id)
    assert days >= 8

    from harness_contracts.budget import MONTH_WINDOW_SQL
    from harness_router.ledger import to_money

    # Month-scoped closed-day truth (the ONE M18-C2 window ∩ closed days):
    # what the rollup is required to hold.
    closed_month = to_money(
        await pg["conn"].fetchval(
            f"""
            SELECT COALESCE(SUM(l.cost_usd), 0)
            FROM cost_ledger l
            JOIN agent_runs r ON r.id = l.run_id
            JOIN projects p ON p.id = r.project_id
            WHERE p.team_id = $1
              AND l.{MONTH_WINDOW_SQL}
              AND l.created_at < date_trunc('day', now())
            """,
            team_id,
        )
    )
    rolled = await _rolled_decimal(sa_factory, team_id)
    assert rolled == closed_month

    status = await check_budget(
        sa_factory, team_id, budget_usd=10**6, estimated_call_cost_usd=0
    )
    got = Decimal(repr(status.spentUsd)).quantize(Decimal("0.000001"))
    want = await _direct_sum(pg["conn"], team_id)  # THE month definition
    assert got == want.quantize(Decimal("0.000001"))


async def sa_factory_bind_days(sa_factory, team_id: str) -> int:
    from sqlalchemy import text

    async with sa_factory() as db:
        n = (
            await db.execute(
                text("SELECT COUNT(*) FROM cost_ledger_daily WHERE team_id = :t"),
                {"t": team_id},
            )
        ).scalar_one()
    return int(n)


async def test_live_pg_recompute_heals_release_deletes(pg, sa_factory):
    runner = _load_runner()
    tag = uuid.uuid4().hex[:8]
    team_id, _run_id = await _seed_chain(sa_factory, tag, bulk_rows=40)

    await runner.rollup_once(sa_factory)
    conn = pg["conn"]

    async def _closed_sum():
        from harness_router.ledger import to_money
        row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(l.cost_usd), 0) AS s
            FROM cost_ledger l
            JOIN agent_runs r ON r.id = l.run_id
            JOIN projects p ON p.id = r.project_id
            WHERE p.team_id = $1 AND l.created_at < date_trunc('day', now())
            """,
            team_id,
        )
        return to_money(row["s"])

    before = await _closed_sum()

    # release() compensation shape: delete SOME reserved rows outright.
    # Compare all closed history, not only the current month window, so the
    # assertion is independent of the calendar day the test runs on.
    deleted = await conn.execute(
        """
        DELETE FROM cost_ledger WHERE seq IN (
          SELECT l.seq FROM cost_ledger l
          JOIN agent_runs r ON r.id = l.run_id
          JOIN projects p ON p.id = r.project_id
          WHERE p.team_id = $1 AND l.created_at < date_trunc('day', now())
          ORDER BY l.created_at DESC LIMIT 5
        )
        """,
        team_id,
    )
    assert str(deleted).endswith("5")
    after = await _closed_sum()
    assert after < before

    await runner.rollup_once(sa_factory)
    from harness_router.ledger import to_money
    healed_raw = await conn.fetchval(
        "SELECT COALESCE(SUM(total_usd), 0) FROM cost_ledger_daily WHERE team_id = $1",
        team_id,
    )
    assert to_money(healed_raw) == after, "recompute must reflect released (deleted) rows"


# --------------------------------------- O(1) latency + routing under 10k rows


async def test_live_pg_gate_exact_and_fast_at_12k_rows_with_source_pruned(pg, sa_factory):
    """12k seeded rows: after the sidecar feeds, EVERY closed-day source row
    is pruned — the covered gate still lands the EXACT figure quickly (only
    the rollup can know those numbers), proving both routing and O(1)-ish
    read cost independent of the (formerly huge) ledger."""
    from sqlalchemy import text

    from harness_orchestrator.budget import check_budget

    runner = _load_runner()
    tag = uuid.uuid4().hex[:8]
    team_id, run_id = await _seed_chain(sa_factory, tag, bulk_rows=12_000)
    async with sa_factory() as db:
        await db.execute(
            text(
                "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens, "
                "output_tokens, cost_usd, latency_ms, created_at) "
                "VALUES (:r, 'primary', 'stub', 0, 0, 0.250000::numeric(12,6), 0, now())"
            ),
            {"r": run_id},
        )
        await db.commit()

    t_feed = time.monotonic()
    await runner.rollup_once(sa_factory)
    feed_seconds = time.monotonic() - t_feed

    conn = pg["conn"]
    expected_total = await _direct_sum(conn, team_id)

    # Prune EVERY closed-day source row for this team (today's row survives:
    # it is the live slice). The covered gate must still land the EXACT
    # pre-prune month figure -- only the rollup can know it.
    await conn.execute(
        """
        DELETE FROM cost_ledger WHERE run_id = $1
          AND created_at < date_trunc('day', now())
        """,
        run_id,
    )

    started = time.monotonic()
    status = await check_budget(
        sa_factory, team_id, budget_usd=10**6, estimated_call_cost_usd=0
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"covered gate read took {elapsed:.3f}s on live PG"
    assert status.allowed is True
    got = Decimal(repr(status.spentUsd)).quantize(Decimal("0.000001"))
    want = expected_total.quantize(Decimal("0.000001"))
    assert got == want, f"gate said {got}, pre-prune truth was {want}"
    # Feed throughput sanity (sidecar, not gate path): 12k rows recompute well
    # under a minute on dev hardware; guards against accidental O(rows^2).
    assert feed_seconds < 60.0, f"rollup feed took {feed_seconds:.1f}s"
