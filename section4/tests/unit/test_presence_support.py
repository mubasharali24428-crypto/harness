"""Shared fixture for the part 41 tests (lane P): the real migration 0042 on sqlite plus the
0035 collaboration tables, the presence and participation routers, and a fake clock."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import participation, presence
from harness_orchestrator.api import get_principal
from harness_orchestrator.service import OrchestratorService

MIGRATION = Path(__file__).resolve().parents[3] / "section1" / "migrations" / "0042_presence.sql"

DDL = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " name VARCHAR(200) NOT NULL DEFAULT 'x')",
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
    " state VARCHAR(40))",
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
    "CREATE TABLE run_handoffs (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " team_id VARCHAR(36) NOT NULL, from_user_id VARCHAR(36) NOT NULL,"
    " to_user_id VARCHAR(36) NOT NULL, state VARCHAR(16) NOT NULL DEFAULT 'pending',"
    " created_at TIMESTAMP, decided_at TIMESTAMP)",
    "CREATE UNIQUE INDEX one_pending ON run_handoffs (run_id) WHERE state = 'pending'",
)

USERS = {  # user -> (team, role)
    "lead": ("team-a", Role.ENGINEER),
    "eng2": ("team-a", Role.ENGINEER),
    "eng3": ("team-a", Role.ENGINEER),
    "eng4": ("team-a", Role.ENGINEER),
    "view": ("team-a", Role.VIEWER),
    "boss": ("team-a", Role.ADMIN),
    "evil": ("team-b", Role.ADMIN),
}

P = "/api/v1/orchestrator"
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def migration_statements() -> list[str]:
    """The real 0042 file, comment lines dropped, split on semicolons."""
    body = "\n".join(
        ln for ln in MIGRATION.read_text().splitlines() if not ln.lstrip().startswith("--")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


class Env:
    def __init__(self, client, who, factory, clock):
        self.client, self.who, self.factory, self.clock = client, who, factory, clock

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

    def run(self, coro_fn):
        async def go():
            async with self.factory() as db:
                return await coro_fn(db)

        return asyncio.run(go())

    def audit(self, action=None):
        rows = self.q("SELECT action, detail FROM audit_log ORDER BY seq")
        if action is None:
            return [r.action for r in rows]
        return [r for r in rows if r.action == action]

    def kinds(self, run="r1"):
        sql = "SELECT kind FROM run_collab_events WHERE run_id=:r ORDER BY seq"
        return [r.kind for r in self.q(sql, r=run)]

    def roster(self, run="r1"):
        return {
            r.user_id: r.role
            for r in self.q("SELECT user_id, role FROM run_participants WHERE run_id=:r", r=run)
        }

    def seat(self, run, user, role="participant"):
        """Put a user on the roster directly (collab.join is lane C's, not under test)."""

        async def go(db):
            await db.execute(
                text("INSERT INTO run_participants (run_id, team_id, user_id, role, joined_at)"
                     " VALUES (:r, :t, :u, :ro, :ts)"),
                {"r": run, "t": USERS[user][0], "u": user, "ro": role, "ts": self.clock["t"]},
            )
            await db.commit()

        self.run(go)

    def beat(self, user, device="d1", state="online", run="r1"):
        return self.as_(user).post(
            f"{P}/runs/{run}/presence/heartbeat", json={"deviceId": device, "state": state}
        )

    def presence(self, viewer, run="r1"):
        r = self.as_(viewer).get(f"{P}/runs/{run}/presence")
        assert r.status_code == 200, r.text
        return {p["userId"]: p for p in r.json()["participants"]}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/p.db")

    async def setup():
        async with engine.begin() as conn:
            for d in (*DDL, *migration_statements()):
                await conn.execute(text(d))
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('p', 'team-a'), ('q', 'team-b')")
            )
            await conn.execute(
                text("INSERT INTO agent_runs (id, project_id) VALUES"
                     " ('r1', 'p'), ('r2', 'p'), ('r3', 'q')")
            )
            for u, (t, role) in USERS.items():
                await conn.execute(
                    text("INSERT INTO team_members (id, team_id, user_id, display_name, role)"
                         " VALUES (:i, :t, :u, :n, :r)"),
                    {"i": f"m-{u}", "t": t, "u": u, "n": f"Name {u}", "r": role.value},
                )

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class Bus:
        async def publish(self, *a, **k):
            return None

        async def ensure_run(self, *a, **k):
            return None

    service = OrchestratorService(session_factory=factory, bus=Bus())
    monkeypatch.setattr(orch_api, "_service", service)
    presence.configure_presence(service)
    participation.configure_participation(service)
    clock = {"t": T0}
    monkeypatch.setattr(presence, "now", lambda: clock["t"])
    app = FastAPI()
    app.include_router(presence.router)
    app.include_router(participation.router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1]
    )
    yield Env(TestClient(app), who, factory, clock)
    asyncio.run(engine.dispose())
