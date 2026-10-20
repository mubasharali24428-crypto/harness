"""P3.2: retention/cleanup of diffs, collab events, staged files, baselines, sessions."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import retention

NOW = datetime(2026, 10, 18, tzinfo=UTC)
OLD, NEW = NOW - timedelta(days=45), NOW - timedelta(days=2)

DDL = (
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
    " state VARCHAR(40) NOT NULL, updated_at TIMESTAMP)",
    "CREATE TABLE run_retention_holds (run_id VARCHAR(36) PRIMARY KEY, held_by VARCHAR(36),"
    " reason VARCHAR(500), created_at TIMESTAMP)",
    "CREATE TABLE high_impact_approvals (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " state VARCHAR(16) NOT NULL, diff TEXT NOT NULL DEFAULT '', created_at TIMESTAMP)",
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36) NOT NULL, kind VARCHAR(40) NOT NULL, ts TIMESTAMP)",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, action VARCHAR(80),"
    " target_id VARCHAR(80), ts TIMESTAMP)",
    "CREATE TABLE agent_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),"
    " type VARCHAR(60), ts TIMESTAMP)",
)


@pytest.fixture()
def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/r.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ws, sess = tmp_path / "ws", tmp_path / "sess"

    async def seed():
        async with engine.begin() as c:
            for d in DDL:
                await c.execute(text(d))
            # NOTE agent_runs.state is never maintained by the orchestrator (always 'idle'
            # in production): "finished" must come from events / the stop audit row.
            runs = [
                ("old-done", "event", OLD),  # engine_completed long ago
                ("new-done", "event", NEW),  # finished recently
                ("old-running", None, OLD),  # never finished
                ("old-pending", "event", OLD),  # finished but a decision is still open
                ("old-stopped", "stop", OLD),  # finished by a stop (audit row only)
            ]
            for rid, how, ts in runs:
                await c.execute(
                    text("INSERT INTO agent_runs (id, state, updated_at) VALUES (:i, 'idle', :t)"),
                    {"i": rid, "t": ts},
                )
                if how == "event":
                    await c.execute(
                        text(
                            "INSERT INTO agent_events (run_id, type, ts)"
                            " VALUES (:i, 'engine_completed', :t)"
                        ),
                        {"i": rid, "t": ts},
                    )
                if how == "stop":
                    await c.execute(
                        text(
                            "INSERT INTO audit_log (action, target_id, ts)"
                            " VALUES ('run.stopped', :i, :t)"
                        ),
                        {"i": rid, "t": ts},
                    )
                await c.execute(
                    text("INSERT INTO run_collab_events (run_id, kind, ts) VALUES (:i, 'x', :t)"),
                    {"i": rid, "t": ts},
                )
                await c.execute(
                    text(
                        "INSERT INTO high_impact_approvals (id, run_id, state, diff, created_at)"
                        " VALUES (:a, :i, :s, '-secret diff', :t)"
                    ),
                    {"a": f"a-{rid}", "i": rid, "t": ts,
                     "s": "pending" if rid == "old-pending" else "completed"},
                )
            await c.execute(
                text(
                    "INSERT INTO run_retention_holds (run_id, held_by, reason, created_at)"
                    " VALUES ('old-done', 'admin-1', 'preserve for investigation', :t)"
                ),
                {"t": OLD},
            )
            await c.execute(
                text(
                    "INSERT INTO audit_log (action, target_id, ts)"
                    " VALUES ('approval.completed', 'x', :t)"
                ),
                {"t": OLD},
            )

    asyncio.run(seed())
    for rid in ("old-done", "new-done", "old-running", "old-pending", "old-stopped"):
        for d in (ws / ".baseline" / rid, ws / ".staged" / rid, sess / rid, ws / rid):
            d.mkdir(parents=True)
            (d / "f.txt").write_text("data")
    yield factory, str(ws), str(sess)
    asyncio.run(engine.dispose())


def q(factory, sql):
    async def go():
        async with factory() as db:
            return (await db.execute(text(sql))).all()

    return asyncio.run(go())


def test_dry_run_changes_nothing_but_reports(env):
    f, ws, sess = env
    rep = asyncio.run(retention.purge(f, ws, sess, days=30, apply=False, now=NOW))
    assert rep["runs"] == ["old-stopped"]
    assert os.path.isdir(f"{ws}/.staged/old-done")
    assert q(f, "SELECT diff FROM high_impact_approvals WHERE run_id='old-done'")[0].diff


def test_apply_purges_only_finished_old_runs_with_no_open_decision(env):
    f, ws, sess = env
    rep = asyncio.run(retention.purge(f, ws, sess, days=30, apply=True, now=NOW))
    assert rep["runs"] == ["old-stopped"]
    for gone in (
        f"{sess}/old-stopped",
    ):
        assert not os.path.exists(gone)
    assert os.path.isdir(f"{ws}/old-done")  # outputs are kept unless asked
    assert os.path.isdir(f"{sess}/old-done")  # held run's session is preserved
    assert os.path.isdir(f"{ws}/.staged/old-done")
    for kept in ("new-done", "old-running", "old-pending"):
        assert os.path.isdir(f"{ws}/.staged/{kept}") and os.path.isdir(f"{sess}/{kept}")
    diffs = {r.run_id: r.diff for r in q(f, "SELECT run_id, diff FROM high_impact_approvals")}
    assert diffs["old-done"] == "-secret diff" and diffs["old-pending"] == "-secret diff"
    events = {r.run_id for r in q(f, "SELECT run_id FROM run_collab_events")}
    assert "old-done" in events and {"new-done", "old-running", "old-pending"} <= events
    assert len(q(f, "SELECT * FROM audit_log")) == 2  # audit trail is never purged


def test_include_workspace_is_opt_in(env):
    f, ws, sess = env
    asyncio.run(
        retention.purge(f, ws, sess, days=30, apply=True, include_workspace=True, now=NOW)
    )
    assert os.path.isdir(f"{ws}/old-done")  # legal hold wins over workspace purge
    assert os.path.isdir(f"{ws}/old-stopped") is False
    assert os.path.isdir(f"{ws}/new-done")


def test_hostile_run_ids_never_become_paths(env, tmp_path):
    f, ws, sess = env
    victim = tmp_path / "victim"
    victim.mkdir()

    async def plant():
        async with f() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_runs (id, state, updated_at)"
                    " VALUES ('../victim', 'idle', :t)"
                ),
                {"t": OLD},
            )
            await db.execute(
                text(
                    "INSERT INTO agent_events (run_id, type, ts)"
                    " VALUES ('../victim', 'engine_completed', :t)"
                ),
                {"t": OLD},
            )
            await db.commit()

    asyncio.run(plant())
    rep = asyncio.run(
        retention.purge(f, ws, sess, days=30, apply=True, include_workspace=True, now=NOW)
    )
    assert victim.is_dir() and "../victim" not in rep["runs"]


def test_resumed_run_with_recent_activity_is_not_purged(env):
    f, ws, sess = env

    async def resume():
        async with f() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_events (run_id, type, ts)"
                    " VALUES ('old-done', 'tool_call', :t)"
                ),
                {"t": NEW},
            )
            await db.commit()

    asyncio.run(resume())
    rep = asyncio.run(retention.purge(f, ws, sess, days=30, apply=True, now=NOW))
    assert rep["runs"] == ["old-stopped"]
    assert os.path.isdir(f"{sess}/old-done")
