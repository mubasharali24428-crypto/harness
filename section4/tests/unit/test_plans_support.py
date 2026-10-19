"""Shared fixture for the part 44 plan tests (lane PL): the REAL migration 0047 on sqlite, the
legacy ``agent_plans`` table, the plans router, a fake clock and a principal override."""

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
from harness_orchestrator import collab, plans
from harness_orchestrator.api import get_principal
from harness_orchestrator.service import OrchestratorService

MIGRATIONS = Path(__file__).resolve().parents[3] / "section1" / "migrations"
FILES = ("0047_plans.sql",)

DDL = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " name VARCHAR(200) NOT NULL DEFAULT 'x', workspace_id VARCHAR(36))",
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
    " state VARCHAR(40))",
    # the legacy table as migrations 0001, 0013 and 0023 leave it
    "CREATE TABLE agent_plans (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " goal TEXT NOT NULL, files_likely_to_change TEXT NOT NULL, steps TEXT NOT NULL,"
    " tests TEXT NOT NULL, risks TEXT NOT NULL, estimated_cost_usd NUMERIC NOT NULL,"
    " forbidden_paths TEXT NOT NULL, expected_result TEXT NOT NULL, status VARCHAR(20) NOT NULL,"
    " version INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP)",
    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36), display_name VARCHAR(200) NOT NULL DEFAULT 'x',"
    " member_type VARCHAR(16) NOT NULL DEFAULT 'human',"
    " role VARCHAR(16) NOT NULL DEFAULT 'engineer')",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " actor_member_id VARCHAR(36), actor_kind VARCHAR(16) NOT NULL,"
    " action VARCHAR(80) NOT NULL, target_type VARCHAR(60) NOT NULL,"
    " target_id VARCHAR(80) NOT NULL, detail TEXT NOT NULL DEFAULT '{}',"
    " ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " actor_user_id VARCHAR(36) NOT NULL, kind VARCHAR(40) NOT NULL, request_id VARCHAR(64),"
    " payload TEXT NOT NULL DEFAULT '{}', ts TIMESTAMP, schema_version INTEGER DEFAULT 1)",
)

USERS = {  # user -> (team, role)
    "lead": ("team-a", Role.ENGINEER),
    "eng2": ("team-a", Role.ENGINEER),
    "eng3": ("team-a", Role.ENGINEER),
    "view": ("team-a", Role.VIEWER),
    "guest": ("team-a", Role.GUEST),
    "boss": ("team-a", Role.ADMIN),
    "evil": ("team-b", Role.ADMIN),
}
P = "/api/v1/orchestrator"
T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
GOAL = "Add a retry to the uploader"
CONTENT = {
    "goal": GOAL,
    "steps": ["Read uploader.py", "Add retry loop", "Run the tests"],
    "tests": ["pytest tests/test_uploader.py"],
    "risks": ["Retry storms"],
    "filesLikelyToChange": ["src/uploader.py"],
    "forbiddenPaths": ["secrets/"],
    "expectedResult": "Uploads survive a flaky network",
    "estimatedCostUsd": 0.25,
}


def migration_statements() -> list[str]:
    out = []
    for name in FILES:
        body = "\n".join(
            ln
            for ln in (MIGRATIONS / name).read_text().splitlines()
            if not ln.lstrip().startswith("--")
        )
        out += [s.strip() for s in body.split(";") if s.strip()]
    return out


def runp(run: str, suffix: str = "") -> str:
    return f"{P}/runs/{run}{suffix}"


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

    def x(self, sql, **kw):
        async def go():
            async with self.factory() as db:
                await db.execute(text(sql), kw)
                await db.commit()

        asyncio.run(go())

    def principal(self, user):
        team, role = USERS[user]
        return Principal(userId=user, teamId=team, role=role)

    def audit(self, action=None):
        rows = self.q("SELECT action, target_type, target_id, detail FROM audit_log ORDER BY seq")
        return rows if action is None else [r for r in rows if r.action == action]

    def kinds(self, run="r1"):
        sql = "SELECT kind FROM run_collab_events WHERE run_id=:r ORDER BY seq"
        return [r.kind for r in self.q(sql, r=run)]

    def legacy(self, plan_id="lp1", run="r1", status="proposed", **over):
        row = {
            "goal": "Legacy goal",
            "steps": '["a", "b"]',
            "tests": '["t"]',
            "risks": "[]",
            "files": '["x.py"]',
            "forbidden": "[]",
            "expected": "done",
            "cost": 0.5,
            **over,
        }
        self.x(
            "INSERT INTO agent_plans (id, run_id, goal, files_likely_to_change, steps, tests,"
            " risks, estimated_cost_usd, forbidden_paths, expected_result, status, created_at)"
            " VALUES (:i, :r, :g, :f, :s, :t, :ri, :c, :fo, :e, :st, :ts)",
            i=plan_id,
            r=run,
            g=row["goal"],
            f=row["files"],
            s=row["steps"],
            t=row["tests"],
            ri=row["risks"],
            c=row["cost"],
            fo=row["forbidden"],
            e=row["expected"],
            st=status,
            ts=self.clock["t"],
        )

    def set_legacy(self, status, plan_id="lp1"):
        self.x("UPDATE agent_plans SET status=:s WHERE id=:i", s=status, i=plan_id)

    def versions(self, user="lead", run="r1"):
        r = self.as_(user).get(runp(run, "/plan/versions"))
        assert r.status_code == 200, r.text
        return r.json()

    def new_version(self, user="lead", run="r1", expected=None, content=None, **extra):
        if expected is None:
            expected = self.versions(user, run)["latestVersion"]
        r = self.as_(user).post(
            runp(run, "/plan/versions"),
            json={"content": content or CONTENT, "expectedVersion": expected, **extra},
        )
        assert r.status_code in (200, 201), r.text
        return r.json()

    def propose(self, user, n, patch, run="r1", **extra):
        r = self.as_(user).post(
            runp(run, f"/plan/versions/{n}/edits"),
            json={"patch": patch, "requestId": None, **extra},
        )
        assert r.status_code in (200, 201), r.text
        return r.json()

    def decide(self, user, n, edit_id, decision="accept", run="r1", **extra):
        return self.as_(user).post(
            runp(run, f"/plan/versions/{n}/edits/{edit_id}/decide"),
            json={"decision": decision, **extra},
        )


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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/p.db")

    async def setup():
        async with engine.begin() as conn:
            for d in (*DDL, *migration_statements()):
                await conn.execute(text(d))
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('p', 'team-a'), ('q', 'team-b')")
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id) VALUES"
                    " ('r1', 'p'), ('r2', 'p'), ('r3', 'q')"
                )
            )
            for u, (t, role) in USERS.items():
                await conn.execute(
                    text(
                        "INSERT INTO team_members (id, team_id, user_id, display_name, role)"
                        " VALUES (:i, :t, :u, :n, :r)"
                    ),
                    {"i": f"m-{u}", "t": t, "u": u, "n": f"Name {u}", "r": role.value},
                )
            await conn.execute(
                text(
                    "INSERT INTO team_members (id, team_id, user_id, display_name, member_type)"
                    " VALUES ('m-bot', 'team-a', 'bot', 'A Bot', 'agent')"
                )
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
    plans.configure_plans(service)

    app = FastAPI()
    app.include_router(plans.router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1]
    )
    yield Env(TestClient(app), who, factory, clock)
    asyncio.run(engine.dispose())
