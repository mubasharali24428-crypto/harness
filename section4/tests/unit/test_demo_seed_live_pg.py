"""M19-N4 live-PG regression (opt-in: HARNESS_TEST_PG) — the FK-cycle lesson.

Found by the OPS-20 compose E2E (2026-10-17): ``POST /demo/seed`` answered 500
DEMO_SEED_FAILED on real Postgres — the seeder wrote ``agent_runs.pr_id``
BEFORE the ``pull_requests`` row it references, violating
``fk_agent_runs_pr_id``. The hermetic unit lane runs SQLite with FKs OFF
(pytest does not enable the pragma), so the original ordering passed every
unit test and broke on the first real deployment.

Laws pinned here, against a throwaway database with the FULL 0001->latest
chain applied (production fidelity via deploy/runners/migrate.py's splitter):

1. ``agent_runs.pr_id`` -> ``pull_requests.id`` and ``pull_requests.run_id``
   -> ``agent_runs.id`` form a CYCLE: the seeder must insert run (NULL pr_id)
   -> PR row -> back-link UPDATE, exactly like S2's pr_open.py producer.
2. FKs are ENFORCED in this lane — a probe that tries to bind a run to a
   nonexistent PR must fail with the FK violation. If this probe ever starts
   passing, the regression guard above is dead and this test must fail loudly.

Mechanics mirror test_cost_rollup_live_pg.py (same DSN conventions, one event
loop, function-scoped fixtures, DROP ... WITH (FORCE)).
"""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS = _ROOT / "section1" / "migrations"

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG demo-seed proof skipped",
)

_spec = importlib.util.spec_from_file_location(
    "harness_migrate", _ROOT / "deploy" / "runners" / "migrate.py"
)
assert _spec is not None and _spec.loader is not None  # noqa: S101 — structural
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


def _db_dsn(dbname: str) -> str:
    return f"{_server_dsn().rsplit('/', 1)[0]}/{dbname}"


def _sa_url(dbname: str) -> str:
    return _db_dsn(dbname).replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture
async def pg():
    """Throwaway database with the whole 0001->latest chain applied."""
    try:
        import asyncpg
    except ImportError:  # pragma: no cover — f1 convention: skip w/o driver
        pytest.skip("asyncpg unavailable in this venv; live-PG probe skipped")

    dbname = f"harness_demo_{uuid.uuid4().hex[:10]}"
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
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_sa_url(pg["dbname"]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def test_seed_run_rows_survive_the_fk_cycle_on_postgres(sa_factory):
    """The exact write sequence the E2E failed on, now green end to end."""
    from types import SimpleNamespace

    from harness_contracts.pricing import PricingEngine
    from sqlalchemy import text

    from harness_orchestrator import demo as demo_mod
    from harness_orchestrator.demo_replay import load_fixture

    fixture = load_fixture()
    proj_spec = fixture["project"]
    run_id, plan_id, pr_id = "demo-run-pglive", "demo-plan-pglive", "demo-pr-pglive"
    run = demo_mod._bind_tokens(
        fixture["run"], run_id=run_id, plan_id=plan_id, pr_id=pr_id
    )

    async with sa_factory() as db:
        team_id = "pg-team-demo"
        await db.execute(
            text("INSERT INTO teams (id, name) VALUES (:t, :n)"),
            {"t": team_id, "n": "pg-demo"},
        )
        project_id, created = await demo_mod._seed_project(
            db, team_id, proj_spec["name"], proj_spec["repoFullName"]
        )
        assert created is True

        service = SimpleNamespace(planner=SimpleNamespace(plans={}, plan_approvals={}))
        cost = await demo_mod._seed_run_rows(
            db,
            service,
            run,
            project_id=project_id,
            run_id=run_id,
            plan_id=plan_id,
            pr_id=pr_id,
            team_id=team_id,
        )
        await db.commit()

        row = (
            await db.execute(
                text("SELECT state, pr_id FROM agent_runs WHERE id = :r"), {"r": run_id}
            )
        ).first()
        assert row.state == "pr_opened"
        # THE regression: the cycle's back-link landed on real Postgres.
        assert row.pr_id == pr_id

        pr_row = (
            await db.execute(
                text("SELECT run_id, github_pr_number FROM pull_requests WHERE id = :p"),
                {"p": pr_id},
            )
        ).first()
        assert pr_row.run_id == run_id and pr_row.github_pr_number == run["pullRequest"]["number"]

        plan_row = (
            await db.execute(
                text("SELECT status FROM agent_plans WHERE id = :p"), {"p": plan_id}
            )
        ).first()
        assert plan_row.status == "approved"
        assert service.planner.plans[plan_id].goal == run["goal"]

        usage = run["usage"]
        expected = float(
            PricingEngine.calculate_cost(
                usage["model"], int(usage["inputTokens"]), int(usage["outputTokens"])
            )
        )
        ledger_sum = (
            await db.execute(
                text("SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger WHERE run_id = :r"),
                {"r": run_id},
            )
        ).scalar_one()
        assert float(ledger_sum) == pytest.approx(cost, abs=1e-9)
        assert cost == pytest.approx(expected, abs=1e-9)

        total, marked = (
            await db.execute(
                text(
                    "SELECT COUNT(*),"
                    " COUNT(*) FILTER (WHERE payload->'demo'->>'replay' = 'true')"
                    " FROM agent_events WHERE run_id = :r"
                ),
                {"r": run_id},
            )
        ).first()
        assert total == len(run["events"]) >= 8
        assert marked == total, "every seeded event must carry the replay marker"


async def test_fk_cycle_probe_fails_in_this_lane(sa_factory):
    """Guard-of-the-guard: FKs ARE enforced here, so the regression above can
    never silently degrade to the SQLite no-op it was born from."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    async with sa_factory() as db:
        await db.execute(
            text("INSERT INTO teams (id, name) VALUES ('pg-team-probe', 'probe')")
        )
        await db.execute(
            text(
                "INSERT INTO projects (id, team_id, name)"
                " VALUES ('pg-proj-probe', 'pg-team-probe', 'probe')"
            )
        )
        await db.commit()

    with pytest.raises(IntegrityError, match="fk_agent_runs_pr_id"):
        async with sa_factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state, pr_id)"
                    " VALUES ('pg-run-probe', 'pg-proj-probe', 'pr_opened', 'ghost-pr')"
                )
            )
            await db.commit()
