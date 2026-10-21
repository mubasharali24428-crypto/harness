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
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


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
        engine = create_async_engine(f"{base}/{dbname}".replace("postgresql://", "postgresql+asyncpg://", 1))
        obs = _obs()
        app = FastAPI()
        monkeypatch.setenv("HARNESS_METRICS_DB_POLL_SECONDS", "0")
        obs.install_domain_metrics(app, factory=async_sessionmaker(engine), engine=engine, bus=None)

        await app.state.domain_metrics_poll()
        body = obs.METRICS.render()
        assert _sample(body, "harness_pg_wal_archive_failed_count_total") == expected_failed
        assert _sample(body, "harness_backup_last_success_timestamp_seconds") is None  # no backup yet

        await conn.execute("INSERT INTO backup_runs (kind) VALUES ('base')")
        newest = await conn.fetchval(
            "SELECT extract(epoch FROM max(finished_at))::float8 FROM backup_runs WHERE kind = 'base'"
        )
        await conn.close()
        await app.state.domain_metrics_poll()
        body = obs.METRICS.render()
        assert _sample(body, "harness_backup_last_success_timestamp_seconds") == pytest.approx(newest, abs=1)
    finally:
        if engine is not None:
            await engine.dispose()
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()
