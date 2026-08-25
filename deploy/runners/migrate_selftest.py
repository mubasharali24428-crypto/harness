"""M17-B2 (OPS-15): prove deploy/runners/migrate() applies 0001→latest on REAL
Postgres — the exact code path `docker compose up` gates every boot on.

Why this exists: until M17-B2, CI only ever built schemas via SQLite
`Base.metadata.create_all` (section1/tests/conftest.py) or the nightly PG
probe's create_all (integration/test_checkpoint1.py). Nothing executed
deploy/runners/migrate.py against Postgres, so its hand-rolled dollar-quote
splitter ($$ DO-blocks in 0003/0023), statement ordering, legacy-marking
branch, and re-run idempotency were all unexercised by any gate — a migration
that passes review but breaks the splitter lands green and fails at 3am.

Safety: this NEVER touches the live cluster's data. It targets one scratch
database named per run (`harness_migrate_probe_<hex>`), drops it when done,
and sweeps orphaned probe DBs from previously killed runs first. Failure
modes are loud: missing HARNESS_TEST_PG guard (parity with Makefile
pg-guard), duplicate version prefixes on disk, any mismatch between disk
migrations and schema_migrations rows.

Usage:
    HARNESS_TEST_PG=1 \\
    HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@localhost:5499/harness \\
    python deploy/runners/migrate_selftest.py
(CI runs it via `make pg-probe-migrate`; same env contract as pg-probe-s1.
Needs sqlalchemy + asyncpg — run inside section1/.venv like every pg-probe.)
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # deploy/runners/

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import migrate  # noqa: E402  (same dir — the runner under test)

# Spot-check subset only (full DDL coverage is implied by "every file applied
# without error"); these anchor several migration eras incl. the $$ DO-blocks.
EXPECTED_TABLES = {
    "projects",
    "agent_plans",
    "agent_events",
    "api_keys",
    "users",
    "schema_migrations",
}

_DEFAULT_URL = "postgresql+asyncpg://harness:harness@db:5432/harness"


def _url_for(dbname: str) -> str:
    url = os.environ.get("HARNESS_DATABASE_URL", _DEFAULT_URL)
    return url.rsplit("/", 1)[0] + f"/{dbname}"


async def main() -> int:
    if os.environ.get("HARNESS_TEST_PG") != "1":
        print(
            "migrate_selftest: refusing to run without HARNESS_TEST_PG=1 "
            "(real Postgres required — that is the entire point of this probe).",
            file=sys.stderr,
        )
        return 2

    # Admin connection to the maintenance DB. Engine-level AUTOCOMMIT because
    # CREATE/DROP DATABASE cannot run inside a transaction block.
    admin = create_async_engine(
        _url_for("postgres"), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    scratch = f"harness_migrate_probe_{uuid.uuid4().hex[:10]}"
    try:
        # Drop orphaned probe DBs from killed runs before creating ours.
        # Engine-level AUTOCOMMIT + plain connect(): each statement commits
        # immediately (CREATE/DROP DATABASE cannot run inside a txn block).
        async with admin.connect() as conn:
            orphans = (
                (
                    await conn.execute(
                        text(
                            "SELECT datname FROM pg_database "
                            "WHERE datname LIKE 'harness_migrate_probe_%'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            for name in orphans:
                await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{scratch}"'))

        engine = create_async_engine(_url_for(scratch), poolclass=NullPool)

        # --- pass 1: apply everything --------------------------------------
        files = sorted(p.name for p in migrate.MIGRATIONS_DIR.glob("*.sql"))
        expected_versions = [f.split("_", 1)[0] for f in files]
        dupes = {v for v in expected_versions if expected_versions.count(v) > 1}
        assert not dupes, (
            f"duplicate version prefix(es) on disk {sorted(dupes)} — "
            "migrate() would die inserting schema_migrations PK (W5 lesson)"
        )

        applied = await migrate.migrate(engine)
        assert applied == files, (
            f"expected the full disk chain applied, got {len(applied)} of "
            f"{len(files)}: first diff "
            f"{next((a for a, e in zip(applied, files) if a != e), '?')}"
        )

        async with engine.connect() as conn:
            rows = (
                await conn.execute(text("SELECT version FROM schema_migrations"))
            ).fetchall()
            versions = sorted(r[0] for r in rows)
            tables = {
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                        )
                    )
                ).fetchall()
            }
        assert versions == sorted(expected_versions), (
            f"schema_migrations {versions} != disk {sorted(expected_versions)}"
        )
        missing = EXPECTED_TABLES - tables
        assert not missing, f"core tables missing after full apply: {missing}"

        # --- pass 2: idempotency (the compose-up re-run path) ---------------
        reapplied = await migrate.migrate(engine)
        assert reapplied == [], f"second run must be a no-op, got {reapplied}"

        async with engine.connect() as conn:
            count = (
                await conn.execute(text("SELECT count(*) FROM schema_migrations"))
            ).scalar()
        assert count == len(expected_versions), "row count changed on re-run"

        await engine.dispose()
        print(
            f"migrate_selftest PASS: {len(files)} migrations "
            f"(0001→{expected_versions[-1]}) applied clean on real PG; "
            f"re-run idempotent."
        )
        return 0
    finally:
        # Best-effort scratch cleanup; the pre-run sweep catches leftovers anyway.
        try:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{scratch}" WITH (FORCE)'))
        except Exception:
            pass
        await admin.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
