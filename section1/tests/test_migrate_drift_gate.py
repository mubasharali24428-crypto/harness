"""M18-C6 (OPS-16/OPS-24 residual): deploy-time schema-drift gate.

The gate lives in deploy/runners/migrate.py (check_drift / SchemaDriftError)
and is enforced at two compose boot points: the `migrate` service (before it
applies anything) and every app-tier container's entrypoint pre-flight
(before uvicorn starts). These tests pin its DECISION TABLE:

    applied ⊆ declared  -> pass (pending migrations are normal)
    applied ⊄ declared  -> SchemaDriftError naming the extra versions and
                           which image tag matches them

The DB-facing cases run on in-memory SQLite via aiosqlite — check_drift only
reads `schema_migrations` and never touches PG-specific SQL, so SQLite is a
faithful driver here (the real-PG apply path is covered by pg-probe-migrate /
migrate_selftest.py, which also exercises the gate inside migrate()).
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

HARNESS_ROOT = Path(__file__).resolve().parents[2]
RUNNERS_DIR = HARNESS_ROOT / "deploy" / "runners"

# Import the runner module by path (it lives outside every section package,
# same trick as migrate_selftest.py).
sys.path.insert(0, str(RUNNERS_DIR))

import migrate as migrate_runner  # noqa: E402


def _disk_versions() -> set[str]:
    return set(migrate_runner.declared_versions())


async def _seed(engine, versions: list[str]) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version VARCHAR(20) PRIMARY KEY)"
            )
        )
        for v in versions:
            await conn.execute(text("INSERT INTO schema_migrations VALUES (:v)"), {"v": v})


async def test_gate_passes_when_applied_subset_of_declared():
    """Fresh-ish DB behind this image = pending migrations, NOT drift."""
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        await _seed(engine, ["0001"])
        await migrate_runner.check_drift(engine)  # must not raise
    finally:
        await engine.dispose()


async def test_gate_passes_on_exact_match_and_fresh_ledger():
    """Exact match passes; a ledger with no rows passes (fresh installs must boot)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        await _seed(engine, sorted(_disk_versions()))
        await migrate_runner.check_drift(engine)

        empty = create_async_engine("sqlite+aiosqlite://")
        try:
            await _seed(empty, [])
            await migrate_runner.check_drift(empty)  # nothing applied ≠ drift
        finally:
            await empty.dispose()
    finally:
        await engine.dispose()


async def test_gate_refuses_rolled_forward_database():
    """applied > declared: old image meets new schema — the rollback accident."""
    disk = _disk_versions()
    rolled_forward = sorted(disk) + ["0099", "0100"]
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        await _seed(engine, rolled_forward)
        with pytest.raises(migrate_runner.SchemaDriftError) as excinfo:
            await migrate_runner.check_drift(engine)
        msg = str(excinfo.value)
        assert "SCHEMA DRIFT" in msg
        for v in ("0099", "0100"):  # every extra version named...
            assert v in msg
        assert "harness-backend" in msg  # ...plus WHICH tag matches them
        # The message names the NEWEST extra version's file: any image whose
        # chain includes it is a valid roll-forward target.
        assert "0100_*.sql" in msg
        assert "rollback.md" in msg  # operator pointed at the runbook
    finally:
        await engine.dispose()


async def test_gate_refuses_unknown_version_even_below_max():
    """A ledger version that matches NO file on disk is drift regardless of order."""
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        await _seed(engine, ["0001", "9999_ghost"])
        with pytest.raises(migrate_runner.SchemaDriftError) as excinfo:
            await migrate_runner.check_drift(engine)
        assert "9999_ghost" in str(excinfo.value)
    finally:
        await engine.dispose()


def test_declared_versions_matches_disk_files():
    """Gate and apply-loop read disk through ONE helper; pin its output shape."""
    files = sorted(p.name for p in migrate_runner.MIGRATIONS_DIR.glob("*.sql"))
    declared = migrate_runner.declared_versions()
    assert declared == [f.split("_", 1)[0] for f in files]
    assert len(declared) == len(set(declared)), "duplicate prefixes would kill migrate()"


async def test_migrate_refuses_to_apply_when_drifted():
    """The gate sits at the TOP of migrate(): drifted ledger => nothing applies."""
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        await _seed(engine, ["0001", "9999_ghost"])
        with pytest.raises(migrate_runner.SchemaDriftError):
            await migrate_runner.migrate(engine)
        async with engine.connect() as conn:
            count = (await conn.execute(text("SELECT count(*) FROM schema_migrations"))).scalar()
        assert count == 2, "migrate() must not touch a drifted ledger"
    finally:
        await engine.dispose()
