"""Shared fixture for the part 43 tests (lane D): the REAL migrations 0041 (sessions), 0043
(control) and 0045 (discussion) on sqlite, plus the 0035 collaboration tables, the discussion,
control and orchestrator routers, a fake clock and a fake lane-N ``notify`` module."""

import asyncio
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import harness_orchestrator
from harness_orchestrator import api as orch_api
from harness_orchestrator import collab, control, discussion
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.service import OrchestratorService

MIGRATIONS = Path(__file__).resolve().parents[3] / "section1" / "migrations"
FILES = ("0041_sessions.sql", "0043_control.sql", "0045_discussion.sql")

DDL = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " name VARCHAR(200) NOT NULL DEFAULT 'x', workspace_id VARCHAR(36))",
    "CREATE TABLE workspaces (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)",
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
    " state VARCHAR(40))",
    "CREATE TABLE agent_plans (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL)",
    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36), display_name VARCHAR(200) NOT NULL DEFAULT 'x',"
    " member_type VARCHAR(16) NOT NULL DEFAULT 'human',"
    " role VARCHAR(16) NOT NULL DEFAULT 'engineer')",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " actor_member_id VARCHAR(36), actor_kind VARCHAR(16) NOT NULL,"
    " action VARCHAR(80) NOT NULL, target_type VARCHAR(60) NOT NULL,"
    " target_id VARCHAR(80) NOT NULL, detail TEXT NOT NULL DEFAULT '{}',"
    " ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE run_participants (run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36) NOT NULL, role VARCHAR(16) NOT NULL DEFAULT 'participant',"
    " joined_at TIMESTAMP, PRIMARY KEY (run_id, user_id))",
    "CREATE UNIQUE INDEX one_leader ON run_participants (run_id) WHERE role = 'leader'",
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " actor_user_id VARCHAR(36) NOT NULL, kind VARCHAR(40) NOT NULL, request_id VARCHAR(64),"
    " payload TEXT NOT NULL DEFAULT '{}', ts TIMESTAMP, schema_version INTEGER DEFAULT 1)",
    "CREATE UNIQUE INDEX collab_req ON run_collab_events (run_id, request_id)"
    " WHERE request_id IS NOT NULL",
    "CREATE TABLE run_handoffs (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " team_id VARCHAR(36) NOT NULL, from_user_id VARCHAR(36) NOT NULL,"
    " to_user_id VARCHAR(36) NOT NULL, state VARCHAR(16) NOT NULL DEFAULT 'pending',"
    " created_at TIMESTAMP, decided_at TIMESTAMP)",
    "CREATE UNIQUE INDEX one_pending ON run_handoffs (run_id) WHERE state = 'pending'",
    "CREATE TABLE high_impact_approvals (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " team_id VARCHAR(36) NOT NULL, requester_user_id VARCHAR(36) NOT NULL,"
    " action VARCHAR(16) NOT NULL, paths TEXT NOT NULL, summary TEXT NOT NULL,"
    " diff TEXT NOT NULL DEFAULT '', digest VARCHAR(64) NOT NULL,"
    " state VARCHAR(16) NOT NULL DEFAULT 'pending', decided_by VARCHAR(36),"
    " decided_at TIMESTAMP, expires_at TIMESTAMP NOT NULL, outcome TEXT, created_at TIMESTAMP)",
)

USERS = {  # user -> (team, role)
    "lead": ("team-a", Role.ENGINEER),
    "eng2": ("team-a", Role.ENGINEER),
    "eng3": ("team-a", Role.ENGINEER),
    "view": ("team-a", Role.VIEWER),
    "guest": ("team-a", Role.GUEST),
    "boss": ("team-a", Role.ADMIN),
    "evil": ("team-b", Role.ADMIN),
    "evil2": ("team-b", Role.ENGINEER),
}
P = "/api/v1/orchestrator"
T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
# sessions: s1 (lead owner, eng2 participant, view observer), spriv (only lead), sb (team-b)
SESSIONS = {"s1": "team-a", "spriv": "team-a", "sb": "team-b"}


def migration_statements() -> list[str]:
    out = []
    for name in FILES:
        body = "\n".join(
            ln for ln in (MIGRATIONS / name).read_text().splitlines()
            if not ln.lstrip().startswith("--")
        )
        out += [s.strip() for s in body.split(";") if s.strip()]
    return out


def base(scope: str, sid: str) -> str:
    return f"{P}/{'runs' if scope == 'run' else 'sessions'}/{sid}/comments"


class Env:
    def __init__(self, client, who, factory, clock, notified):
        self.client, self.who, self.factory, self.clock, self.notified = (
            client, who, factory, clock, notified)

    def as_(self, user):
        self.who["u"] = user
        return self.client

    def tick(self, **kw):
        self.clock["t"] = self.clock["t"] + timedelta(**kw)

    def q(self, sql, **kw):
        async def go():
            async with self.factory() as db:
                return (await db.execute(text(sql), kw)).all()

        return asyncio.run(go())

    def x(self, sql, **kw):
        async def go():
            async with self.factory() as db:
                await db.execute(text(sql), kw)
                await db.commit()

        asyncio.run(go())

    def run(self, coro_fn):
        async def go():
            async with self.factory() as db:
                return await coro_fn(db)

        return asyncio.run(go())

    def principal(self, user):
        team, role = USERS[user]
        return Principal(userId=user, teamId=team, role=role)

    def audit(self, action=None):
        rows = self.q("SELECT action, target_type, target_id, detail FROM audit_log ORDER BY seq")
        return rows if action is None else [r for r in rows if r.action == action]

    def kinds(self, run="r1"):
        sql = "SELECT kind FROM run_collab_events WHERE run_id=:r ORDER BY seq"
        return [r.kind for r in self.q(sql, r=run)]

    def post(self, user, scope, sid, body, **extra):
        r = self.as_(user).post(base(scope, sid), json={"body": body, **extra})
        assert r.status_code in (200, 201), r.text
        return r.json()

    def listing(self, user, scope, sid, **params):
        r = self.as_(user).get(base(scope, sid), params=params)
        assert r.status_code == 200, r.text
        return r.json()

    def seat(self, run, user, role="participant"):
        self.x("INSERT INTO run_participants (run_id, team_id, user_id, role, joined_at)"
               " VALUES (:r, :t, :u, :ro, :ts)",
               r=run, t=USERS[user][0], u=user, ro=role, ts=self.clock["t"])

    def approval(self, aid="ap1", run="r1", team="team-a", diff="@@ -1 +1 @@"):
        self.x("INSERT INTO high_impact_approvals (id, run_id, team_id, requester_user_id,"
               " action, paths, summary, diff, digest, state, expires_at, created_at)"
               " VALUES (:i, :r, :t, 'lead', 'delete', '[]', 's', :d, 'x', 'pending', :e, :c)",
               i=aid, r=run, t=team, d=diff, e=self.clock["t"] + timedelta(days=1),
               c=self.clock["t"])


def err(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["detail"]["error"]["code"] == code, resp.text
    return resp.json()["detail"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    monkeypatch.delenv("HARNESS_ALLOW_SELF_APPROVAL", raising=False)
    clock = {"t": T0}
    monkeypatch.setattr(collab, "db_now", lambda: clock["t"])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d.db")

    async def setup():
        async with engine.begin() as conn:
            for d in (*DDL, *migration_statements()):
                await conn.execute(text(d))
            await conn.execute(text(
                "INSERT INTO projects (id, team_id) VALUES ('p', 'team-a'), ('q', 'team-b')"))
            await conn.execute(text(
                "INSERT INTO agent_runs (id, project_id) VALUES"
                " ('r1', 'p'), ('r2', 'p'), ('r3', 'q')"))
            for u, (t, role) in USERS.items():
                await conn.execute(
                    text("INSERT INTO team_members (id, team_id, user_id, display_name, role)"
                         " VALUES (:i, :t, :u, :n, :r)"),
                    {"i": f"m-{u}", "t": t, "u": u, "n": f"Name {u}", "r": role.value})
            for sid, team in SESSIONS.items():
                await conn.execute(
                    text("INSERT INTO collab_sessions (id, team_id, title, owner_user_id, state,"
                         " created_at, updated_at) VALUES (:i, :t, :ti, :o, 'active', :c, :c)"),
                    {"i": sid, "t": team, "ti": f"SECRET TITLE {sid}",
                     "o": "evil" if team == "team-b" else "lead", "c": T0})
            members = [("s1", "lead", "owner"), ("s1", "eng2", "participant"),
                       ("s1", "view", "observer"), ("spriv", "lead", "owner"),
                       ("sb", "evil", "owner")]
            for sid, u, role in members:
                await conn.execute(
                    text("INSERT INTO session_members (id, session_id, kind, user_id, role,"
                         " display_name, status, joined_at) VALUES"
                         " (:i, :s, 'user', :u, :r, :n, 'active', :c)"),
                    {"i": f"sm-{sid}-{u}", "s": sid, "u": u, "r": role, "n": u, "c": T0})
            await conn.execute(text(
                "INSERT INTO session_runs (run_id, session_id, linked_by, linked_at)"
                " VALUES ('r1', 's1', 'lead', :c)"), {"c": T0})

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class Bus:
        async def publish(self, *a, **k):
            return None

        async def ensure_run(self, *a, **k):
            return None

    service = OrchestratorService(session_factory=factory, bus=Bus())
    monkeypatch.setattr(orch_api, "_service", service)
    discussion.configure_discussion(service)
    monkeypatch.setattr(control, "_service", service)

    notified: list[dict] = []

    async def emit(db, **kw):
        notified.append(kw)

    fake = types.ModuleType("harness_orchestrator.notify")
    fake.emit = emit
    monkeypatch.setitem(sys.modules, "harness_orchestrator.notify", fake)
    monkeypatch.setattr(harness_orchestrator, "notify", fake, raising=False)

    app = FastAPI()
    app.include_router(orch_router)
    app.include_router(control.router)
    app.include_router(discussion.router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1])
    yield Env(TestClient(app), who, factory, clock, notified)
    asyncio.run(engine.dispose())
