"""Part 38 (session 12) on live PostgreSQL: the socket and policy-cache scenarios of
test_policy_sockets.py, run against a migrated throwaway harness_s12_* database. Opt-in like the
other live-PG suites (HARNESS_TEST_PG=1, default DSN is the local compose database)."""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# The same scenarios, collected here with this module's PostgreSQL `env` fixture.
from test_policy_sockets import (  # noqa: F401
    ORG,
    test_a_policy_write_clears_the_cache_before_the_ttl,
    test_foreign_project_is_refused_before_any_policy_is_read,
    test_open_socket_closes_on_the_recheck_after_a_deny_lands,
    test_realtime_sockets_are_classified_and_org_routes_are_not_exempt,
    test_repeat_decisions_within_the_ttl_do_not_reread_policy,
    test_upgrade_is_refused_on_a_non_overridable_deny,
)

from harness_identity.app import make_app
from harness_identity.config import Settings

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG policy socket tests skipped",
)

_spec = importlib.util.spec_from_file_location(
    "harness_migrate", ROOT / "deploy" / "runners" / "migrate.py"
)
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


@pytest.fixture
async def env():
    import asyncpg

    name = f"harness_s12_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        for path in sorted((ROOT / "section1" / "migrations").glob("*.sql")):
            for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                await conn.execute(stmt)
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, status, created_at)"
            " VALUES ($1, 'Acme', 'acme', 'active', now())",
            ORG,
        )
    finally:
        await conn.close()
    engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
    db = async_sessionmaker(engine, expire_on_commit=False)
    app = make_app(db, Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[],
                                require_ownership=False))

    async def verifier(request, db_session, principal, code):
        return code == "000000"

    app.state.stepup_verifier = verifier
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.app_, c.db, c.engine = app, db, engine
        yield c
    await engine.dispose()
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
