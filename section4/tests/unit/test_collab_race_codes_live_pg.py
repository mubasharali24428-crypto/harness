"""Part 14 (session 12): the two race-only collaboration codes, provoked on real Postgres.

`HANDOFF_DECIDED` and `LEADER_CHANGED` are refused only when a concurrent commit lands between a
read and a conditional UPDATE, so SQLite cannot produce them. Each test holds the conflicting row
lock in a second transaction, starts the racing call, proves it is blocked on that lock, then
commits the other side. Opt-in (HARNESS_TEST_PG); each test uses its own throwaway database.
"""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from harness_contracts.roles import Principal, Role
from sqlalchemy import text

from harness_orchestrator import collab

_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG race-code tests skipped",
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

    dbname = f"harness_s12_race_{uuid.uuid4().hex[:10]}"
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


async def _join_all(f, users):
    for u in users:
        await collab.join(f, _p(u), "r1")


async def test_handoff_decided_when_another_decision_commits_first(factory):
    await _join_all(factory, ["lead", "eng"])
    await collab.claim_leader(factory, _p("lead"), "r1")
    handoff = await collab.request_handoff(factory, _p("lead"), "r1", "eng")
    async with factory() as hold:  # a rejection that has not committed yet
        await hold.execute(
            text("UPDATE run_handoffs SET state = 'rejected' WHERE id = :i"),
            {"i": handoff["handoffId"]},
        )
        racer = asyncio.ensure_future(
            collab.decide_handoff(factory, _p("eng"), "r1", handoff["handoffId"], True)
        )
        await asyncio.sleep(0.5)
        assert not racer.done(), "the accept should wait on the row lock held by the rejection"
        await hold.commit()
    with pytest.raises(collab.CollabError) as refused:
        await racer
    assert (refused.value.status, refused.value.code) == (409, "HANDOFF_DECIDED")


async def test_leader_changed_when_the_proposer_is_demoted_during_accept(factory):
    await _join_all(factory, ["lead", "eng"])
    await collab.claim_leader(factory, _p("lead"), "r1")
    handoff = await collab.request_handoff(factory, _p("lead"), "r1", "eng")
    async with factory() as demote:  # a concurrent leader change that has not committed yet
        await demote.execute(
            text("UPDATE run_participants SET role = 'participant'"
                 " WHERE run_id = 'r1' AND user_id = 'lead'")
        )
        racer = asyncio.ensure_future(
            collab.decide_handoff(factory, _p("eng"), "r1", handoff["handoffId"], True)
        )
        await asyncio.sleep(0.5)
        assert not racer.done(), "the accept should wait on the proposer's row lock"
        await demote.commit()
    with pytest.raises(collab.CollabError) as refused:
        await racer
    assert (refused.value.status, refused.value.code) == (409, "LEADER_CHANGED")
    async with factory() as db:
        state = (
            await db.execute(
                text("SELECT state FROM run_handoffs WHERE id = :i"), {"i": handoff["handoffId"]}
            )
        ).scalar_one()
    assert state == "pending", "the refused accept must roll back its handoff update"
