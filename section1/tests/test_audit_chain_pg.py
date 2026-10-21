"""Part 11.004: the audit_log hash chain (migration 0036) on a real Postgres. Parts 11 and 29
(session 12) add the external anchor of the chain head at the end of this file.

Opt-in like the other live-PG suites (HARNESS_TEST_PG=1, default DSN is the local
pilot DB server; each test builds its own throwaway database, the live harness
database is never touched). SQLite cannot run the trigger.
"""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.audit import write_audit
from harness_identity.audit_anchor import anchor_head, read_anchors, verify_anchors
from harness_identity.audit_chain import verify_chain

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "section1" / "scripts" / "verify_audit_chain.py"

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG audit chain tests skipped",
)

_spec = importlib.util.spec_from_file_location(
    "harness_migrate", ROOT / "deploy" / "runners" / "migrate.py"
)
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


async def _apply(conn, upto: str | None = None) -> None:
    for path in sorted((ROOT / "section1" / "migrations").glob("*.sql")):
        if upto and path.name.split("_", 1)[0] > upto:
            break
        for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
            await conn.execute(stmt)


@pytest.fixture
async def db():
    import asyncpg

    name = f"harness_chain_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        await _apply(conn)
    finally:
        await conn.close()
    url = f"{base}/{name}"
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    yield {"engine": engine, "factory": async_sessionmaker(engine, expire_on_commit=False),
           "url": url, "name": name}
    await engine.dispose()
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _raw_audit(factory, action: str, detail: dict) -> None:
    """The section4 writer: raw SQL, no ORM."""
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type,"
                " target_id, detail, ts) VALUES (NULL, 'human', :a, 'agent_run', 'r1', :d, now())"
            ),
            {"a": action, "d": json.dumps(detail)},
        )
        await s.commit()


async def _verify(engine):
    async with engine.connect() as c:
        return await verify_chain(c)


async def _tamper(engine, sql: str, params: dict | None = None) -> None:
    """Simulate a DBA/attacker editing the table behind the app (guard trigger off)."""
    async with engine.begin() as c:
        await c.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update"))
        await c.execute(text(sql), params or {})
        await c.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update"))


async def _seed(factory, n=6) -> None:
    for i in range(n):
        if i % 2:
            async with factory() as s:
                await write_audit(s, "authz.denied", target_type="agent_run", target_id="r1",
                                  detail={"i": i, "teamId": "t1"})
        else:
            await _raw_audit(factory, "approval.requested", {"i": i, "teamId": "t1"})


async def test_both_writers_are_chained_and_verify_clean(db):
    await _seed(db["factory"], 6)
    rep = await _verify(db["engine"])
    assert rep.ok, rep.problems
    assert rep.rows_checked == 6 and rep.first_pos == 1 and rep.last_pos == 6


async def test_concurrent_writers_keep_one_linear_chain(db):
    await asyncio.gather(
        *(_raw_audit(db["factory"], "approval.requested", {"i": i}) for i in range(25))
    )
    rep = await _verify(db["engine"])
    assert rep.ok, rep.problems
    assert rep.rows_checked == 25


async def test_rolled_back_audit_leaves_no_gap(db):
    async with db["factory"]() as s:
        await s.execute(
            text(
                "INSERT INTO audit_log (actor_kind, action, target_type, target_id, detail, ts)"
                " VALUES ('human', 'x', 'agent_run', 'r1', '{}', now())"
            )
        )
        await s.rollback()
    await _seed(db["factory"], 2)
    assert (await _verify(db["engine"])).ok


async def test_editing_a_row_fails_verification(db):
    await _seed(db["factory"], 6)
    await _tamper(
        db["engine"], "UPDATE audit_log SET action = 'approval.rejected' WHERE chain_pos = 3"
    )
    rep = await _verify(db["engine"])
    assert not rep.ok
    assert any("chain_pos 3" in p and "modified" in p for p in rep.problems), rep.problems


async def test_editing_detail_before_after_values_fails_verification(db):
    await _seed(db["factory"], 4)
    await _tamper(
        db["engine"], "UPDATE audit_log SET detail = detail || '{\"after\":{\"role\":\"admin\"}}' "
        "WHERE chain_pos = 2"
    )
    assert not (await _verify(db["engine"])).ok


async def test_editing_the_stored_hash_too_still_breaks_the_next_link(db):
    """An attacker who recomputes row N's hash cannot fix row N+1's prev_hash."""
    await _seed(db["factory"], 5)
    await _tamper(db["engine"], "UPDATE audit_log SET action = 'forged' WHERE chain_pos = 2")
    async with db["engine"].connect() as c:
        fixed = (
            await c.execute(
                text(
                    "SELECT audit_row_hash(prev_hash, seq, chain_pos, ts, actor_member_id,"
                    " actor_kind, action, target_type, target_id, detail) FROM audit_log"
                    " WHERE chain_pos = 2"
                )
            )
        ).scalar_one()
    await _tamper(
        db["engine"], "UPDATE audit_log SET row_hash = :h WHERE chain_pos = 2", {"h": fixed}
    )
    rep = await _verify(db["engine"])
    assert not rep.ok and any("prev_hash does not link" in p for p in rep.problems)


async def test_deleting_a_middle_row_fails_verification(db):
    await _seed(db["factory"], 6)
    async with db["engine"].begin() as c:
        await c.execute(text("DELETE FROM audit_log WHERE chain_pos = 3"))
    rep = await _verify(db["engine"])
    assert not rep.ok and any("gap in chain" in p for p in rep.problems)


async def test_deleting_the_newest_rows_fails_verification(db):
    await _seed(db["factory"], 6)
    async with db["engine"].begin() as c:
        await c.execute(text("DELETE FROM audit_log WHERE chain_pos >= 5"))
    rep = await _verify(db["engine"])
    assert not rep.ok and any("chain head" in p for p in rep.problems)


async def test_row_written_around_the_trigger_is_flagged(db):
    await _seed(db["factory"], 3)
    async with db["engine"].begin() as c:
        await c.execute(text("SET LOCAL session_replication_role = replica"))
        await c.execute(
            text(
                "INSERT INTO audit_log (actor_kind, action, target_type, target_id, detail, ts)"
                " VALUES ('human', 'planted', 'agent_run', 'r1', '{}', now())"
            )
        )
    rep = await _verify(db["engine"])
    assert not rep.ok and any("not in the chain" in p for p in rep.problems)


async def test_update_is_refused_by_the_database(db):
    await _seed(db["factory"], 2)
    with pytest.raises(Exception, match="append-only"):
        async with db["engine"].begin() as c:
            await c.execute(text("UPDATE audit_log SET action = 'x' WHERE chain_pos = 1"))


async def test_retention_pruning_oldest_rows_keeps_the_rest_verifiable(db):
    await _seed(db["factory"], 6)
    async with db["engine"].begin() as c:
        await c.execute(text("DELETE FROM audit_log WHERE chain_pos <= 2"))
    rep = await _verify(db["engine"])
    assert rep.ok, rep.problems
    assert rep.pruned_prefix and rep.first_pos == 3


async def test_migration_backfills_pre_existing_rows(db):
    import asyncpg

    base = _server_dsn().rsplit("/", 1)[0]
    name = f"harness_chain_bf_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(_server_dsn())
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        conn = await asyncpg.connect(f"{base}/{name}")
        await _apply(conn, upto="0035")
        for i in range(4):
            await conn.execute(
                "INSERT INTO audit_log (actor_kind, action, target_type, target_id, detail, ts)"
                " VALUES ('human', 'legacy', 'agent_run', 'r', $1::jsonb, now())",
                json.dumps({"i": i}),
            )
        await _apply_one(conn, "0036")
        await conn.close()
        engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
        try:
            rep = await _verify(engine)
            assert rep.ok and rep.rows_checked == 4, rep.problems
        finally:
            await engine.dispose()
    finally:
        admin = await asyncpg.connect(_server_dsn())
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _apply_one(conn, version: str) -> None:
    path = next((ROOT / "section1" / "migrations").glob(f"{version}_*.sql"))
    for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
        await conn.execute(stmt)


async def test_cli_exit_codes_intact_then_edited(db):
    await _seed(db["factory"], 4)
    run = lambda: subprocess.run(  # noqa: E731
        [sys.executable, str(SCRIPT), db["url"]], capture_output=True, text=True, timeout=60
    )
    ok = await asyncio.to_thread(run)
    assert ok.returncode == 0 and "audit chain OK" in ok.stdout, ok.stdout + ok.stderr
    await _tamper(db["engine"], "UPDATE audit_log SET target_id = 'other' WHERE chain_pos = 2")
    bad = await asyncio.to_thread(run)
    assert bad.returncode == 1 and "BROKEN" in bad.stdout and "chain_pos 2" in bad.stdout


# ---------------------------------------------------------------- external anchor (part 11/29)


async def _rebuild_from(engine, pos: int) -> None:
    """A privileged attacker: re-seal every row from chain position ``pos`` to the head and move
    the head pointer, so the chain is internally consistent again."""
    async with engine.begin() as c:
        await c.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update"))
        prev = (await c.execute(
            text("SELECT row_hash FROM audit_log WHERE chain_pos = :p"), {"p": pos - 1}
        )).scalar_one()
        seqs = (await c.execute(
            text("SELECT seq FROM audit_log WHERE chain_pos >= :p ORDER BY chain_pos"), {"p": pos}
        )).scalars().all()
        for seq in seqs:
            prev = (await c.execute(text(
                "UPDATE audit_log SET prev_hash = :pv, row_hash = audit_row_hash(:pq, seq,"
                " chain_pos, ts, actor_member_id, actor_kind, action, target_type, target_id,"
                " detail) WHERE seq = :s RETURNING row_hash"
            ), {"pv": prev, "pq": prev, "s": seq})).scalar_one()
        await c.execute(text("UPDATE audit_chain_head SET last_hash = :h WHERE id = 1"),
                        {"h": prev})
        await c.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update"))


async def _head_seq(engine) -> int:
    async with engine.connect() as c:
        return (await c.execute(text("SELECT max(seq) FROM audit_log"))).scalar_one()


async def test_an_untouched_chain_passes_and_only_a_moved_head_is_appended(db, tmp_path):
    await _seed(db["factory"], 6)
    sink = str(tmp_path / "anchors.jsonl")
    anchor = await anchor_head(db["factory"], sink)
    assert anchor["seq"] == await _head_seq(db["engine"])
    async with db["engine"].connect() as c:
        assert await verify_anchors(c, read_anchors(sink)) == []
    assert await anchor_head(db["factory"], sink) is None  # the head has not moved
    await _seed(db["factory"], 2)  # rows written after the anchor leave it intact
    async with db["engine"].connect() as c:
        assert await verify_anchors(c, read_anchors(sink)) == []
    assert (await anchor_head(db["factory"], sink)) is not None
    assert len(read_anchors(sink)) == 2


async def test_rebuilding_the_chain_after_an_edit_passes_verify_chain_but_not_the_anchor(
    db, tmp_path
):
    await _seed(db["factory"], 6)
    sink = str(tmp_path / "anchors.jsonl")
    await anchor_head(db["factory"], sink)
    await _tamper(db["engine"], "UPDATE audit_log SET target_id = 'other' WHERE chain_pos = 2")
    await _rebuild_from(db["engine"], 2)
    rep = await _verify(db["engine"])
    assert rep.ok, rep.problems  # the attacker's consistent chain is invisible to the chain check
    async with db["engine"].connect() as c:
        problems = await verify_anchors(c, read_anchors(sink))
    assert problems and "altered after anchoring" in problems[0]


async def test_deleting_the_anchored_head_is_detected(db, tmp_path):
    await _seed(db["factory"], 4)
    sink = str(tmp_path / "anchors.jsonl")
    await anchor_head(db["factory"], sink)
    async with db["engine"].begin() as c:
        await c.execute(text("DELETE FROM audit_log WHERE chain_pos = (SELECT max(chain_pos)"
                             " FROM audit_log)"))
    async with db["engine"].connect() as c:
        problems = await verify_anchors(c, read_anchors(sink))
    assert problems and "is gone" in problems[0]


async def test_the_verify_command_exits_1_only_after_an_anchored_row_is_altered(db, tmp_path):
    await _seed(db["factory"], 6)
    sink = str(tmp_path / "anchors.jsonl")
    await anchor_head(db["factory"], sink)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(ROOT / "section1" / "src"), str(ROOT / "contracts" / "src")]))

    def verify():
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--anchors", sink, db["url"]],
            env=env, capture_output=True, text=True, timeout=60,
        )

    intact = verify()
    assert intact.returncode == 0, intact.stdout + intact.stderr
    assert "anchors checked" in intact.stdout
    await _tamper(db["engine"], "UPDATE audit_log SET target_id = 'other' WHERE chain_pos = 2")
    await _rebuild_from(db["engine"], 2)
    broken = verify()
    assert broken.returncode == 1, broken.stdout + broken.stderr
    assert "altered after anchoring" in broken.stdout
