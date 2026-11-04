"""P2.4 chaos on real Postgres (opt-in: HARNESS_TEST_PG), full 0001->latest chain.

SQLite hides: jsonb typing, partial-unique-index races, real concurrent commits.
Each test uses its own throwaway database.
"""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import collab

_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG collab chaos skipped",
)

_spec = importlib.util.spec_from_file_location(
    "harness_migrate", _ROOT / "deploy" / "runners" / "migrate.py"
)
assert _spec is not None and _spec.loader is not None  # noqa: S101
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


def _p(user: str) -> Principal:
    return Principal(userId=user, teamId="t1", role=Role.ENGINEER)


@pytest.fixture
async def factory():
    import asyncpg

    dbname = f"harness_chaos_{uuid.uuid4().hex[:10]}"
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


async def _join_all(f, users):
    for u in users:
        await collab.join(f, _p(u), "r1")


async def test_many_leaders_racing_yield_exactly_one(factory):
    users = [f"u{i}" for i in range(8)]
    await _join_all(factory, users)
    results = await asyncio.gather(
        *(collab.claim_leader(factory, _p(u), "r1") for u in users), return_exceptions=True
    )
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 1, results
    losers = [r for r in results if r not in ok]
    assert all(isinstance(r, collab.CollabError) and r.status == 409 for r in losers)
    async with factory() as db:
        n = (await db.execute(text(
            "SELECT count(*) FROM run_participants WHERE run_id='r1' AND role='leader'"
        ))).scalar_one()
    assert n == 1


async def test_concurrent_approve_and_reject_have_one_winner(factory):
    await _join_all(factory, ["lead", "eng"])
    await collab.claim_leader(factory, _p("lead"), "r1")
    a = await collab.request_approval(factory, _p("eng"), "r1", "delete", ["a.py"], "rm", "-x")
    results = await asyncio.gather(
        collab.decide_approval(factory, _p("lead"), "r1", a["id"], True, a["digest"]),
        collab.decide_approval(factory, _p("lead"), "r1", a["id"], False, a["digest"]),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1


async def test_pending_approval_and_guidance_cursor_survive_restart(factory):
    await _join_all(factory, ["lead", "eng"])
    await collab.claim_leader(factory, _p("lead"), "r1")
    a = await collab.request_approval(factory, _p("eng"), "r1", "delete", ["a.py"], "rm", "-x")
    # the lease holder's guidance is recorded directly; a non-holder's is queued (part 42)
    await collab.redirect(factory, _p("lead"), "r1", "guidance", "use v2", None, None)
    block, seq = await collab.guidance_since(factory, "r1", 0)
    assert "use v2" in block
    await collab.mark_guidance_delivered(factory, "r1", seq)  # jsonb insert on real PG
    # "restart": a brand-new engine/factory on the same database, in-memory state gone
    engine2 = create_async_engine(factory.kw["bind"].url)
    try:
        f2 = async_sessionmaker(engine2, expire_on_commit=False)
        assert await collab.pending_approval(f2, "r1") == a["id"]
        again, _ = await collab.guidance_since(f2, "r1", 0)
        assert again == ""
        done = await collab.decide_approval(f2, _p("lead"), "r1", a["id"], True, a["digest"])
        assert done["state"] == "approved"
    finally:
        await engine2.dispose()


async def test_retention_purge_runs_on_real_postgres(factory, tmp_path):
    from datetime import UTC, datetime, timedelta

    from harness_orchestrator import retention

    await _join_all(factory, ["lead", "eng"])
    await collab.redirect(factory, _p("eng"), "r1", "guidance", "hello", None, None)
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO agent_events (project_id, run_id, type, payload, ts)"
            " VALUES ('p', 'r1', 'engine_completed', '{}', now() - interval '60 days')"
        ))
        await db.commit()
    ws, sess = tmp_path / "ws", tmp_path / "sess"
    (ws / ".staged" / "r1").mkdir(parents=True)
    (sess / "r1").mkdir(parents=True)
    rep = await retention.purge(factory, str(ws), str(sess), 30, apply=True,
                                now=datetime.now(UTC) + timedelta(seconds=1))
    assert rep["runs"] == ["r1"] and rep["removedDirs"] == 2
    async with factory() as db:
        n = (await db.execute(text("SELECT count(*) FROM run_collab_events"))).scalar_one()
    assert n == 0


async def test_collab_audit_rows_form_one_valid_hash_chain_under_concurrency(factory):
    """Every raw-SQL audit writer in collab.py is sealed by migration 0036's trigger, and
    concurrent transactions still produce one linear, verifiable chain (no deadlock)."""
    await _join_all(factory, ["lead", "eng", "eng2"])
    await collab.claim_leader(factory, _p("lead"), "r1")
    a = await collab.request_approval(factory, _p("eng"), "r1", "delete", ["a.py"], "rm", "-x")
    await asyncio.gather(
        collab.redirect(factory, _p("eng"), "r1", "guidance", "one", None, "q1"),
        collab.redirect(factory, _p("eng2"), "r1", "guidance", "two", None, "q2"),
        collab.redirect(factory, _p("lead"), "r1", "scope_change", "narrow", "risk", "q3"),
        collab.decide_approval(factory, _p("lead"), "r1", a["id"], True, a["digest"]),
        return_exceptions=True,
    )
    async with factory() as db:
        bad = (
            await db.execute(
                text(
                    "SELECT count(*) FROM audit_log WHERE chain_pos IS NULL OR row_hash IS DISTINCT"
                    " FROM audit_row_hash(prev_hash, seq, chain_pos, ts, actor_member_id,"
                    " actor_kind, action, target_type, target_id, detail)"
                )
            )
        ).scalar_one()
        linked = (
            await db.execute(
                text(
                    "SELECT count(*) FROM audit_log c JOIN audit_log p"
                    " ON p.chain_pos = c.chain_pos - 1 WHERE c.prev_hash <> p.row_hash"
                )
            )
        ).scalar_one()
        total = (await db.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()
        seqs = [r[0] for r in (await db.execute(text(
            "SELECT seq FROM run_collab_events WHERE run_id='r1' ORDER BY seq"))).all()]
    assert total >= 8 and bad == 0 and linked == 0
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
