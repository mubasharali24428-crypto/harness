"""Phase 7 slice (C-01..C-04): admin-reviewed assignment proposals. Drives the real routes on
sqlite; table mirrors migration 0078."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import assignment_plan
from harness_orchestrator.api import get_principal
from harness_orchestrator.service import OrchestratorService

DDL = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)",
    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36), role VARCHAR(16))",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, actor_member_id VARCHAR(36),"
    " actor_kind VARCHAR(16), action VARCHAR(80), target_type VARCHAR(60), target_id VARCHAR(80),"
    " detail TEXT, ts TIMESTAMP)",
    "CREATE TABLE assignment_plan_versions (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36),"
    " project_id VARCHAR(36), version INTEGER, parent_version INTEGER, state VARCHAR(16),"
    " brief TEXT, assignments TEXT, created_by VARCHAR(64), note VARCHAR(500),"
    " created_at TIMESTAMP, UNIQUE (project_id, version))",
)
USERS = {
    "boss": ("team-a", Role.ADMIN), "sec": ("team-a", Role.ENGINEER),
    "ui": ("team-a", Role.ENGINEER), "evil": ("team-b", Role.ADMIN),
}
BRIEF = {
    "goals": ["ship login hardening"],
    "acceptanceCriteria": ["OTP lockout audited"],
    "tasks": [
        {"key": "threat", "title": "Threat-model the OTP flow", "requiredRole": "security",
         "preferredAssignee": "sec"},
        {"key": "form", "title": "SECRET-UI-TASK-BODY", "preferredAssignee": "ui",
         "dependsOn": ["threat"]},
        {"key": "pen", "title": "External pen test", "requiredRole": "pentester",
         "preferredAssignee": "nobody-here"},
    ],
}
P = "/api/v1/orchestrator/projects"


@pytest.fixture()
def as_(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/a.db")

    async def setup():
        async with engine.begin() as c:
            for d in DDL:
                await c.execute(text(d))
            await c.execute(text("INSERT INTO projects VALUES ('p', 'team-a'), ('q', 'team-b')"))
            for u, (t, r) in USERS.items():
                await c.execute(text("INSERT INTO team_members VALUES (:i, :t, :u, :r)"),
                                {"i": f"m-{u}", "t": t, "u": u, "r": r.value})

    asyncio.run(setup())
    monkeypatch.setattr(orch_api, "_service", OrchestratorService(
        session_factory=async_sessionmaker(engine, expire_on_commit=False), bus=None))
    app = FastAPI()
    app.include_router(assignment_plan.router)
    who = {"u": "boss"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1])
    client = TestClient(app)

    def as_user(u):
        who["u"] = u
        return client

    yield as_user
    asyncio.run(engine.dispose())


def test_proposal_is_honest_about_unknowns(as_):
    v1 = as_("boss").post(f"{P}/p/assignment-plans", json=BRIEF).json()
    by = {a["task"]: a for a in v1["assignments"]}
    assert v1["version"] == 1 and v1["state"] == "proposed"
    assert by["threat"]["assignee"] == "sec" and by["threat"]["costEstimate"] == "unknown"
    assert by["pen"]["assignee"] is None and by["pen"]["uncertainty"] == "unknown"
    assert by["pen"]["missingExpertise"] == ["pentester"]  # non-member is not fabricated


def test_review_versions_and_v1_stays_readable(as_):
    c = as_("boss")
    c.post(f"{P}/p/assignment-plans", json=BRIEF)
    v2 = c.post(f"{P}/p/assignment-plans/1/review",
                json={"decision": "edit", "reassign": [{"task": "pen", "assignee": "sec"}]}).json()
    assert v2["version"] == 2 and v2["state"] == "proposed"  # an edit needs renewed review
    assert c.post(f"{P}/p/assignment-plans/1/review",
                  json={"decision": "approve"}).status_code == 409  # stale: re-review latest
    v3 = c.post(f"{P}/p/assignment-plans/2/review", json={"decision": "approve"}).json()
    assert v3["state"] == "approved"
    versions = c.get(f"{P}/p/assignment-plans").json()["versions"]
    assert [(v["version"], v["state"]) for v in versions] == [
        (1, "proposed"), (2, "proposed"), (3, "approved")]
    assert {a["task"]: a["assignee"] for a in versions[0]["assignments"]}["pen"] is None
    assert c.post(f"{P}/p/assignment-plans/3/review",
                  json={"decision": "approve"}).status_code == 409  # PLAN_DECIDED


def test_members_see_only_their_own_tasks(as_):
    c = as_("boss")
    c.post(f"{P}/p/assignment-plans", json=BRIEF)
    assert as_("sec").get(f"{P}/p/assignment-plans/mine").json()["tasks"] == []  # not approved
    as_("boss").post(f"{P}/p/assignment-plans/1/review", json={"decision": "approve"})
    sec = as_("sec").get(f"{P}/p/assignment-plans/mine")
    assert [t["task"] for t in sec.json()["tasks"]] == ["threat"]
    assert "SECRET-UI-TASK-BODY" not in sec.text  # another member's task body never leaks
    ui = as_("ui").get(f"{P}/p/assignment-plans/mine").json()
    assert ui["tasks"][0]["dependsOn"] == ["threat"]  # dependency KEY only (C-05)
    assert as_("sec").get(f"{P}/p/assignment-plans").status_code == 403
    assert as_("sec").post(f"{P}/p/assignment-plans", json=BRIEF).status_code == 403


def test_foreign_project_is_not_an_oracle(as_):
    c = as_("evil")
    for path in ("/p/assignment-plans", "/missing/assignment-plans"):
        r = c.get(P + path)
        assert r.status_code == 404 and r.json()["detail"]["error"]["code"] == "NOT_FOUND"
    assert c.post(f"{P}/p/assignment-plans", json=BRIEF).status_code == 404


def test_bad_brief_rejected(as_):
    bad = {**BRIEF, "tasks": [{"key": "a", "title": "x", "dependsOn": ["zzz"]}]}
    assert as_("boss").post(f"{P}/p/assignment-plans", json=bad).status_code == 422
