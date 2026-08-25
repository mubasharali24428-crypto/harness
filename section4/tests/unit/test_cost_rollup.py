"""M18-C3 — cost-gate O(1) rollup + index proofs (F6, m17-findings-pbc1.md).

Covers the three C3 deliverables end-to-end on the hermetic SQLite lane:

1. migration 0027 file pins (runner-compatible: no $$ blocks, no transaction
   control, semicolon-free comments) + the duplicate-prefix live-race guard;
2. deploy/runners/cost_rollup.py semantics: backfill of closed days only,
   RECOMPUTE self-healing after release()-style deletes, never storing today;
3. section4 budget.check_budget routing: exact legacy fallback while the
   rollup is empty, rollup(closed)+live(today) once covered — proven
   behaviorally by pruning closed ledger rows and showing the gate STILL
   reports their spend (only possible via the rollup) — plus a latency
   assertion with 10k+ seeded ledger rows;
4. SQLite EXPLAIN QUERY PLAN proof that idx_cost_ledger_created_at serves
   the today slice. The authoritative PG EXPLAIN + exact-NUMERIC proofs run
   live under HARNESS_TEST_PG (skipped otherwise, money-suite convention).

Window note (M18-C2 integration): the gate evaluates MONTH-to-date spend
under the ONE ``date_trunc('month', now())`` definition, so every expected
value here is computed by a mirror SUM carrying the identical DB-clock window
(registered as SQLite user functions, pattern from test_budget_atomic) — the
assertions hold on any calendar day, including the 1st.

Self-contained per wave-9 hotspot rules: the schema comes straight from the
section1 ORM metadata (models carry cost_ledger_daily since 0027), and
deploy/runners scripts are loaded by path like section1's retention tests.
"""

import importlib.util
import sys as _sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Sibling section src trees resolve at RUNTIME (pyproject pythonpath carries
# only this section + contracts) — mirrors tests/unit/test_run_metering.py.
_ROOT = Path(__file__).resolve().parents[3]
for _seam in ("section1",):
    _src = str(_ROOT / _seam / "src")
    if _src not in _sys.path:
        _sys.path.append(_src)

# deploy/runners/cost_rollup.py is a script, not a package member: load by
# path (mirrors section1/tests/test_events_retention.py).
_RUNNER = _ROOT / "deploy" / "runners" / "cost_rollup.py"
_spec = importlib.util.spec_from_file_location("cost_rollup", _RUNNER)
assert _spec is not None and _spec.loader is not None  # noqa: S101 — structural
cost_rollup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cost_rollup)

from harness_identity.models import Base  # noqa: E402  (ORM carries 0027 table)

from harness_orchestrator.budget import (  # noqa: E402
    MONTH_WINDOW_SQL,
    check_budget,
)

MIGRATIONS_DIR = _ROOT / "section1" / "migrations"
MIGRATION_0027 = MIGRATIONS_DIR / "0027_cost_ledger_indexes.sql"


def _sqlite_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _sqlite_date_trunc(unit: str, value: str) -> str:
    assert unit in ("day", "month")
    if unit == "month":
        return f"{str(value)[:7]}-01 00:00:00"
    return f"{str(value)[:10]} 00:00:00"


def _register_sqlite_funcs(dbapi_conn, _rec=None) -> None:
    """Production SQL runs verbatim: now() + date_trunc get identical-semantics
    user functions (pattern from section1 tests/test_budget_atomic.py)."""
    dbapi_conn.create_function("now", 0, _sqlite_now)
    dbapi_conn.create_function("date_trunc", 2, _sqlite_date_trunc)


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    event.listen(engine.sync_engine, "connect", _register_sqlite_funcs)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_team(factory, suffix: str = "a") -> tuple[str, str]:
    """teams/projects/agent_run chain the frozen attribution walks."""
    from harness_identity.models import AgentRun, Project, Team

    team_id, project_id, run_id = f"team-{suffix}", f"proj-{suffix}", f"run-{suffix}"
    async with factory() as db:
        db.add_all(
            [
                Team(id=team_id, name=f"rollup-team-{suffix}"),
                Project(id=project_id, team_id=team_id, name=f"rollup-proj-{suffix}"),
                AgentRun(id=run_id, project_id=project_id, state="running"),
            ]
        )
        await db.commit()
    return team_id, run_id


async def _add_ledger(
    factory,
    run_id: str,
    cost_usd: str,
    *,
    created_at: datetime | None = None,
    days_ago: float | None = None,
) -> None:
    from harness_identity.models import CostLedger

    if created_at is None:
        created_at = datetime.now(UTC) - timedelta(days=days_ago or 0.0)
    async with factory() as db:
        db.add(
            CostLedger(
                run_id=run_id,
                agent_role="primary",
                model="stub",
                input_tokens=0,
                output_tokens=0,
                cost_usd=cost_usd,  # exact base-10 text (H-023 bind shape)
                latency_ms=0,
                created_at=created_at,
            )
        )
        await db.commit()


async def _add_many_ledger(
    factory, run_id: str, count: int, *, cost_each: str, closed_days: int
) -> None:
    """executemany bulk seed spread over ``closed_days`` closed days."""

    now = datetime.now(UTC)
    rows = []
    for i in range(count):
        # Strictly INSIDE each closed day (noon + sub-hour jitter): varying
        # times-of-day would smear one source day across two calendar dates
        # and inflate the distinct-day count past ``closed_days``.
        created_at = (now - timedelta(days=(i % closed_days) + 1)).replace(
            hour=12, minute=(i * 7) % 60, second=0, microsecond=0
        )
        rows.append(
            {
                "run_id": run_id,
                "agent_role": "primary",
                "model": "stub",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": cost_each,
                "latency_ms": 0,
                "created_at": created_at,
            }
        )
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens, "
                "output_tokens, cost_usd, latency_ms, created_at) "
                "VALUES (:run_id, :agent_role, :model, :input_tokens, :output_tokens, "
                ":cost_usd, :latency_ms, :created_at)"
            ),
            rows,
        )
        await db.commit()


async def _mirror_month_sum(factory, team_id: str) -> float:
    """Expected spend per the ONE M18-C2 definition, computed independently."""
    async with factory() as db:
        value = (
            await db.execute(
                text(
                    "SELECT COALESCE(SUM(l.cost_usd), 0) FROM cost_ledger l "
                    "JOIN agent_runs r ON r.id = l.run_id "
                    "JOIN projects p ON p.id = r.project_id "
                    "WHERE p.team_id = :t AND l." + MONTH_WINDOW_SQL
                ),
                {"t": team_id},
            )
        ).scalar_one()
    return float(value or 0.0)


async def _rollup_rows(factory, team_id: str) -> dict[str, float]:
    async with factory() as db:
        rows = (
            await db.execute(
                text("SELECT day, total_usd FROM cost_ledger_daily WHERE team_id = :t"),
                {"t": team_id},
            )
        ).fetchall()
    return {str(day)[:10]: float(total) for day, total in rows}


async def _set_rollup_row(factory, team_id: str, day_iso: str, total: str) -> None:
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO cost_ledger_daily (team_id, day, total_usd) "
                "VALUES (:t, :d, :v)"
            ),
            {"t": team_id, "d": day_iso, "v": total},
        )
        await db.commit()


async def _prune_closed_ledger(factory, *, run_id: str | None = None) -> None:
    """Delete every CLOSED source row (retention/release simulation)."""
    sql = "DELETE FROM cost_ledger WHERE created_at < date_trunc('day', now())"
    params: dict = {}
    if run_id is not None:
        sql += " AND run_id = :r"
        params["r"] = run_id
    async with factory() as db:
        await db.execute(text(sql), params)
        await db.commit()


# --------------------------------------------------------------------- 1. gate


async def test_gate_falls_back_to_exact_sum_while_rollup_empty(db_factory):
    """No rollup coverage ⇒ legacy whole-month SUM answers, unchanged."""
    f = db_factory
    team_id, run_id = await _seed_team(f)
    await _add_ledger(f, run_id, "3.000000", days_ago=2)
    await _add_ledger(f, run_id, "0.500000", days_ago=0)

    status = await check_budget(
        f, team_id, budget_usd=10.0, estimated_call_cost_usd=0.05
    )
    assert status.allowed is True
    assert status.spentUsd == pytest.approx(await _mirror_month_sum(f, team_id))


async def test_gate_uses_rollup_plus_live_today_slice(db_factory):
    """Covered team ⇒ closed days from the rollup, today summed live; the two
    halves agree exactly with the mirror month-to-date SUM."""
    f = db_factory
    team_id, run_id = await _seed_team(f)
    await _add_ledger(f, run_id, "3.000000", days_ago=1)
    await _add_ledger(f, run_id, "1.250000", days_ago=2)
    await _add_ledger(f, run_id, "0.500000", days_ago=0)  # today, stays live

    upserted = await cost_rollup.rollup_once(f)
    assert upserted == 2  # two CLOSED days; today is never stored
    rows = await _rollup_rows(f, team_id)
    assert pytest.approx(sum(rows.values())) == 4.25

    status = await check_budget(
        f, team_id, budget_usd=10.0, estimated_call_cost_usd=0.05
    )
    assert status.spentUsd == pytest.approx(await _mirror_month_sum(f, team_id))


async def test_gate_read_comes_from_rollup_not_ledger(db_factory):
    """Decisive routing proof: closed-day ledger rows are PRUNED while their
    rollup rows remain. Only a gate that truly reads cost_ledger_daily can
    still report the closed spend (the legacy SUM would see today's rows
    alone). Expected = the pre-prune mirror truth, captured first."""
    f = db_factory
    team_id, run_id = await _seed_team(f)
    await _add_ledger(f, run_id, "7.000000", days_ago=1)
    await _add_ledger(f, run_id, "0.500000", days_ago=0)
    truth = await _mirror_month_sum(f, team_id)

    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    await _set_rollup_row(f, team_id, yesterday, "7.000000")

    await _prune_closed_ledger(f, run_id=run_id)

    status = await check_budget(
        f, team_id, budget_usd=10.0, estimated_call_cost_usd=0.05
    )
    assert status.spentUsd == pytest.approx(truth)


async def test_gate_boundary_day_is_exact(db_factory):
    """The closed/open seam: yesterday 23:59:59Z rolls up, today 00:00:01Z
    stays live, neither half double-counts. On the month's first day
    yesterday belongs to LAST month and the ONE window drops it — expected
    tracks that law instead of assuming."""
    f = db_factory
    team_id, run_id = await _seed_team(f)
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    await _add_ledger(f, run_id, "2.000000", created_at=midnight - timedelta(seconds=1))
    await _add_ledger(f, run_id, "0.250000", created_at=midnight + timedelta(seconds=1))

    await cost_rollup.rollup_once(f)
    rows = await _rollup_rows(f, team_id)
    assert len(rows) == 1 and pytest.approx(2.0) == sum(rows.values())

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    expected = 2.25 if midnight > month_start else 0.25
    status = await check_budget(
        f, team_id, budget_usd=10.0, estimated_call_cost_usd=0.05
    )
    assert status.spentUsd == pytest.approx(expected)


# ------------------------------------------------------------------ 2. sidecar


async def test_rollup_never_stores_today(db_factory):
    f = db_factory
    team_id, run_id = await _seed_team(f)
    await _add_ledger(f, run_id, "9.990000", days_ago=0)

    upserted = await cost_rollup.rollup_once(f)
    assert upserted == 0
    assert await _rollup_rows(f, team_id) == {}


async def test_rollup_recompute_self_heals_release_deletes(db_factory):
    """release() deletes reserved ledger rows; an additive upsert could never
    undo them. Recompute semantics: the next pass reflects the deletion."""
    f = db_factory
    team_id, run_id = await _seed_team(f)
    await _add_ledger(f, run_id, "3.000000", days_ago=1)
    await _add_ledger(f, run_id, "1.000000", days_ago=1)

    await cost_rollup.rollup_once(f)
    assert pytest.approx(4.0) == sum((await _rollup_rows(f, team_id)).values())

    async with f() as db:  # release() compensation shape
        await db.execute(text("DELETE FROM cost_ledger WHERE cost_usd = 1.000000"))
        await db.commit()

    await cost_rollup.rollup_once(f)
    assert pytest.approx(3.0) == sum((await _rollup_rows(f, team_id)).values())


async def test_rollup_vanishing_day_leaves_no_phantom_spend(db_factory):
    """Regression (found via live-PG probe): when release() deletes a day's
    LAST ledger row, that day must VANISH from the rollup on the next pass.
    A key-set-derived DELETE never revisits an emptied day and its stale row
    would overstate spend forever (+$8.53 in the live repro)."""
    f = db_factory
    team_id, run_id = await _seed_team(f)
    # Day -2 gets TWO rows; day -1 gets exactly ONE.
    await _add_ledger(f, run_id, "3.000000", days_ago=2)
    await _add_ledger(f, run_id, "1.000000", days_ago=2)
    await _add_ledger(f, run_id, "5.000000", days_ago=1)

    await cost_rollup.rollup_once(f)
    assert len(await _rollup_rows(f, team_id)) == 2

    async with f() as db:  # delete day -1's ONLY row
        await db.execute(text("DELETE FROM cost_ledger WHERE cost_usd = 5.000000"))
        await db.commit()

    await cost_rollup.rollup_once(f)
    rows = await _rollup_rows(f, team_id)
    assert len(rows) == 1, f"emptied day must vanish, got {rows}"
    assert pytest.approx(4.0) == sum(rows.values())


async def test_rollup_scopes_by_team(db_factory):
    f = db_factory
    team_a, run_a = await _seed_team(f, "a")
    team_b, run_b = await _seed_team(f, "b")
    await _add_ledger(f, run_a, "5.000000", days_ago=1)
    await _add_ledger(f, run_b, "1.000000", days_ago=1)

    await cost_rollup.rollup_once(f)
    assert pytest.approx(5.0) == sum((await _rollup_rows(f, team_a)).values())
    assert pytest.approx(1.0) == sum((await _rollup_rows(f, team_b)).values())


async def test_orphan_ledger_rows_never_reach_the_rollup(db_factory):
    """Unattributable rows (no run) stay excluded — same law as every other
    spend read."""
    f = db_factory
    await _seed_team(f)
    from harness_identity.models import CostLedger

    async with f() as db:
        db.add(
            CostLedger(
                run_id=None,
                agent_role="primary",
                model="stub",
                input_tokens=0,
                output_tokens=0,
                cost_usd="50.000000",
                latency_ms=0,
                created_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await db.commit()

    assert await cost_rollup.rollup_once(f) == 0


# ------------------------------------------------------- 3. latency @ 10k rows


async def test_gate_latency_with_rollup_at_10k_rows(db_factory):
    """10k+ seeded ledger rows across 30 closed days: the covered-gate read
    stays far inside any interactive budget AND lands the mirror-exact
    month-to-date figure."""
    f = db_factory
    team_id, run_id = await _seed_team(f)
    await _add_many_ledger(
        f, run_id, count=10_000, cost_each="0.010000", closed_days=30
    )
    await _add_ledger(f, run_id, "0.500000", days_ago=0)

    await cost_rollup.rollup_once(f)
    assert len(await _rollup_rows(f, team_id)) == 30

    import time

    started = time.monotonic()
    status = await check_budget(
        f, team_id, budget_usd=1_000.0, estimated_call_cost_usd=0.05
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"covered gate read took {elapsed:.3f}s at 10k rows"
    assert status.allowed is True
    assert status.spentUsd == pytest.approx(await _mirror_month_sum(f, team_id))


# --------------------------------------------------------- 4. migration pins


def _sql_body(content: str) -> str:
    return "\n".join(
        line for line in content.splitlines() if not line.strip().startswith("--")
    )


def test_migration_0027_runner_compatible_and_additive():
    content = MIGRATION_0027.read_text(encoding="utf-8")
    body = _sql_body(content)
    assert "$$" not in content, "PG-runner law: no dollar-quote DO blocks"
    assert "BEGIN" not in body.upper(), "runner wraps the file in its own transaction"
    assert "COMMIT" not in body.upper()
    for line in content.splitlines():
        if line.strip().startswith("--"):
            assert ";" not in line, f"semicolon inside comment breaks the splitter: {line!r}"
    statements = [s.strip() for s in body.split(";") if s.strip()]
    assert len(statements) == 2, "0027 = one CREATE INDEX + one CREATE TABLE"
    assert statements[0].upper().startswith(
        "CREATE INDEX IF NOT EXISTS IDX_COST_LEDGER_CREATED_AT"
    )
    assert statements[1].upper().startswith("CREATE TABLE IF NOT EXISTS COST_LEDGER_DAILY")
    joined = "\n".join(statements).upper()
    assert "DROP " not in joined and "DELETE" not in joined
    assert "NUMERIC(12, 6)" in joined, "money column mirrors frozen NUMERIC(12,6)"


def test_migration_0027_slot_is_exclusively_ours():
    """Live-race guard (W5 lesson): exactly ONE file owns the 0027 prefix."""
    claims = list(MIGRATIONS_DIR.glob("0027_*.sql"))
    assert [p.name for p in claims] == ["0027_cost_ledger_indexes.sql"]


async def test_migration_0027_applies_to_real_dialect(tmp_path):
    """Apply 0027 to a scratch SQLite carrying 0001-shaped money tables: the
    statements execute verbatim and both objects exist afterwards."""
    path = tmp_path / "apply.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200))")
        )
        await conn.execute(
            text(
                "CREATE TABLE cost_ledger (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "run_id VARCHAR(36), agent_role VARCHAR(20), model VARCHAR(120), "
                "input_tokens INTEGER, output_tokens INTEGER, cost_usd NUMERIC(12,6), "
                "latency_ms INTEGER, created_at TIMESTAMP)"
            )
        )
        content = MIGRATION_0027.read_text(encoding="utf-8")
        for stmt in [s.strip() for s in _sql_body(content).split(";") if s.strip()]:
            await conn.execute(text(stmt))
        names = (
            await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type IN ('index','table')")
            )
        ).fetchall()
    await engine.dispose()
    flat = {row[0] for row in names}
    assert "idx_cost_ledger_created_at" in flat
    assert "cost_ledger_daily" in flat


# ------------------------------------------------------------------- 5. plans


async def test_sqlite_eqp_uses_created_at_index_for_today_slice(tmp_path):
    """EXPLAIN QUERY PLAN: the today slice scans idx_cost_ledger_created_at
    instead of the bare ledger (SQLite leg of the O(1) proof; PG leg runs
    live under HARNESS_TEST_PG)."""
    path = tmp_path / "plan.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        for ddl in (
            "CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY)",
            "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36))",
            "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36))",
            "CREATE TABLE cost_ledger (seq INTEGER PRIMARY KEY, run_id VARCHAR(36), "
            "cost_usd NUMERIC(12,6), created_at TIMESTAMP)",
            "CREATE TABLE cost_ledger_daily (team_id VARCHAR(36), day DATE, "
            "total_usd NUMERIC(12,6), PRIMARY KEY (team_id, day))",
        ):
            await conn.execute(text(ddl))
        content = MIGRATION_0027.read_text(encoding="utf-8")
        for stmt in [s.strip() for s in _sql_body(content).split(";") if s.strip()]:
            await conn.execute(text(stmt))
        plan = (
            await conn.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT COALESCE(SUM(l.cost_usd), 0) "
                    "FROM cost_ledger l "
                    "JOIN agent_runs r ON l.run_id = r.id "
                    "JOIN projects p ON r.project_id = p.id "
                    "WHERE p.team_id = 'team-a' "
                    "AND l.created_at >= '2026-08-25 00:00:00'"
                )
            )
        ).fetchall()
    await engine.dispose()
    detail = " | ".join(str(row[-1]) for row in plan)
    assert "idx_cost_ledger_created_at" in detail, f"unexpected plan: {detail}"
