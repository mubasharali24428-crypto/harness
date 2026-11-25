"""Phase 7 slice (C-01..C-04): admin-reviewed assignment proposals. Drives the real routes on
sqlite; table mirrors migration 0078."""

import asyncio
import json

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
    "obs": ("team-a", Role.VIEWER),
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


def test_reject_requires_a_note_and_keeps_it_in_history(as_):
    c = as_("boss")
    c.post(f"{P}/p/assignment-plans", json=BRIEF)
    empty = c.post(f"{P}/p/assignment-plans/1/review", json={"decision": "reject", "note": "   "})
    assert empty.status_code == 422
    assert empty.json()["detail"]["error"]["code"] == "NOTE_REQUIRED"
    rejected = c.post(f"{P}/p/assignment-plans/1/review",
                      json={"decision": "reject", "note": "Disagree: keep the pen test in scope"})
    assert rejected.status_code in (200, 201)
    versions = c.get(f"{P}/p/assignment-plans").json()["versions"]
    assert versions[-1]["state"] == "rejected"
    assert versions[-1]["note"] == "Disagree: keep the pen test in scope"


def test_mine_dependency_readiness_has_no_oracle(as_):
    brief = {**BRIEF, "tasks": [
        {"key": "a", "title": "SECRET-A-BODY", "preferredAssignee": "sec"},
        {"key": "b", "title": "SECRET-B-BODY", "preferredAssignee": "sec", "dependsOn": ["a"]},
        {"key": "c", "title": "SECRET-C-BODY", "preferredAssignee": "ui", "dependsOn": ["b"]},
    ]}
    boss = as_("boss")
    boss.post(f"{P}/p/assignment-plans", json=brief)
    boss.post(f"{P}/p/assignment-plans/1/review", json={"decision": "approve"})
    sec = as_("sec").get(f"{P}/p/assignment-plans/mine").json()
    ui = as_("ui").get(f"{P}/p/assignment-plans/mine").json()
    by_task = {t["task"]: t for t in sec["tasks"] + ui["tasks"]}
    assert by_task["b"]["dependsOnStatus"] == {"a": "ready"}   # both a and b are sec's
    assert by_task["c"]["dependsOnStatus"] == {"b": "waiting"}  # b is someone else's
    sec_blob, ui_blob = json.dumps(sec), json.dumps(ui)
    assert "SECRET-C-BODY" not in sec_blob  # ui's task body never reaches sec
    assert "SECRET-A-BODY" not in ui_blob and "SECRET-B-BODY" not in ui_blob
    assert [t["task"] for t in sec["tasks"]] == ["a", "b"]
    assert [t["task"] for t in ui["tasks"]] == ["c"]


# --- 7.1 (C-01) brief completeness: roles, capabilities, priorities, outputs, cycles ---------

FULL_BRIEF = {
    "goals": ["ship login hardening"],
    "outputs": ["hardened OTP flow", "threat model doc"],
    "roles": [
        {"name": "security", "capabilities": ["threat-modelling", "otp"]},
        {"name": "frontend", "capabilities": ["react"]},
    ],
    "tasks": [
        {"key": "threat", "title": "Threat-model the OTP flow", "requiredRole": "security",
         "preferredAssignee": "sec", "priority": "high", "outputs": ["threat model doc"]},
        {"key": "form", "title": "Lockout form", "requiredRole": "frontend",
         "preferredAssignee": "evil", "requiredCapabilities": ["a11y"], "dependsOn": ["threat"]},
        {"key": "pen", "title": "External pen test", "requiredRole": "security"},
    ],
}


def test_full_brief_round_trips_and_is_honest(as_):
    v1 = as_("boss").post(f"{P}/p/assignment-plans", json=FULL_BRIEF)
    assert v1.status_code == 201, v1.text
    body = v1.json()
    assert body["brief"]["outputs"] == ["hardened OTP flow", "threat model doc"]
    assert body["brief"]["roles"][0] == {"name": "security",
                                         "capabilities": ["threat-modelling", "otp"]}
    by = {a["task"]: a for a in body["assignments"]}
    # A named member: assigned, but capability fit and availability are never asserted.
    assert by["threat"]["assignee"] == "sec" and by["threat"]["assigneeStatus"] == "member"
    assert by["threat"]["priority"] == "high"
    assert by["threat"]["requiredCapabilities"] == ["threat-modelling", "otp"]
    assert by["threat"]["capabilityFit"] == "unverified"
    assert by["threat"]["availability"] == "unknown"
    assert by["threat"]["missingExpertise"] == []
    # A named person from ANOTHER team is not a member here: unassigned, and the role plus
    # every required capability (role's and task's own, de-duplicated) is listed as missing.
    assert by["form"]["assignee"] is None and by["form"]["assigneeStatus"] == "not_a_member"
    assert by["form"]["missingExpertise"] == ["frontend", "react", "a11y"]
    assert by["form"]["uncertainty"] == "unknown" and by["form"]["priority"] == "unspecified"
    assert "not a current team member" in by["form"]["rationale"]
    # Nobody named: a different, equally honest status.
    assert by["pen"]["assigneeStatus"] == "unnamed" and by["pen"]["assignee"] is None
    assert "team-b" not in v1.text  # the other team is never described


def test_dependency_cycles_are_rejected(as_):
    def tasks(*edges):
        return {"goals": ["g"], "tasks": [
            {"key": k, "title": k, "dependsOn": list(d)} for k, d in edges]}

    for brief, cycle in (
        (tasks(("a", ["a"])), "a -> a"),
        (tasks(("a", ["b"]), ("b", ["a"])), "a -> b -> a"),
        (tasks(("a", []), ("b", ["c"]), ("c", ["d"]), ("d", ["b"])), "b -> c -> d -> b"),
    ):
        r = as_("boss").post(f"{P}/p/assignment-plans", json=brief)
        assert r.status_code == 422, brief
        err = r.json()["detail"]["error"]
        assert err["code"] == "BAD_BRIEF" and cycle in err["message"]
    diamond = tasks(("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"]))
    assert as_("boss").post(f"{P}/p/assignment-plans", json=diamond).status_code == 201


def test_undeclared_role_and_duplicate_roles_are_rejected(as_):
    undeclared = {**FULL_BRIEF, "tasks": [{"key": "x", "title": "x", "requiredRole": "dba"}]}
    r = as_("boss").post(f"{P}/p/assignment-plans", json=undeclared)
    assert r.status_code == 422 and "'dba'" in r.json()["detail"]["error"]["message"]
    dup = {**FULL_BRIEF, "roles": [{"name": "security"}, {"name": "security"}]}
    assert as_("boss").post(f"{P}/p/assignment-plans", json=dup).status_code == 422
    # With no roles declared, a free-text requiredRole stays allowed (pre-7.1 briefs).
    assert as_("boss").post(f"{P}/p/assignment-plans", json=BRIEF).status_code == 201


def test_bad_priority_and_unknown_fields_are_rejected(as_):
    bad = {"goals": ["g"], "tasks": [{"key": "a", "title": "a", "priority": "urgent"}]}
    assert as_("boss").post(f"{P}/p/assignment-plans", json=bad).status_code == 422
    extra = {"goals": ["g"], "tasks": [{"key": "a", "title": "a", "availability": "free"}]}
    assert as_("boss").post(f"{P}/p/assignment-plans", json=extra).status_code == 422


def test_edit_updates_assignee_status(as_):
    c = as_("boss")
    c.post(f"{P}/p/assignment-plans", json=FULL_BRIEF)
    v2 = c.post(f"{P}/p/assignment-plans/1/review",
                json={"decision": "edit", "reassign": [
                    {"task": "form", "assignee": "ui"},
                    {"task": "threat", "assignee": None}]}).json()
    by = {a["task"]: a for a in v2["assignments"]}
    assert by["form"]["assigneeStatus"] == "member" and by["form"]["assignee"] == "ui"
    assert by["threat"]["assigneeStatus"] == "unnamed" and by["threat"]["assignee"] is None
    assert by["form"]["capabilityFit"] == "unverified"  # an admin pick is not a skill check


def test_mine_shows_own_priority_and_outputs_only(as_):
    c = as_("boss")
    brief = {**FULL_BRIEF, "tasks": [
        {"key": "threat", "title": "Threat-model", "preferredAssignee": "sec",
         "priority": "high", "outputs": ["SECRET-SEC-OUTPUT"]},
        {"key": "form", "title": "Form", "preferredAssignee": "ui", "priority": "low",
         "outputs": ["SECRET-UI-OUTPUT"], "dependsOn": ["threat"]},
    ]}
    c.post(f"{P}/p/assignment-plans", json=brief)
    c.post(f"{P}/p/assignment-plans/1/review", json={"decision": "approve"})
    sec = as_("sec").get(f"{P}/p/assignment-plans/mine")
    ui = as_("ui").get(f"{P}/p/assignment-plans/mine")
    assert sec.json()["tasks"][0]["priority"] == "high"
    assert sec.json()["tasks"][0]["outputs"] == ["SECRET-SEC-OUTPUT"]
    assert ui.json()["tasks"][0]["priority"] == "low"
    assert "SECRET-UI-OUTPUT" not in sec.text and "SECRET-SEC-OUTPUT" not in ui.text


def test_pre_7_1_stored_version_still_serves_mine(as_):
    """A version stored before 7.1 has no priority/outputs keys; /mine must not 500."""
    old_brief = json.dumps({"goals": ["g"], "constraints": [], "acceptanceCriteria": [],
                            "tasks": [{"key": "a", "title": "A", "kind": "human",
                                       "requiredRole": None, "preferredAssignee": "sec",
                                       "dependsOn": []}]})
    old_assign = json.dumps([{"task": "a", "kind": "human", "dependsOn": [], "assignee": "sec",
                              "rationale": "x", "uncertainty": "low", "missingExpertise": [],
                              "costEstimate": "unknown"}])

    async def seed():
        async with orch_api._service.session_factory() as db:
            await db.execute(text(
                "INSERT INTO assignment_plan_versions VALUES ('old', 'team-a', 'p', 1, NULL,"
                " 'approved', :b, :a, 'boss', NULL, '2026-11-20 00:00:00')"),
                {"b": old_brief, "a": old_assign})
            await db.commit()

    asyncio.run(seed())
    t = as_("sec").get(f"{P}/p/assignment-plans/mine").json()["tasks"]
    assert t == [{"task": "a", "title": "A", "kind": "human", "dependsOn": [],
                  "priority": "unspecified", "outputs": [], "dependsOnStatus": {}}]


def test_read_only_seats_are_never_proposed_or_assigned(as_):
    """7.7 eval finding: a viewer named in the brief used to be proposed for work."""
    brief = {"goals": ["g"], "tasks": [
        {"key": "docs", "title": "Write docs", "preferredAssignee": "obs"},
        {"key": "code", "title": "Write code", "preferredAssignee": "sec"}]}
    c = as_("boss")
    by = {a["task"]: a for a in c.post(f"{P}/p/assignment-plans", json=brief).json()["assignments"]}
    assert by["docs"]["assignee"] is None and by["docs"]["assigneeStatus"] == "read_only"
    assert "read-only seat" in by["docs"]["rationale"] and by["docs"]["missingExpertise"]
    assert by["code"]["assignee"] == "sec"
    r = c.post(f"{P}/p/assignment-plans/1/review",
               json={"decision": "edit", "reassign": [{"task": "code", "assignee": "obs"}]})
    assert r.status_code == 422 and r.json()["detail"]["error"]["code"] == "BAD_REASSIGN"


def test_read_only_role_match_is_case_insensitive(as_):
    """ORM writes persist enum NAMES ('VIEWER'); the planner must still see a read-only seat."""
    async def seed():
        async with orch_api._service.session_factory() as db:
            await db.execute(text("INSERT INTO team_members VALUES ('m-v2', 'team-a', 'v2',"
                                  " 'VIEWER')"))
            await db.commit()

    asyncio.run(seed())
    brief = {"goals": ["g"], "tasks": [{"key": "a", "title": "A", "preferredAssignee": "v2"}]}
    (a,) = as_("boss").post(f"{P}/p/assignment-plans", json=brief).json()["assignments"]
    assert a["assigneeStatus"] == "read_only" and a["assignee"] is None
