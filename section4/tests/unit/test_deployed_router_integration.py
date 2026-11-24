"""Sprint 3 / Task 17 — 0078 + 0080 through the deployed orchestrator app router.

The same two routers `deploy/runners/services.py::app_orchestrator` mounts are put on one
sqlite-backed FastAPI app (the repo's standard integration harness): assignment_plan.router
for 0078 and api.router for the collab approvals GET that now carries 0080's lesson.
No live Postgres needed here; the PG migration selftest is Task 11's evidence.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import assignment_plan
from harness_orchestrator.api import get_principal
from harness_orchestrator.case_memory import record_case
from harness_orchestrator.service import OrchestratorService

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)
P = "/api/v1/orchestrator/projects"
USERS = {
    "boss": ("team-a", Role.ADMIN), "sec": ("team-a", Role.ENGINEER),
    "evil": ("team-b", Role.ADMIN),
}
BRIEF = {
    "goals": ["ship login hardening"],
    "acceptanceCriteria": ["OTP lockout audited"],
    "tasks": [{"key": "threat", "title": "Threat-model the OTP flow", "preferredAssignee": "sec"}],
}

DDL = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)",
    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36), role VARCHAR(16))",
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36))",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, actor_member_id VARCHAR(36),"
    " actor_kind VARCHAR(16), action VARCHAR(80), target_type VARCHAR(60), target_id VARCHAR(80),"
    " detail TEXT, ts TIMESTAMP)",
    "CREATE TABLE assignment_plan_versions (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36),"
    " project_id VARCHAR(36), version INTEGER, parent_version INTEGER, state VARCHAR(16),"
    " brief TEXT, assignments TEXT, created_by VARCHAR(64), note VARCHAR(500),"
    " created_at TIMESTAMP, UNIQUE (project_id, version))",
    "CREATE TABLE case_memory (case_id VARCHAR(36) PRIMARY KEY, team_id TEXT NOT NULL,"
    " project_id TEXT, run_id TEXT NOT NULL, failure_class TEXT, tool TEXT, paths TEXT,"
    " fingerprint TEXT, outcome TEXT, weight REAL, suggested_rule TEXT,"
    " status TEXT CHECK (status = 'proposed'), recorded_at TIMESTAMP)",
    "CREATE TABLE high_impact_approvals (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " team_id VARCHAR(36) NOT NULL, requester_user_id VARCHAR(36) NOT NULL,"
    " action VARCHAR(16) NOT NULL, paths TEXT NOT NULL, summary TEXT NOT NULL,"
    " diff TEXT NOT NULL DEFAULT '', digest VARCHAR(64) NOT NULL,"
    " state VARCHAR(16) NOT NULL DEFAULT 'pending', decided_by VARCHAR(36),"
    " decided_at TIMESTAMP, expires_at TIMESTAMP NOT NULL, outcome TEXT, created_at TIMESTAMP)",
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL, actor_user_id VARCHAR(36),"
    " kind TEXT NOT NULL, request_id VARCHAR(36), payload TEXT, ts TIMESTAMP)",
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/deployed.db")

    async def setup():
        async with engine.begin() as c:
            for d in DDL:
                await c.execute(text(d))
            await c.execute(text(
                "INSERT INTO projects (id, team_id) VALUES ('p','team-a'),('q','team-b')"))
            await c.execute(text("INSERT INTO agent_runs (id, project_id) VALUES ('r-now','p')"))
            for u, (t, r) in USERS.items():
                await c.execute(text("INSERT INTO team_members (id, team_id, user_id, role)"
                                     " VALUES (:i,:t,:u,:r)"),
                                {"i": f"m-{u}", "t": t, "u": u, "r": r.value})
            for run, when in (("r-now", NOW), ("r-old", NOW - timedelta(days=1))):
                case = record_case(run, "denials_repeated", "bash", ["/etc/nginx/nginx.conf"],
                                   "denied", when)
                await c.execute(text(
                    "INSERT INTO case_memory (case_id, team_id, project_id, run_id, failure_class,"
                    " tool, paths, fingerprint, outcome, weight, suggested_rule, status,"
                    " recorded_at)"
                    " VALUES (:c,'team-a','p',:r,:f,:tool,:paths,:fp,:o,:w,:s,'proposed',:at)"),
                    {"c": case["caseId"], "r": run, "f": case["failureClass"],
                     "tool": case["tool"], "paths": json.dumps(case["paths"]),
                     "fp": case["fingerprint"], "o": case["outcome"], "w": case["weight"],
                     "s": case["suggestedRule"], "at": when})
            await c.execute(text(
                "INSERT INTO high_impact_approvals (id, run_id, team_id, requester_user_id,"
                " action, paths, summary, digest, expires_at, created_at)"
                " VALUES ('a1','r-now','team-a','eng','modify',:p,'s','d',:x,:c)"),
                {"p": json.dumps(["/etc/nginx/nginx.conf"]),
                 "x": datetime.now(UTC) + timedelta(days=1), "c": NOW})

    asyncio.run(setup())
    monkeypatch.setattr(orch_api, "_service", OrchestratorService(
        session_factory=async_sessionmaker(engine, expire_on_commit=False), bus=None))
    app = FastAPI()
    app.include_router(assignment_plan.router)
    app.include_router(orch_api.router)
    who = {"u": "boss"}

    def principal():
        t, r = USERS[who["u"]]
        return Principal(userId=who["u"], teamId=t, role=r)

    app.dependency_overrides[get_principal] = principal
    client = TestClient(app)

    def as_user(u):
        who["u"] = u
        return client

    yield as_user
    asyncio.run(engine.dispose())


def test_0078_plan_approval_and_member_mine_through_deployed_router(client):
    boss = client("boss")
    v1 = boss.post(f"{P}/p/assignment-plans", json=BRIEF)
    assert v1.status_code in (200, 201) and v1.json()["state"] == "proposed"
    approved = boss.post(f"{P}/p/assignment-plans/1/review", json={"decision": "approve"})
    assert approved.status_code in (200, 201) and approved.json()["state"] == "approved"
    mine = client("sec").get(f"{P}/p/assignment-plans/mine")
    assert mine.status_code == 200
    assert [t["task"] for t in mine.json()["tasks"]] == ["threat"]


def test_0080_similar_denials_through_deployed_approvals_route(client):
    res = client("boss").get("/api/v1/orchestrator/runs/r-now/approvals")
    assert res.status_code == 200, res.text
    approval = res.json()["approvals"][0]
    assert approval["id"] == "a1"
    assert len(approval["similarCases"]) >= 1
    assert "r-old" in [c["runId"] for c in approval["similarCases"]]
