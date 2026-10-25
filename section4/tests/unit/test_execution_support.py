"""Fixture for part 44.004-010 tests: real migrations 0041 and 0048 on sqlite,
older tables required by the orchestrator, a real service with a stub engine,
a persisting fake bus, the execution router, and a fake clock.

Requests go through an in-process ASGI client on the test's event loop, so races are real
``asyncio.gather`` races between coroutines that share the database."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import collab, execution
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.service import OrchestratorService

MIGRATIONS = Path(__file__).resolve().parents[3] / "section1" / "migrations"
FILES = ("0041_sessions.sql", "0048_execution.sql")

DDL = """
CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL DEFAULT 'x',
    budget_usd REAL NULL);
CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,
    workspace_id VARCHAR(36));
CREATE TABLE workspaces (id VARCHAR(36) PRIMARY KEY, state VARCHAR(16) NOT NULL DEFAULT 'active');
CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,
    state VARCHAR(40) NOT NULL DEFAULT 'idle');
CREATE TABLE agent_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id VARCHAR(36),
    run_id VARCHAR(36), type VARCHAR(60) NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
    ts TIMESTAMP);
CREATE TABLE agent_plans (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,
    goal TEXT NOT NULL,
    files_likely_to_change TEXT NOT NULL DEFAULT '[]', steps TEXT NOT NULL DEFAULT '[]',
    tests TEXT NOT NULL DEFAULT '[]', risks TEXT NOT NULL DEFAULT '[]',
    estimated_cost_usd REAL NOT NULL DEFAULT 0, forbidden_paths TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT NOT NULL DEFAULT '', status VARCHAR(20) NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
-- 0019's table, without its PG-only FKs: start_execution reads it for the
-- planner's advisory rules block, so its absence aborts that path.
CREATE TABLE project_rules (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,
    rules_text TEXT NOT NULL DEFAULT '');
CREATE TABLE run_claims (run_id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL, claimed_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL);
CREATE TABLE cost_ledger (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),
    agent_role VARCHAR(20) NOT NULL DEFAULT 'primary', model VARCHAR(120) NOT NULL DEFAULT 'stub',
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12,6) NOT NULL, latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36), display_name VARCHAR(200) NOT NULL DEFAULT 'x',
    member_type VARCHAR(16) NOT NULL DEFAULT 'human', role VARCHAR(16) NOT NULL DEFAULT 'engineer');
CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, actor_member_id VARCHAR(36),
    actor_kind VARCHAR(16) NOT NULL, action VARCHAR(80) NOT NULL, target_type VARCHAR(60) NOT NULL,
    target_id VARCHAR(80) NOT NULL, detail TEXT NOT NULL DEFAULT '{}', ts TIMESTAMP);
CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL, actor_user_id VARCHAR(36) NOT NULL, kind VARCHAR(40) NOT NULL,
    request_id VARCHAR(64), payload TEXT NOT NULL DEFAULT '{}', ts TIMESTAMP,
    schema_version INTEGER DEFAULT 1);
CREATE TABLE high_impact_approvals (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL, requester_user_id VARCHAR(36) NOT NULL,
    action VARCHAR(16) NOT NULL,
    paths TEXT NOT NULL, summary TEXT NOT NULL, diff TEXT NOT NULL DEFAULT '',
    digest VARCHAR(64) NOT NULL, state VARCHAR(16) NOT NULL DEFAULT 'pending',
    decided_by VARCHAR(36),
    decided_at TIMESTAMP, expires_at TIMESTAMP NOT NULL, outcome TEXT, created_at TIMESTAMP);
"""

USERS = {  # user -> (team, role)
    "lead": ("team-a", Role.ENGINEER),
    "eng2": ("team-a", Role.ENGINEER),
    "view": ("team-a", Role.VIEWER),
    "guest": ("team-a", Role.GUEST),
    "boss": ("team-a", Role.ADMIN),
    "evil": ("team-b", Role.ADMIN),
}
P = "/api/v1/orchestrator"
T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
RESERVED = {
    "subtask.created",
    "subtask.updated",
    "subtask.blocked",
    "subtask.unblocked",
    "subtask.completed",
    "subtask.evidence_added",
    "execution.limits_updated",
    "execution.limit_exceeded",
    "execution.checkpoint_resumed",
    "completion.reported",
    "completion.accepted",
    "completion.rejected",
    "run.abandoned",
    "run.retry_requested",
    "run.retry_refused",
}


def statements(sql: str) -> list[str]:
    body = "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def migration_statements() -> list[str]:
    out: list[str] = []
    for name in FILES:
        out += statements((MIGRATIONS / name).read_text())
    return out


def err(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["detail"]["error"]["code"] == code, resp.text
    return resp.json()["detail"]


class Engine:
    """Stub engine: completes unless the session id is in ``fail``."""

    def __init__(self):
        self.calls: list[str] = []
        self.fail: set[str] = set()

    def execute_goal(self, goal=None, session_id=None):
        self.calls.append(session_id)
        if session_id in self.fail:
            return {
                "sessionId": session_id,
                "engine": "stub",
                "status": "failed",
                "finalResponse": "boom " + "x" * 400,
            }
        return {
            "sessionId": session_id,
            "engine": "stub",
            "status": "completed",
            "finalResponse": "done",
        }


class Bus:
    """Persists every published event to agent_events, like the real bus (durable lifecycle log)."""

    def __init__(self, factory, clock):
        self.factory, self.clock = factory, clock

    async def publish(self, event_type, project_id, payload, run_id):
        async with self.factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_events (project_id, run_id, type, payload, ts)"
                    " VALUES (:p, :r, :t, :pl, :ts)"
                ),
                {
                    "p": project_id,
                    "r": run_id,
                    "t": getattr(event_type, "value", event_type),
                    "pl": json.dumps(payload, default=str),
                    "ts": self.clock["t"],
                },
            )
            await db.commit()

    async def ensure_run(self, *a, **k):
        return None


class Env:
    def __init__(self, client, who, factory, clock, svc, engine):
        self.client, self.who, self.factory, self.clock, self.svc, self.engine = (
            client,
            who,
            factory,
            clock,
            svc,
            engine,
        )

    # ---- http
    async def call(self, user, method, path, **kw):
        self.who["u"] = user
        return await self.client.request(method, P + path, **kw)

    async def get(self, user, path, **kw):
        return await self.call(user, "GET", path, **kw)

    async def post(self, user, path, body=None, **kw):
        return await self.call(user, "POST", path, json=body or {}, **kw)

    def tick(self, **kw):
        self.clock["t"] = self.clock["t"] + timedelta(**kw)

    # ---- sql
    async def q(self, sql, **kw):
        async with self.factory() as db:
            return (await db.execute(text(sql), kw)).all()

    async def x(self, sql, **kw):
        async with self.factory() as db:
            await db.execute(text(sql), kw)
            await db.commit()

    async def audit(self, action=None):
        rows = await self.q("SELECT action, target_id, detail FROM audit_log ORDER BY seq")
        return rows if action is None else [r for r in rows if r.action == action]

    async def events(self, rid, kind=None):
        rows = await self.q(
            "SELECT seq, type, payload, ts FROM agent_events WHERE run_id = :r ORDER BY seq", r=rid
        )
        return rows if kind is None else [r for r in rows if r.type == kind]

    # ---- fixtures
    async def new_run(self, rid, team="team-a", *, approved=True):
        pid = f"proj-{rid}"
        await self.x("INSERT INTO projects (id, team_id) VALUES (:p, :t)", p=pid, t=team)
        await self.x("INSERT INTO agent_runs (id, project_id) VALUES (:r, :p)", r=rid, p=pid)
        if approved:
            await self.svc.propose_plan(rid, goal=f"goal {rid}", project_id=pid)
            await self.svc.approve_plan(rid, self.svc.runs[rid]["plan_id"], "u-test")
        return rid

    async def flush(self):
        await asyncio.gather(*list(self.svc._bg_tasks), return_exceptions=True)

    async def start(self, rid):
        await self.svc.start_execution(rid)
        await self.flush()

    async def work(self, rid, *, fail=False):
        """Claim and run the queued run once; waits for the engine task and the event publishes."""
        if fail:
            self.engine.fail.add(rid)
        assert await self.svc.poll_once() == rid
        await self.svc.runs[rid]["task"]
        await self.flush()
        self.engine.fail.discard(rid)

    async def blocked_run(self, rid="r1", team="team-a"):
        """A run that really ran and failed: BLOCKED, claim released, events in the durable log."""
        await self.new_run(rid, team)
        await self.start(rid)
        self.tick(seconds=5)
        await self.work(rid, fail=True)
        assert self.svc.runs[rid]["state_machine"].state.value == "blocked"
        return rid

    def seat(self, run):  # state of the in-memory machine
        return self.svc.runs[run]["state_machine"].state.value

    async def ledger(self, rid, cost, n=1):
        for _ in range(n):
            await self.x(
                "INSERT INTO cost_ledger (run_id, cost_usd) VALUES (:r, :c)", r=rid, c=cost
            )

    async def event(self, rid, typ, at, payload=None):
        await self.x(
            "INSERT INTO agent_events (project_id, run_id, type, payload, ts)"
            " VALUES (:p, :r, :t, :pl, :ts)",
            p=f"proj-{rid}",
            r=rid,
            t=typ,
            pl=json.dumps(payload or {}),
            ts=at,
        )

    async def approval(self, rid, state, aid="ap1", team="team-a"):
        await self.x(
            "INSERT INTO high_impact_approvals (id, run_id, team_id, requester_user_id, action,"
            " paths, summary, digest, state, expires_at, created_at) VALUES (:i, :r, :t, 'lead',"
            " 'delete', '[]', 's', 'x', :s, :e, :c)",
            i=aid,
            r=rid,
            t=team,
            s=state,
            e=self.clock["t"] + timedelta(days=1),
            c=self.clock["t"],
        )

    async def sub(self, rid, title="t", user="lead", **extra):
        r = await self.post(user, f"/runs/{rid}/subtasks", {"title": title, **extra})
        assert r.status_code in (200, 201), r.text
        return r.json()["subtask"]

    async def done_sub(self, rid, title="t"):
        s = await self.sub(rid, title)
        sid = f"/runs/{rid}/subtasks/{s['id']}"
        assert (
            await self.post("lead", sid + "/evidence", {"kind": "test", "ref": "tests::ok"})
        ).status_code == 200
        r = await self.post("lead", sid + "/complete", {"expectedVersion": s["version"]})
        assert r.status_code == 200, r.text
        return r.json()["subtask"]

    async def report(self, rid, user="lead", summary="all done", **extra):
        r = await self.post(user, f"/runs/{rid}/completion/report", {"summary": summary, **extra})
        assert r.status_code in (200, 201), r.text
        return r.json()


@pytest.fixture()
async def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    clock = {"t": T0}
    monkeypatch.setattr(collab, "db_now", lambda: clock["t"])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/x.db")
    async with engine.begin() as conn:
        for d in (*statements(DDL), *migration_statements()):
            await conn.execute(text(d))
        for t in ("team-a", "team-b"):
            await conn.execute(text("INSERT INTO teams (id) VALUES (:t)"), {"t": t})
        for u, (t, role) in USERS.items():
            await conn.execute(
                text(
                    "INSERT INTO team_members (id, team_id, user_id, display_name, role)"
                    " VALUES (:i, :t, :u, :u, :r)"
                ),
                {"i": f"m-{u}", "t": t, "u": u, "r": role.value},
            )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    stub = Engine()
    svc = OrchestratorService(engine=stub, session_factory=factory, bus=Bus(factory, clock))
    monkeypatch.setattr(orch_api, "_service", svc)
    monkeypatch.setattr(execution, "_service", svc)
    app = FastAPI()
    app.include_router(orch_router)
    app.include_router(execution.router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        yield Env(client, who, factory, clock, svc, stub)
    # spawned engine tasks still hold sqlite connections: await them before dispose
    await asyncio.gather(
        *[r["task"] for r in svc.runs.values() if r.get("task") is not None], return_exceptions=True
    )
    await asyncio.gather(*list(svc._bg_tasks), return_exceptions=True)
    svc.shutdown()
    await engine.dispose()
