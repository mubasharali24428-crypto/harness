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
from harness_identity.audit_chain import verify_chain, verify_window

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


# ------------------------------------------------------------------ W16 slice 4
# The customer-facing window verdict (POST /api/v1/audit/verify) is this
# function; the SQLite lane can only prove the endpoint refuses, so the checks
# that actually re-derive hashes live here, on the engine that seals them.


async def _window(db, **over):
    async with db["engine"].connect() as c:
        return await verify_window(c, **over)


async def test_a_clean_window_answers_row_by_row(db):
    await _seed(db["factory"], 6)
    verdict = await _window(db)
    assert verdict["ok"] is True, verdict["problems"]
    assert verdict["rowsChecked"] == 6
    assert [row["chainPos"] for row in verdict["rows"]] == list(range(1, 7))
    assert all(row["ok"] and row["rowHash"] for row in verdict["rows"])
    assert verdict["prunedPrefix"] is False and verdict["anchored"] is False
    assert verdict["windowComplete"] is True
    assert verdict["head"]["chainPos"] == 6


async def test_the_window_starts_where_the_caller_asked(db):
    await _seed(db["factory"], 6)
    tail = await _window(db, after_chain_pos=4)
    assert [row["chainPos"] for row in tail["rows"]] == [5, 6]
    assert tail["ok"] is True and tail["windowComplete"] is True
    assert tail["anchored"] is False, "a chosen start is not a pruned prefix"
    capped = await _window(db, max_rows=3)
    assert capped["rowsChecked"] == 3 and capped["windowComplete"] is False


async def test_an_edited_row_is_named_and_only_that_row_is_accused(db):
    await _seed(db["factory"], 6)
    await _tamper(db["engine"], "UPDATE audit_log SET target_id = 'rewritten' WHERE chain_pos = 3")
    verdict = await _window(db)
    assert verdict["ok"] is False
    assert [row["chainPos"] for row in verdict["rows"] if not row["ok"]] == [3]
    assert any("row content was modified" in p for p in verdict["problems"])
    # The edit is not re-sealed, so the NEXT row's link is untouched: the trail is
    # provably the one the database wrote everywhere else.
    assert verdict["windowComplete"] is True


async def test_a_row_removed_from_the_middle_breaks_the_link_ahead_of_it(db):
    await _seed(db["factory"], 6)
    async with db["engine"].begin() as c:
        await c.execute(text("ALTER TABLE audit_log DISABLE TRIGGER ALL"))
        await c.execute(text("DELETE FROM audit_log WHERE chain_pos = 4"))
    verdict = await _window(db)
    assert verdict["ok"] is False
    assert any("gap in chain: positions 3 -> 5" in p for p in verdict["problems"])


async def test_a_pruned_prefix_is_declared_instead_of_invented(db):
    await _seed(db["factory"], 6)
    async with db["engine"].begin() as c:
        await c.execute(text("ALTER TABLE audit_log DISABLE TRIGGER ALL"))
        await c.execute(text("DELETE FROM audit_log WHERE chain_pos <= 2"))
    verdict = await _window(db)
    assert verdict["prunedPrefix"] is True
    assert verdict["firstChainPos"] == 3 and verdict["rowsChecked"] == 4
    assert verdict["anchored"] is True, "the missing predecessor must be said out loud"
    # The surviving suffix is intact: retention is not tampering, and a verdict
    # that called it broken would train an operator to ignore broken verdicts.
    assert verdict["ok"] is True, verdict["problems"]


async def test_a_resealed_chain_is_honest_about_what_the_window_can_prove(db):
    """A privileged attacker who re-derives every hash from the edit onward makes
    the chain internally consistent. The window cannot see that — the anchors
    can — so the endpoint must not be sold as tamper-PROOF, only tamper-evident
    against a rewrite that did not re-seal. Pinned here so the docs stay honest."""
    await _seed(db["factory"], 6)
    await _tamper(db["engine"], "UPDATE audit_log SET target_id = 'rewritten' WHERE chain_pos = 3")
    await _rebuild_from(db["engine"], 3)
    verdict = await _window(db)
    assert verdict["ok"] is True, verdict["problems"]
    async with db["engine"].connect() as c:
        rep = await verify_chain(c)
    assert rep.ok


async def test_a_deleted_newest_row_is_caught_by_the_window(db):
    """Tail deletion is the case a hash chain alone cannot show (nothing links to
    the removed row). The stored head can, so the window uses it — otherwise a
    customer would export yesterday's trail, someone would delete today's rows,
    and every verdict would still read clean."""
    await _seed(db["factory"], 6)
    async with db["engine"].begin() as c:
        await c.execute(text("ALTER TABLE audit_log DISABLE TRIGGER ALL"))
        await c.execute(text("DELETE FROM audit_log WHERE chain_pos = 6"))
    verdict = await _window(db)
    assert verdict["ok"] is False
    assert verdict["windowComplete"] is False
    assert any("newest rows removed" in p for p in verdict["problems"])


async def test_a_forged_link_is_caught_even_when_the_rows_are_self_consistent(db):
    """Each row's own hash covers its own ``prev_hash``, so a splice can be
    re-sealed row by row and still be internally correct. This is the case only
    the seam check can see: row 4 is rewritten to hang off the genesis hash and
    re-sealed, so rows 4 and 5 each hash correctly against their own stored
    predecessor — and the trail is nonetheless not the one the database wrote.
    Without a link check, a window verdict here would read clean over a splice."""
    await _seed(db["factory"], 6)
    await _tamper(
        db["engine"],
        "UPDATE audit_log SET prev_hash = CAST(:g AS varchar),"
        " row_hash = audit_row_hash(CAST(:g AS varchar), seq, chain_pos, ts,"
        "                           actor_member_id, actor_kind, action, target_type,"
        "                           target_id, detail)"
        " WHERE chain_pos = 4",
        {"g": "0" * 64},
    )
    verdict = await _window(db)
    assert verdict["ok"] is False
    assert all(row["ok"] is False for row in verdict["rows"] if row["chainPos"] in (4, 5))
    assert all(row["ok"] is True for row in verdict["rows"] if row["chainPos"] not in (4, 5))
    assert any("prev_hash does not link" in p for p in verdict["problems"])
