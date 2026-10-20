"""Part 39.008 follow-ups: gated resume, runs stop on archive, deleted workspaces purge."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState
from harness_orchestrator.workspace_gate import (
    WorkspaceNotActiveError,
    purge_expired_workspaces,
    sweep_inactive_workspace_runs,
    workspace_block,
)

from .test_workspace_gate import _DDL, StubEngine, _approve, _seed

_WS_DDL = """
CREATE TABLE workspaces (
    id VARCHAR(36) PRIMARY KEY,
    state VARCHAR(16) NOT NULL DEFAULT 'active',
    name VARCHAR(80) NOT NULL DEFAULT 'ws',
    name_key VARCHAR(80) NOT NULL DEFAULT 'ws',
    state_reason VARCHAR(500),
    tags TEXT NOT NULL DEFAULT 'tags',
    settings TEXT NOT NULL DEFAULT 'settings',
    template_name VARCHAR(80),
    updated_at TEXT,
    purge_after TEXT
);
CREATE TABLE workspace_members (workspace_id VARCHAR(36) NOT NULL, user_id VARCHAR(36) NOT NULL);
CREATE TABLE workspace_bindings (workspace_id VARCHAR(36) NOT NULL, value VARCHAR(40) NOT NULL);
CREATE TABLE workspace_settings_versions (workspace_id VARCHAR(36) NOT NULL, version INTEGER);
"""
_DDL_FULL = _DDL.replace(
    "CREATE TABLE workspaces (\n    id VARCHAR(36) PRIMARY KEY,\n"
    "    state VARCHAR(16) NOT NULL DEFAULT 'active'\n);\n", _WS_DDL)
assert _DDL_FULL != _DDL


@pytest.fixture
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for stmt in _DDL_FULL.split(";"):
            if stmt.strip():
                await conn.execute(text(stmt.strip()))
    await _seed(factory, {"p-active": ("ws-active", "active"), "p-paused": ("ws-paused", "paused"),
                          "p-arch": ("ws-arch", "archived"), "p-del": ("ws-del", "deleted")})
    svc = OrchestratorService(engine=StubEngine(), session_factory=factory)
    yield svc, factory
    await asyncio.gather(*[r["task"] for r in svc.runs.values() if r.get("task") is not None],
                         return_exceptions=True)  # sqlite: await spawned tasks before dispose
    svc.shutdown()
    await engine.dispose()


async def _claim_running(factory, run_id, project_id):
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at,"
            " worker_id) VALUES (:r, :p, 'running', 't', 't', 'other-worker')"),
            {"r": run_id, "p": project_id})
        await db.commit()


async def _actions(factory, action):
    async with factory() as db:
        rows = (await db.execute(
            text("SELECT target_id, detail FROM audit_log WHERE action=:a"), {"a": action})).all()
    return [(r.target_id, json.loads(r.detail)) for r in rows]


async def test_resume_is_refused_in_a_non_active_workspace(env):
    svc, _ = env
    await _approve(svc, "r-arch", "p-arch")
    with pytest.raises(WorkspaceNotActiveError):
        await svc.resume_agent("r-arch")


async def test_resume_still_works_in_active_workspace_and_terminal_resume_is_a_noop(env):
    svc, _ = env
    await _approve(svc, "r-ok", "p-active")
    await svc.resume_agent("r-ok")  # no 409
    await _approve(svc, "r-done", "p-arch")
    svc.runs["r-done"]["state_machine"].state = AgentState.DONE
    await svc.resume_agent("r-done")  # terminal: silent no-op, not a 409


async def test_sweep_stops_only_runs_in_archived_or_deleted_workspaces(env):
    svc, f = env
    for rid, pid in (("s-arch", "p-arch"), ("s-del", "p-del"), ("s-paused", "p-paused"),
                     ("s-active", "p-active")):
        await _approve(svc, rid, pid)
        await _claim_running(f, rid, pid)
    assert sorted(await sweep_inactive_workspace_runs(svc)) == ["s-arch", "s-del"]
    assert svc.runs["s-arch"]["state_machine"].state is AgentState.DONE
    assert svc.runs["s-paused"]["state_machine"].state is not AgentState.DONE
    assert svc.runs["s-active"]["state_machine"].state is not AgentState.DONE
    assert await sweep_inactive_workspace_runs(svc) == []  # claims released: second sweep is empty
    stops = await _actions(f, "workspace.run_stopped")
    assert sorted(t for t, _ in stops) == ["ws-arch", "ws-del"]
    assert {d["runId"] for _, d in stops} == {"s-arch", "s-del"}


async def test_purge_scrubs_expired_deleted_workspaces_and_is_idempotent(env):
    _, f = env
    now = datetime(2026, 11, 1, tzinfo=UTC)
    past, future = (now - timedelta(days=1)).isoformat(), (now + timedelta(days=1)).isoformat()
    async with f() as db:
        await db.execute(text("UPDATE workspaces SET purge_after=:p, state_reason='why' "
                              "WHERE id='ws-del'"), {"p": past})
        await db.execute(text("INSERT INTO workspaces (id, state, purge_after) VALUES "
                              "('ws-later','deleted',:p), ('ws-arch2','archived',:q)"),
                         {"p": future, "q": past})
        for w in ("ws-del", "ws-later"):
            await db.execute(text("INSERT INTO workspace_members VALUES (:w,'u1')"), {"w": w})
            await db.execute(text("INSERT INTO workspace_bindings VALUES (:w,'repo')"), {"w": w})
            await db.execute(
                text("INSERT INTO workspace_settings_versions VALUES (:w,1)"), {"w": w})
        await db.commit()
    assert await purge_expired_workspaces(f, now=now.isoformat()) == ["ws-del"]
    async with f() as db:
        ws = (await db.execute(text("SELECT * FROM workspaces WHERE id='ws-del'"))).one()
        assert (ws.name, ws.name_key, ws.tags, ws.settings, ws.state_reason) == (
            "[purged]", "ws-del", "[]", "{}", None) and ws.state == "deleted"
        left = (await db.execute(
            text("SELECT workspace_id FROM workspace_members"))).scalars().all()
        assert left == ["ws-later"]  # only the not-yet-expired workspace keeps its members
        later = (await db.execute(
            text("SELECT name FROM workspaces WHERE id='ws-later'"))).scalar()
        assert later == "ws"  # not expired: untouched
        arch = (await db.execute(text("SELECT name FROM workspaces WHERE id='ws-arch2'"))).scalar()
        assert arch == "ws"  # not deleted: never purged
    assert await workspace_block(f, "p-del") == ("ws-del", "deleted")  # still blocks its project
    assert await purge_expired_workspaces(f, now=now.isoformat()) == []
    assert [t for t, _ in await _actions(f, "workspace.purged")] == ["ws-del"]
