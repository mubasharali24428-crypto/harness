"""Shared fixture for the part 50 tests (lane SE): the REAL migrations 0041 (sessions), 0043
(control), 0045 (discussion) and 0050 (search, export, deletion) on sqlite, the discussion,
search and export routers, a fake clock, a temporary export store.
Model: test_discussion_support."""

import asyncio
import sys
import types
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import harness_orchestrator
from harness_orchestrator import api as orch_api
from harness_orchestrator import collab, control, discussion, exports, search
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.service import OrchestratorService

from . import test_discussion_support as base
from .test_discussion_support import SESSIONS, T0, USERS, P, err  # noqa: F401 - re-exported

MIGRATIONS = Path(__file__).resolve().parents[3] / "section1" / "migrations"
FILES = ("0041_sessions.sql", "0043_control.sql", "0045_discussion.sql", "0050_search_export.sql")

_REPLACED = ("CREATE TABLE agent_runs", "CREATE TABLE agent_plans")
DDL = tuple(d for d in base.DDL if not d.startswith(_REPLACED)) + (
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
    " state VARCHAR(40), branch VARCHAR(300), created_at TIMESTAMP, updated_at TIMESTAMP)",
    "CREATE TABLE agent_plans (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " goal TEXT, created_at TIMESTAMP)",
    "CREATE TABLE agent_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id VARCHAR(36),"
    " run_id VARCHAR(36), type VARCHAR(60) NOT NULL, payload TEXT NOT NULL DEFAULT '{}',"
    " ts TIMESTAMP)",
    "CREATE TABLE artifacts (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " run_id VARCHAR(36) NOT NULL, name VARCHAR(300), media_type VARCHAR(100),"
    " kind VARCHAR(12), state VARCHAR(16), created_by VARCHAR(36), created_at TIMESTAMP)",
)


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


class Env(base.Env):
    def sync(self, team=None, **kw):
        return asyncio.run(search.sync_index(self.factory, team, **kw))

    def find(self, user, expect=200, **params):
        r = self.as_(user).get(f"{P}/search", params=params)
        assert r.status_code == expect, r.text
        return r.json()

    def hits(self, user, **params):
        return [(h["kind"], h["sourceId"]) for h in self.find(user, **params)["results"]]

    def comment_id(self, scope, sid, text_, user="lead"):
        return self.post(user, scope, sid, text_)["id"]

    def event(self, kind, payload=None, run="r1", team="team-a", actor="lead"):
        import json

        self.x(
            "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind, payload, ts,"
            " schema_version) VALUES (:r, :t, :a, :k, :p, :ts, 1)",
            r=run,
            t=team,
            a=actor,
            k=kind,
            p=json.dumps(payload or {}),
            ts=self.clock["t"],
        )

    def agent_event(self, type_, payload=None, run="r1"):
        import json

        self.x(
            "INSERT INTO agent_events (project_id, run_id, type, payload, ts) VALUES"
            " ('p', :r, :k, :p, :ts)",
            r=run,
            k=type_,
            p=json.dumps(payload or {}),
            ts=self.clock["t"],
        )

    def export(self, user, expect=201, **body):
        r = self.as_(user).post(f"{P}/exports", json=body)
        assert r.status_code == expect, r.text
        return r.json()

    def export_doc(self, user, job_id):
        r = self.as_(user).get(f"{P}/exports/{job_id}/download")
        assert r.status_code == 200, r.text
        return r

    def docs(self, kind=None, team="team-a"):
        sql = "SELECT kind, source_id, body, title FROM search_documents WHERE team_id = :t"
        if kind:
            return self.q(sql + " AND kind = :k", t=team, k=kind)
        return self.q(sql, t=team)

    def tick_s(self, seconds):
        self.tick(seconds=seconds)

    def plus(self, **kw):
        return self.clock["t"] + timedelta(**kw)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    monkeypatch.delenv("HARNESS_ALLOW_SELF_APPROVAL", raising=False)
    monkeypatch.setenv("HARNESS_SEARCH_AUTO_SYNC_SECONDS", "0")  # tests sync explicitly
    monkeypatch.setenv("HARNESS_EXPORT_DIR", str(tmp_path / "exports"))
    for name in (
        "HARNESS_EXPORT_MAX_BYTES",
        "HARNESS_EXPORT_MAX_RECORDS",
        "HARNESS_EXPORT_TTL_SECONDS",
        "HARNESS_SEARCH_ENGINE",
    ):
        monkeypatch.delenv(name, raising=False)
    clock = {"t": T0}
    monkeypatch.setattr(collab, "db_now", lambda: clock["t"])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d.db")

    async def setup():
        async with engine.begin() as conn:
            for d in (*DDL, *migration_statements()):
                await conn.execute(text(d))
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('p', 'team-a'), ('q', 'team-b')")
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state, branch, created_at, updated_at)"
                    " VALUES ('r1', 'p', 'executing', 'feature/alpha', :c, :c),"
                    " ('r2', 'p', 'done', 'fix/beta', :c, :c),"
                    " ('r3', 'q', 'executing', 'other', :c, :c)"
                ),
                {"c": T0},
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_plans (id, run_id, goal, created_at) VALUES"
                    " ('pl1', 'r1', 'Refactor the billing module', :c)"
                ),
                {"c": T0},
            )
            for u, (t, role) in USERS.items():
                await conn.execute(
                    text(
                        "INSERT INTO team_members (id, team_id, user_id, display_name, role)"
                        " VALUES (:i, :t, :u, :n, :r)"
                    ),
                    {"i": f"m-{u}", "t": t, "u": u, "n": f"Name {u}", "r": role.value},
                )
            for sid, team in SESSIONS.items():
                await conn.execute(
                    text(
                        "INSERT INTO collab_sessions (id, team_id, title, owner_user_id, state,"
                        " created_at, updated_at) VALUES (:i, :t, :ti, :o, 'active', :c, :c)"
                    ),
                    {
                        "i": sid,
                        "t": team,
                        "ti": f"Session title {sid}",
                        "o": "evil" if team == "team-b" else "lead",
                        "c": T0,
                    },
                )
            members = [
                ("s1", "lead", "owner"),
                ("s1", "eng2", "participant"),
                ("s1", "view", "observer"),
                ("spriv", "lead", "owner"),
                ("sb", "evil", "owner"),
            ]
            for sid, u, role in members:
                await conn.execute(
                    text(
                        "INSERT INTO session_members (id, session_id, kind, user_id, role,"
                        " display_name, status, joined_at) VALUES"
                        " (:i, :s, 'user', :u, :r, :n, 'active', :c)"
                    ),
                    {"i": f"sm-{sid}-{u}", "s": sid, "u": u, "r": role, "n": u, "c": T0},
                )
            await conn.execute(
                text(
                    "INSERT INTO session_runs (run_id, session_id, linked_by, linked_at)"
                    " VALUES ('r1', 's1', 'lead', :c)"
                ),
                {"c": T0},
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
    discussion.configure_discussion(service)
    search.configure_search(service)
    exports.configure_exports(service)
    monkeypatch.setattr(control, "_service", service)

    async def emit(db, **kw):
        return None

    fake = types.ModuleType("harness_orchestrator.notify")
    fake.emit = emit
    monkeypatch.setitem(sys.modules, "harness_orchestrator.notify", fake)
    monkeypatch.setattr(harness_orchestrator, "notify", fake, raising=False)

    app = FastAPI()
    app.include_router(orch_router)
    app.include_router(control.router)
    app.include_router(discussion.router)
    app.include_router(search.router)
    app.include_router(exports.router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1]
    )
    yield Env(TestClient(app), who, factory, clock, [])
    asyncio.run(engine.dispose())
