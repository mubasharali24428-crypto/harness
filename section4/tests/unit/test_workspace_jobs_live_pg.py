"""Part 39 workspace lifecycle jobs on a real PostgreSQL; opt in with HARNESS_TEST_PG=1.

The sqlite tests use a stand-in schema (text timestamps, no foreign keys, a text audit detail).
Here the gate lookup, the archive-stop sweep and the purge run against the real migration chain,
where `purge_after` and `updated_at` are timestamptz and `audit_log.detail` is jsonb.
"""

import importlib.util
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState
from harness_orchestrator.workspace_gate import (
    WorkspaceNotActiveError,
    purge_expired_workspaces,
    sweep_inactive_workspace_runs,
    workspace_block,
)
from tests.unit.test_workspace_gate import StubEngine, _approve

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; workspace jobs PG probe skipped",
)
_spec = importlib.util.spec_from_file_location(
    "harness_migrate_wsjobs", ROOT / "deploy/runners/migrate.py"
)
assert _spec is not None and _spec.loader is not None
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)

# project -> (workspace, state)
WORKSPACES = {
    "p-active": ("ws-active", "active"), "p-paused": ("ws-paused", "paused"),
    "p-arch": ("ws-arch", "archived"), "p-del": ("ws-del", "deleted"),
}


def server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else (os.environ.get("HARNESS_TEST_PG_DSN") or "postgresql://harness:harness@127.0.0.1:5499/harness")


@pytest.fixture
async def database():
    """A throwaway database, dropped even when the fixture that fills it fails."""
    import asyncpg

    name = f"harness_wsj_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    yield name
    admin = await asyncpg.connect(server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture
async def env(database):
    import asyncpg

    base = server_dsn().rsplit("/", 1)[0]
    name = database
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        for path in sorted((ROOT / "section1/migrations").glob("*.sql")):
            for stmt in migrate.split_statements(path.read_text(encoding="utf-8")):
                if stmt.strip():
                    await conn.execute(stmt)
        # migrate() rolls the partition window forward; this hand-split loop
        # does not, so replicate that post-migration step or current-month
        # agent_events inserts fail.
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        _engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
        try:
            await migrate.ensure_forward_partitions(
                async_sessionmaker(_engine, expire_on_commit=False)
            )
        finally:
            await _engine.dispose()
        await conn.execute("INSERT INTO teams (id, name) VALUES ('team-a', 'a')")
        await conn.execute("INSERT INTO users (id, email, name, is_active)"
                           " VALUES ('u1', 'u1@example.test', 'u1', true)")
        for pid, (wid, state) in WORKSPACES.items():
            await conn.execute(
                "INSERT INTO workspaces (id, team_id, name, name_key, state, owner_user_id,"
                " tags, settings, template_name, state_reason, created_at, updated_at)"
                " VALUES ($1, 'team-a', $1, $1, $2, 'u1', '[\"x\"]', '{\"k\": 1}', 'tpl', 'why',"
                " now(), now())", wid, state)
            await conn.execute("INSERT INTO projects (id, team_id, name, workspace_id)"
                               " VALUES ($1, 'team-a', $1, $2)", pid, wid)
    finally:
        await conn.close()
    engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = OrchestratorService(engine=StubEngine(), session_factory=factory)
    e = type("Env", (), {})()
    e.svc, e.factory, e.dsn = svc, factory, f"{base}/{name}"
    yield e
    svc.shutdown()
    await engine.dispose()


async def new_run(env, run_id, project_id):
    """A run row (plans reference it), then an approved plan, as the API leaves it."""
    async with env.factory() as db:
        await db.execute(text("INSERT INTO agent_runs (id, project_id, state)"
                              " VALUES (:r, :p, 'idle')"), {"r": run_id, "p": project_id})
        await db.commit()
    await _approve(env.svc, run_id, project_id)


async def claim_running(factory, run_id, project_id):
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at,"
            " worker_id) VALUES (:r, :p, 'running', 't', 't', 'other-worker')"),
            {"r": run_id, "p": project_id})
        await db.commit()


async def audit(factory, action):
    async with factory() as db:
        return (await db.execute(
            text("SELECT target_id, detail FROM audit_log WHERE action = :a ORDER BY seq"),
            {"a": action})).all()


async def test_the_gate_reads_each_workspace_state(env):
    for pid, (wid, state) in WORKSPACES.items():
        expected = None if state == "active" else (wid, state)
        assert await workspace_block(env.factory, pid) == expected


async def test_a_resume_in_an_archived_workspace_is_refused(env):
    await new_run(env, "r-arch", "p-arch")
    with pytest.raises(WorkspaceNotActiveError):
        await env.svc.resume_agent("r-arch")


async def test_the_sweep_stops_only_runs_in_archived_or_deleted_workspaces(env, caplog):
    for rid, pid in (("s-arch", "p-arch"), ("s-del", "p-del"), ("s-paused", "p-paused"),
                     ("s-active", "p-active")):
        await new_run(env, rid, pid)
        await claim_running(env.factory, rid, pid)
    assert sorted(await sweep_inactive_workspace_runs(env.svc)) == ["s-arch", "s-del"]
    assert env.svc.runs["s-arch"]["state_machine"].state is AgentState.DONE
    assert env.svc.runs["s-paused"]["state_machine"].state is not AgentState.DONE
    assert await sweep_inactive_workspace_runs(env.svc) == []  # the claims were released
    stops = await audit(env.factory, "workspace.run_stopped")
    assert sorted(r.target_id for r in stops) == ["ws-arch", "ws-del"]
    assert {r.detail["runId"] for r in stops} == {"s-arch", "s-del"}
    # the service swallows persistence errors into its log: none may have happened
    assert [r.getMessage() for r in caplog.records if r.levelname == "ERROR"] == []


async def test_the_purge_scrubs_only_expired_deleted_workspaces_and_is_idempotent(env):
    import asyncpg
    now = datetime(2026, 11, 1, tzinfo=UTC)
    conn = await asyncpg.connect(env.dsn)
    try:
        await conn.execute("UPDATE workspaces SET purge_after = $1 WHERE id = 'ws-del'",
                           now - timedelta(days=1))
        await conn.execute(
            "INSERT INTO workspaces (id, team_id, name, name_key, state, owner_user_id,"
            " created_at, updated_at, purge_after) VALUES"
            " ('ws-later', 'team-a', 'later', 'later', 'deleted', 'u1', now(), now(), $1),"
            " ('ws-arch2', 'team-a', 'arch2', 'arch2', 'archived', 'u1', now(), now(), $2)",
            now + timedelta(days=1), now - timedelta(days=1))
        for wid in ("ws-del", "ws-later"):
            await conn.execute("INSERT INTO workspace_members (workspace_id, user_id, role,"
                               " created_at) VALUES ($1, 'u1', 'member', now())", wid)
            await conn.execute("INSERT INTO workspace_bindings (id, workspace_id, kind, value,"
                               " created_by, created_at)"
                               " VALUES ($1, $2, 'repository', 'r', 'u1', now())",
                               f"b-{wid}", wid)
            await conn.execute(
                "INSERT INTO workspace_settings_versions (id, workspace_id, version, before_json,"
                " after_json, changed_by, created_at) VALUES ($1, $2, 1, '{}', '{}', 'u1', now())",
                f"v-{wid}", wid)
    finally:
        await conn.close()
    assert await purge_expired_workspaces(env.factory, now=now) == ["ws-del"]
    assert await purge_expired_workspaces(env.factory, now=now) == []  # a rerun is a no-op
    conn = await asyncpg.connect(env.dsn)
    try:
        ws = await conn.fetchrow("SELECT * FROM workspaces WHERE id = 'ws-del'")
        assert (ws["name"], ws["name_key"], ws["tags"], ws["settings"], ws["state_reason"],
                ws["template_name"], ws["state"]) == (
            "[purged]", "ws-del", "[]", "{}", None, None, "deleted")
        for table in ("workspace_members", "workspace_bindings", "workspace_settings_versions"):
            left = await conn.fetch(f"SELECT workspace_id FROM {table}")
            assert [r["workspace_id"] for r in left] == ["ws-later"], table
        names = {r["id"]: r["name"] for r in await conn.fetch(
            "SELECT id, name FROM workspaces WHERE id IN ('ws-later', 'ws-arch2')")}
        assert names == {"ws-later": "later", "ws-arch2": "arch2"}  # not expired / not deleted
    finally:
        await conn.close()
    assert [r.target_id for r in await audit(env.factory, "workspace.purged")] == ["ws-del"]
    assert await workspace_block(env.factory, "p-del") == ("ws-del", "deleted")
