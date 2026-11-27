"""Part 28 (session 12) on real Postgres: the two backup alerts now have emitters.

`harness_pg_wal_archive_failed_count_total` mirrors pg_stat_archiver.failed_count, and
`harness_backup_last_success_timestamp_seconds` is the newest `backup_runs` row that
deploy/runners/backup.sh writes after its storage post-check. With no backup recorded the series
is absent (not a false zero). Opt-in (HARNESS_TEST_PG); builds and drops its own database.
"""

import importlib.util
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNERS = REPO_ROOT / "deploy" / "runners"

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG backup metrics test skipped",
)


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else (os.environ.get("HARNESS_TEST_PG_DSN") or "postgresql://harness:harness@127.0.0.1:5499/harness")


def _obs():
    """Load deploy/runners/observability.py as deployed (the same path surgery as the `obs`
    fixture in test_deploy_observability.py)."""
    srcs = [str(p / "src") for p in sorted(REPO_ROOT.glob("section*")) if (p / "src").exists()]
    added = [p for p in srcs if p not in sys.path]
    sys.path.extend(added)
    sys.path.insert(0, str(RUNNERS))
    try:
        spec = importlib.util.spec_from_file_location("obs_backup_pg", RUNNERS / "observability.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["obs_backup_pg"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for p in added:
            sys.path.remove(p)
        sys.path.remove(str(RUNNERS))


def _sample(body: str, name: str):
    m = re.search(rf"^{name} (\S+)$", body, re.M)
    return None if m is None else float(m.group(1))


async def test_backup_and_wal_series_come_from_postgres(monkeypatch):
    import asyncpg
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    dbname = f"harness_s12_backup_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    engine = None
    try:
        conn = await asyncpg.connect(f"{base}/{dbname}")
        spec = importlib.util.spec_from_file_location("harness_migrate_b", RUNNERS / "migrate.py")
        migrate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migrate)
        for path in sorted((REPO_ROOT / "section1" / "migrations").glob("*.sql")):
            for stmt in migrate.split_statements(path.read_text(encoding="utf-8")):
                if stmt.strip():
                    await conn.execute(stmt)
        expected_failed = await conn.fetchval("SELECT failed_count FROM pg_stat_archiver")
        url = f"{base}/{dbname}".replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url)
        obs = _obs()
        app = FastAPI()
        monkeypatch.setenv("HARNESS_METRICS_DB_POLL_SECONDS", "0")
        factory = async_sessionmaker(engine)
        obs.install_domain_metrics(app, factory=factory, engine=engine, bus=None)

        await app.state.domain_metrics_poll()
        body = obs.METRICS.render()
        assert _sample(body, "harness_pg_wal_archive_failed_count_total") == expected_failed
        # no backup recorded yet: the series is absent, not zero
        assert _sample(body, "harness_backup_last_success_timestamp_seconds") is None

        await conn.execute("INSERT INTO backup_runs (kind) VALUES ('base')")
        newest = await conn.fetchval(
            "SELECT extract(epoch FROM max(finished_at))::float8"
            " FROM backup_runs WHERE kind = 'base'"
        )
        await conn.close()
        await app.state.domain_metrics_poll()
        body = obs.METRICS.render()
        seen = _sample(body, "harness_backup_last_success_timestamp_seconds")
        assert seen == pytest.approx(newest, abs=1)
    finally:
        if engine is not None:
            await engine.dispose()
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()


async def test_cost_gauges_and_latency_quantiles_come_from_postgres(monkeypatch):
    """W14: the cost dashboard's series must be real rollups of cost_ledger.

    PostgreSQL is the only dialect that computes the latency quantiles
    (percentile_cont), so this is the test that exercises them at all. Seeded
    through the frozen attribution chain; p50/p95 are interpolated values, not
    nearest-rank rows.
    """
    import asyncpg
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    dbname = f"harness_w14_cost_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    engine = None
    try:
        conn = await asyncpg.connect(f"{base}/{dbname}")
        spec = importlib.util.spec_from_file_location("harness_migrate_c", RUNNERS / "migrate.py")
        migrate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migrate)
        for path in sorted((REPO_ROOT / "section1" / "migrations").glob("*.sql")):
            for stmt in migrate.split_statements(path.read_text(encoding="utf-8")):
                if stmt.strip():
                    await conn.execute(stmt)

        await conn.execute("INSERT INTO teams (id, name, budget_usd) VALUES ('t-pg','Team',100)")
        await conn.execute(
            "INSERT INTO projects (id, team_id, name) VALUES ('p-pg','t-pg','Proj')"
        )
        await conn.execute(
            "INSERT INTO agent_runs (id, project_id, state) VALUES ('r-pg','p-pg','created')"
        )
        for latency_ms, cost in ((100, 0.25), (200, 0.25), (300, 0.5), (400, 1.0)):
            await conn.execute(
                "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens,"
                " output_tokens, cost_usd, latency_ms)"
                " VALUES ('r-pg','engineer','pg/model',10,10,$1,$2)",
                cost,
                latency_ms,
            )
        await conn.close()

        url = f"{base}/{dbname}".replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url)
        obs = _obs()
        app = FastAPI()
        monkeypatch.setenv("HARNESS_METRICS_DB_POLL_SECONDS", "0")
        factory = async_sessionmaker(engine)
        obs.install_domain_metrics(app, factory=factory, engine=engine, bus=None)
        await app.state.domain_metrics_poll()
        body = obs.METRICS.render()

        def labelled(name: str, labels: str):
            match = re.search(rf"^{name}\{{{labels}\}} (\S+)$", body, re.M)
            return None if match is None else float(match.group(1))

        # percentile_cont(0.5) over [100,200,300,400] = 250 ms;
        # percentile_cont(0.95) = 385 ms.
        assert labelled(
            "harness_model_latency_p50_seconds", 'team_id="t-pg",model="pg/model"'
        ) == pytest.approx(0.25, abs=1e-9)
        assert labelled(
            "harness_model_latency_p95_seconds", 'team_id="t-pg",model="pg/model"'
        ) == pytest.approx(0.385, abs=1e-9)
        assert labelled(
            "harness_model_cost_usd", 'team_id="t-pg",model="pg/model"'
        ) == pytest.approx(2.0, abs=1e-6)
        assert labelled("harness_team_spend_usd_month", 'team_id="t-pg"') == pytest.approx(
            2.0, abs=1e-6
        )
        assert labelled("harness_team_budget_usd", 'team_id="t-pg"') == pytest.approx(100.0)
    finally:
        if engine is not None:
            await engine.dispose()
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()
