"""Part 59: a database that holds real data upgrades through the migration chain without loss.

Every other PostgreSQL test builds an empty database from the whole chain. A deployment is not
empty: migrations 0020 (money type), 0023 (foreign keys rebuilt by copying tables), 0028
(`agent_events` swapped for monthly partitions) and 0036 (audit hash chain backfilled) move or
rewrite rows. This stops the chain at 0019, seeds data, runs the rest with the real runner and
compares. Opt-in (HARNESS_TEST_PG=<server dsn or 1>); builds and drops its own throwaway database.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.audit_chain import verify_chain
from harness_identity.config import Settings

ROOT = Path(__file__).resolve().parents[2]
CHAIN = ROOT / "section1" / "migrations"
pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; upgrade rehearsal skipped",
)
_spec = importlib.util.spec_from_file_location(
    "harness_migrate_upgrade", ROOT / "deploy/runners/migrate.py"
)
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

TOKEN = "rehearsal-session-token"
# What a deployment holds before the upgrade; cents-scale money, history either side of 2026-09-01
# (the bound migration 0028 gives the old events table).
EVENT_TIMES = ("2026-03-01T10:00:00Z", "2026-08-31T23:00:00Z")
LATE_EVENT_TIMES = ("2026-09-15T09:00:00Z", "2026-10-10T09:00:00Z")
SNAPSHOT = {  # only columns that exist at 0019; ordered, so the two sides compare as lists
    "teams": "SELECT id, name, budget_usd FROM teams ORDER BY id",
    "users": "SELECT id, email, name, is_active FROM users ORDER BY id",
    "team_members": "SELECT id, team_id, user_id, display_name, member_type, role"
    " FROM team_members ORDER BY id",
    "projects": "SELECT id, team_id, name, repo_full_name FROM projects ORDER BY id",
    "agent_runs": "SELECT id, project_id, state, branch FROM agent_runs ORDER BY id",
    "agent_plans": "SELECT id, run_id, goal, estimated_cost_usd, status, version"
    " FROM agent_plans ORDER BY id",
    "agent_events": "SELECT seq, project_id, run_id, type, payload, ts"
    " FROM agent_events ORDER BY seq",
    "cost_ledger": "SELECT seq, run_id, agent_role, model, input_tokens, output_tokens, cost_usd,"
    " latency_ms FROM cost_ledger ORDER BY seq",
    "audit_log": "SELECT seq, actor_kind, action, target_type, target_id, detail, ts"
    " FROM audit_log ORDER BY seq",
    "billing_plans": "SELECT id, team_id, plan, monthly_budget_usd FROM billing_plans ORDER BY id",
    "api_keys": "SELECT id, team_id, provider, label, key_encrypted, key_hint"
    " FROM api_keys ORDER BY id",
    "auth_sessions": "SELECT token_hash, user_id, team_id FROM auth_sessions ORDER BY token_hash",
}


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


@pytest.fixture
async def database():
    """A throwaway database, dropped even when the test fails."""
    import asyncpg

    name = f"harness_s11_up_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    yield name
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture
async def engine(database):
    eng = create_async_engine(
        f"{_server_dsn().rsplit('/', 1)[0]}/{database}".replace(
            "postgresql://", "postgresql+asyncpg://"
        )
    )
    yield eng
    await eng.dispose()


@pytest.fixture
def old_chain(tmp_path):
    """The migrations a deployment already ran: 0001-0019, in a directory of their own."""
    for path in CHAIN.glob("*.sql"):
        if int(path.name[:4]) <= 19:
            shutil.copy(path, tmp_path / path.name)
    return tmp_path


async def seed(dsn: str, event_times) -> None:
    """Foreign-key-valid rows in every table the chain moves, written through the 0019 schema."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("INSERT INTO teams (id, name, budget_usd) VALUES ('t1', 'Team', 25.50)")
        await conn.execute(
            "INSERT INTO users (id, email, name, is_active) VALUES ('u1', 'u1@x.io', 'U', true)"
        )
        await conn.execute(
            "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
            " VALUES ('m1', 't1', 'u1', 'U', 'HUMAN', 'ADMIN')"
        )
        await conn.execute(
            "INSERT INTO projects (id, team_id, name, repo_full_name)"
            " VALUES ('p1', 't1', 'P', 'o/r')"
        )
        await conn.execute(
            "INSERT INTO agent_runs (id, project_id, state, branch)"
            " VALUES ('r1', 'p1', 'done', 'b')"
        )
        await conn.execute(
            "INSERT INTO agent_plans (id, run_id, goal, files_likely_to_change, steps, tests,"
            " risks,"
            " estimated_cost_usd, forbidden_paths, expected_result, status, version)"
            " VALUES ('pl1', 'r1', 'g', '[]', '[]', '[]', '[]', 1.234567, '[]', 'e', 'approved', 2)"
        )
        for i, ts in enumerate(event_times):
            await conn.execute(
                "INSERT INTO agent_events (project_id, run_id, type, payload, ts)"
                " VALUES ('p1', 'r1', 'run.note', $1, $2)",
                json.dumps({"n": i}),
                datetime.fromisoformat(ts.replace("Z", "+00:00")),
            )
        await conn.execute(
            "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens, output_tokens,"
            " cost_usd, latency_ms) VALUES ('r1', 'coder', 'm', 10, 20, 0.123456, 30)"
        )
        for action in ("project.created", "run.approved", "key.revoked"):
            await conn.execute(
                "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type,"
                " target_id,"
                " detail) VALUES ('m1', 'human', $1, 'project', 'p1', $2)",
                action,
                json.dumps({"a": action}),
            )
        await conn.execute(
            "INSERT INTO billing_plans (id, team_id, plan, monthly_budget_usd)"
            " VALUES ('b1', 't1', 'team', 99.99)"
        )
        await conn.execute(
            "INSERT INTO api_keys (id, team_id, provider, label, key_encrypted, key_hint)"
            " VALUES ('k1', 't1', 'anthropic', 'main', 'ciphertext', 'abcd')"
        )
        await conn.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at)"
            " VALUES ($1, 'u1', 't1', '2099-01-01T00:00:00Z')",
            hashlib.sha256(TOKEN.encode()).hexdigest(),
        )
    finally:
        await conn.close()


async def snapshot(dsn: str) -> dict:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        return {t: [tuple(r) for r in await conn.fetch(sql)] for t, sql in SNAPSHOT.items()}
    finally:
        await conn.close()


async def upgrade(engine, dsn: str, old_chain, event_times, monkeypatch):
    """Stop at 0019, seed, run the real chain; returns (snapshot before, versions applied after)."""
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", old_chain)
    await migrate_mod.migrate(engine)
    await seed(dsn, event_times)
    before = await snapshot(dsn)
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", CHAIN)
    return before, await migrate_mod.migrate(engine)


def _dsn(database: str) -> str:
    return f"{_server_dsn().rsplit('/', 1)[0]}/{database}"


async def test_data_written_before_the_money_audit_and_foreign_key_migrations_survives_them(
    database, engine, old_chain, monkeypatch
):
    dsn = _dsn(database)
    before, applied = await upgrade(engine, dsn, old_chain, EVENT_TIMES, monkeypatch)
    assert applied == [f.name for f in sorted(CHAIN.glob("*.sql")) if int(f.name[:4]) > 19]
    after = await snapshot(dsn)
    assert all(before.values()), before  # a table seeded with nothing would prove nothing
    for table, rows in before.items():
        assert after[table] == rows, table  # same values; Decimal('25.50') == Decimal('25.500000')


async def test_events_dated_after_the_legacy_bound_still_upgrade(
    database, engine, old_chain, monkeypatch
):
    import asyncpg

    dsn = _dsn(database)
    before, _ = await upgrade(engine, dsn, old_chain, EVENT_TIMES + LATE_EVENT_TIMES, monkeypatch)
    assert (await snapshot(dsn))["agent_events"] == before["agent_events"]
    conn = await asyncpg.connect(
        dsn
    )  # the month after the old history is the first with a home of its own
    try:
        home = await conn.fetchval(
            "INSERT INTO agent_events (project_id, type, payload, ts)"
            " VALUES ('p1', 'run.note', '{}',"
            " '2026-11-15T00:00:00Z') RETURNING tableoid::regclass::text"
        )
    finally:
        await conn.close()
    assert home == "agent_events_2026_11"


async def test_the_first_event_after_the_upgrade_gets_a_seq_of_its_own(
    database, engine, old_chain, monkeypatch
):
    import asyncpg

    dsn = _dsn(database)
    before, _ = await upgrade(engine, dsn, old_chain, EVENT_TIMES, monkeypatch)
    conn = await asyncpg.connect(dsn)
    try:
        seq = await conn.fetchval(
            "INSERT INTO agent_events (project_id, run_id, type, payload, ts)"
            " VALUES ('p1', 'r1', 'run.note', '{}', $1) RETURNING seq",
            datetime(2026, 10, 1, tzinfo=UTC),
        )
    finally:
        await conn.close()
    assert seq > max(r[0] for r in before["agent_events"])  # the replay cursor never repeats


async def test_the_upgraded_database_keeps_working(database, engine, old_chain, monkeypatch):
    import asyncpg

    dsn = _dsn(database)
    await upgrade(engine, dsn, old_chain, EVENT_TIMES, monkeypatch)
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT numeric_scale FROM information_schema.columns"
                " WHERE table_name = 'teams' AND column_name = 'budget_usd'"
            )
            == 6
        )
    finally:
        await conn.close()
    async with engine.connect() as c:
        report = await verify_chain(c)
    assert report.ok and report.rows_checked == 3, report.problems  # backfilled, not just empty
    assert await migrate_mod.migrate(engine) == []  # a second run finds nothing pending
    app = make_app(
        async_sessionmaker(engine, expire_on_commit=False),
        Settings(
            master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/v1/projects", headers={"Authorization": f"Bearer {TOKEN}"})
    assert res.status_code == 200 and [p["id"] for p in res.json()["items"]] == ["p1"], res.text
