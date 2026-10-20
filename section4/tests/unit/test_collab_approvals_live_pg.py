"""Approvals on a real PostgreSQL; opt in with HARNESS_TEST_PG=1 (a server DSN, like the other
*_live_pg tests).

The sqlite tests cannot show what `decidedAt` looks like on a TIMESTAMPTZ column, or that the
"changed after approval" flag reads JSONB event payloads correctly, so this applies the real
migration chain to a disposable database and drives the real collab functions.
"""

import importlib.util
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from harness_contracts.roles import Principal, Role
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import collab

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; approvals PG probe skipped",
)
_spec = importlib.util.spec_from_file_location(
    "harness_migrate_approvals", ROOT / "deploy/runners/migrate.py"
)
assert _spec is not None and _spec.loader is not None
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)

ISO_UTC_MS = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00"


def server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


@pytest.fixture
async def database():
    """A throwaway database, dropped even when the fixture that fills it fails."""
    import asyncpg

    name = f"harness_appr_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        # a session zone that is not UTC: the API must still say UTC
        await admin.execute(f"ALTER DATABASE \"{name}\" SET timezone = 'Asia/Karachi'")
    finally:
        await admin.close()
    yield name
    admin = await asyncpg.connect(server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture
async def pg(database):
    import asyncpg

    base = server_dsn().rsplit("/", 1)[0]
    name = database
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        for path in sorted((ROOT / "section1/migrations").glob("*.sql")):
            for stmt in migrate.split_statements(path.read_text(encoding="utf-8")):
                if stmt.strip():
                    await conn.execute(stmt)
        await conn.execute("INSERT INTO teams (id, name) VALUES ('t1', 'appr')")
        for uid, role in (("u1", "admin"), ("u2", "engineer")):
            await conn.execute(
                "INSERT INTO users (id, email, name, is_active) VALUES ($1, $2, $1, true)",
                uid, f"{uid}@example.test",
            )
            await conn.execute(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                " VALUES ($1, 't1', $2, $2, 'human', $3)", f"m-{uid}", uid, role,
            )
        await conn.execute("INSERT INTO projects (id, team_id, name) VALUES ('p1', 't1', 'appr')")
        await conn.execute(
            "INSERT INTO agent_runs (id, project_id, state) VALUES ('r1', 'p1', 'idle')"
        )
    finally:
        await conn.close()
    engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_decision_time_and_changed_after_approval_flag_on_postgres(pg):
    lead = Principal(userId="u1", teamId="t1", role=Role.ADMIN)
    eng = Principal(userId="u2", teamId="t1", role=Role.ENGINEER)
    await collab.claim_leader(pg, lead, "r1")
    await collab.join(pg, eng, "r1")
    a = await collab.request_approval(pg, eng, "r1", "delete", ["src/a.py"], "drop a", "-x")
    only = lambda: collab.list_approvals(pg, "r1")  # noqa: E731
    row = (await only())[0]
    assert (row["decidedAt"], row["amendedAfterApproval"]) == (None, False)

    await collab.decide_approval(pg, lead, "r1", a["id"], True, a["digest"])
    row = (await only())[0]
    assert re.fullmatch(ISO_UTC_MS, row["decidedAt"]), row["decidedAt"]
    decided = datetime.fromisoformat(row["decidedAt"])
    assert abs(decided - datetime.now(UTC)) < timedelta(minutes=1)
    assert row["amendedAfterApproval"] is False  # approved and unchanged

    await collab.amend_approval(pg, eng, "r1", a["id"], ["src/b.py"], "drop b", "-y")
    row = (await only())[0]
    assert (row["state"], row["decidedAt"], row["amendedAfterApproval"]) == ("pending", None, True)
