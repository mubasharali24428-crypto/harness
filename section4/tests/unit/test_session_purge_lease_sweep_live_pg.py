"""Parts 40 and 42 (session 12) on real Postgres: the scheduled session purge erases only the due
sessions' members, keeps their runs and audits the purge as a system action; the lease sweep
releases only the lapsed leases and audits each release. Opt-in (HARNESS_TEST_PG); the fixture
builds its own throwaway database."""

import importlib.util
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from harness_contracts.roles import Principal, Role
from sqlalchemy import text

from harness_orchestrator import collab
from harness_orchestrator.session_purge import purge_due

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "harness_migrate", _ROOT / "deploy" / "runners" / "migrate.py"
)
assert _spec is not None and _spec.loader is not None  # noqa: S101
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG session purge and lease sweep tests skipped",
)


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else (os.environ.get("HARNESS_TEST_PG_DSN") or "postgresql://harness:harness@127.0.0.1:5499/harness")


def _p(user: str) -> Principal:
    return Principal(userId=user, teamId="t1", role=Role.ENGINEER)


@pytest.fixture
async def factory():
    import asyncpg

    dbname = f"harness_s12_sweep_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(f"{base}/{dbname}")
    try:
        for path in sorted((_ROOT / "section1" / "migrations").glob("*.sql")):
            for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                if stmt.strip():
                    await conn.execute(stmt)
        # migrate() rolls the partition window forward; this hand-split loop
        # does not, so replicate that post-migration step or current-month
        # agent_events inserts fail.
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        _engine = create_async_engine(f"{base}/{dbname}".replace("postgresql://", "postgresql+asyncpg://"))
        try:
            await migrate_mod.ensure_forward_partitions(
                async_sessionmaker(_engine, expire_on_commit=False)
            )
        finally:
            await _engine.dispose()
        await conn.execute("INSERT INTO teams (id, name) VALUES ('t1', 'chaos')")
        await conn.execute(
            "INSERT INTO projects (id, team_id, name, repo_full_name)"
            " VALUES ('p', 't1', 'p', 'l/p')"
        )
        await conn.execute(
            "INSERT INTO agent_runs (id, project_id, state) VALUES ('r1', 'p', 'idle')"
        )
        for uid in ("lead", "eng"):  # handoff recipients must be joined team engineers
            await conn.execute(
                "INSERT INTO users (id, email, name, is_active) VALUES ($1, $2, $1, true)",
                uid, f"{uid}@x.test",
            )
            await conn.execute(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                " VALUES ($1, 't1', $2, $3, 'HUMAN', 'ENGINEER')",
                f"m-{uid}", uid, uid,
            )
    finally:
        await conn.close()
    url = f"{base}/{dbname}".replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    finally:
        await admin.close()


async def test_scheduled_purge_erases_due_members_keeps_runs_and_audits_the_system(factory):
    now = datetime.now(UTC)
    async with factory() as db:
        for sid, state, purge_after in (
            ("s-due", "deleted", now - timedelta(days=1)),
            ("s-later", "deleted", now + timedelta(days=5)),
            ("s-live", "active", None),
        ):
            await db.execute(
                text("INSERT INTO collab_sessions (id, team_id, title, description, owner_user_id,"
                     " state, version, retention_days, created_at, updated_at, deleted_at,"
                     " purge_after) VALUES (:i, 't1', 'Release', 'notes', 'lead', :st, 1, 30, :n,"
                     " :n, :d, :p)"),
                {"i": sid, "st": state, "n": now,
                 "d": now - timedelta(days=40) if state == "deleted" else None, "p": purge_after},
            )
            await db.execute(
                text("INSERT INTO session_members (id, session_id, user_id, role, display_name,"
                     " status, joined_at) VALUES (:m, :i, 'eng', 'participant', 'eng', 'active',"
                     " :n)"),
                {"m": f"m-{sid}", "i": sid, "n": now},
            )
        await db.execute(
            text("INSERT INTO session_runs (run_id, session_id, linked_by, linked_at)"
                 " VALUES ('r1', 's-due', 'lead', :n)"),
            {"n": now},
        )
        await db.commit()

    assert await purge_due(factory) == 1
    async with factory() as db:
        due = (await db.execute(text(
            "SELECT title, purged_at IS NOT NULL, state FROM collab_sessions WHERE id = 's-due'"
        ))).one()
        members = (await db.execute(text(
            "SELECT COUNT(*) FROM session_members WHERE session_id = 's-due'"))).scalar_one()
        runs = (await db.execute(text(
            "SELECT COUNT(*) FROM session_runs WHERE session_id = 's-due'"))).scalar_one()
        later = (await db.execute(text(
            "SELECT purged_at FROM collab_sessions WHERE id = 's-later'"))).scalar_one()
        live_members = (await db.execute(text(
            "SELECT COUNT(*) FROM session_members WHERE session_id = 's-live'"))).scalar_one()
        audit = (await db.execute(text(
            "SELECT actor_kind, actor_member_id, detail->>'actor', target_id"
            " FROM audit_log WHERE action = 'session.purged'"))).all()
    assert tuple(due) == ("[purged]", True, "deleted")
    assert members == 0 and runs == 1 and later is None and live_members == 1
    assert [tuple(a) for a in audit] == [("system", None, "scheduler", "s-due")]
    assert await purge_due(factory) == 0  # a purged session is never purged twice


async def test_lease_sweep_releases_only_lapsed_leases_and_audits_each(factory):
    await collab.join(factory, _p("lead"), "r1")
    await collab.join(factory, _p("eng"), "r1")
    await collab.claim_leader(factory, _p("lead"), "r1")
    async with factory() as db:
        await db.execute(text("INSERT INTO agent_runs (id, project_id, state)"
                              " VALUES ('r2', 'p', 'idle')"))
        await db.commit()
    await collab.join(factory, _p("lead"), "r2")
    await collab.claim_leader(factory, _p("lead"), "r2")
    async with factory() as db:  # r1's lease has lapsed; r2's is still live
        await db.execute(text("UPDATE control_leases SET expires_at = now() - interval '1 minute'"
                              " WHERE run_id = 'r1'"))
        await db.commit()

    assert await collab.sweep_expired_leases(factory) == 1
    async with factory() as db:
        leases = [r[0] for r in (await db.execute(text("SELECT run_id FROM control_leases"))).all()]
        role = (await db.execute(text(
            "SELECT role FROM run_participants WHERE run_id = 'r1' AND user_id = 'lead'"
        ))).scalar_one()
        audit = [r[0] for r in (await db.execute(text(
            "SELECT target_id FROM audit_log WHERE action = 'control.expired'"))).all()]
    assert leases == ["r2"] and role == "participant" and audit == ["r1"]
    assert await collab.sweep_expired_leases(factory) == 0
