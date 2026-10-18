"""Multiplayer pilot S2: participants/leader, redirect, handoff, high-impact approvals.

Drives the real routes. Tables mirror migration 0035 (sqlite types); the
migration itself was applied to Postgres separately.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.service import OrchestratorService

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
    "view": ("team-a", Role.VIEWER),
    "evil": ("team-b", Role.ADMIN),
}


class Bus:
    async def publish(self, *a, **k):
        return None

    async def ensure_run(self, *a, **k):
        return None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/c.db")

    async def setup():
        async with engine.begin() as conn:
            for d in DDL:
                await conn.execute(text(d))
            await conn.execute(text("INSERT INTO projects (id, team_id) VALUES ('p', 'team-a')"))
            await conn.execute(
                text("INSERT INTO agent_runs (id, project_id) VALUES ('r1', 'p'), ('r2', 'p')")
            )
            for u, (t, role) in USERS.items():
                await conn.execute(
                    text(
                        "INSERT INTO team_members (id, team_id, user_id, role)"
                        " VALUES (:i, :t, :u, :r)"
                    ),
                    {"i": f"m-{u}", "t": t, "u": u, "r": role.value},
                )

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=Bus())
    monkeypatch.setattr(orch_api, "_service", service)
    app = FastAPI()
    app.include_router(orch_router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1]
    )
    client = TestClient(app)

    def as_(user):
        who["u"] = user
        return client

    yield as_, factory
    asyncio.run(engine.dispose())


P = "/api/v1/orchestrator/runs"


def q(factory, sql, **kw):
    async def go():
        async with factory() as db:
            return (await db.execute(text(sql), kw)).all()

    return asyncio.run(go())


def audit_actions(factory):
    return [r.action for r in q(factory, "SELECT action FROM audit_log ORDER BY seq")]


def lead_run(as_):
    """lead is leader; eng2 and view have joined."""
    assert as_("lead").post(f"{P}/r1/leader").status_code == 200
    as_("eng2").post(f"{P}/r1/join")
    as_("view").post(f"{P}/r1/join")


# ------------------------------------------------------------------ leader/roster


def test_single_leader_and_roster(env):
    as_, f = env
    r = as_("lead").post(f"{P}/r1/leader")
    assert r.status_code == 200 and r.json()["leader"] == "lead"
    as_("eng2").post(f"{P}/r1/join")
    second = as_("eng2").post(f"{P}/r1/leader")
    assert second.status_code == 409 and second.json()["detail"]["error"]["code"] == "LEADER_EXISTS"
    roster = as_("view").post(f"{P}/r1/join").json()
    roles = {p["userId"]: p["role"] for p in roster["participants"]}
    assert roles == {"lead": "leader", "eng2": "participant", "view": "observer"}
    assert as_("view").post(f"{P}/r1/leader").status_code == 403  # viewer cannot lead


def test_foreign_team_gets_404_everywhere(env):
    as_, _ = env
    lead_run(as_)
    c = as_("evil")
    for method, path in [
        ("post", "/r1/join"),
        ("get", "/r1/participants"),
        ("post", "/r1/leader"),
        ("get", "/r1/collab-history"),
        ("get", "/r1/approvals"),
    ]:
        assert getattr(c, method)(P + path).status_code in (403, 404)
    assert c.get(f"{P}/r1/participants").status_code == 404  # no existence oracle
    assert c.post(f"{P}/r1/redirect", json={"content": "x"}).status_code == 404


# ----------------------------------------------------------------------- redirect


def test_guidance_is_recorded_with_actor_and_idempotent(env):
    as_, f = env
    lead_run(as_)
    body = {"content": "prefer small commits", "requestId": "req-1"}
    first = as_("eng2").post(f"{P}/r1/redirect", json=body).json()
    again = as_("eng2").post(f"{P}/r1/redirect", json=body).json()
    assert first["seq"] == again["seq"] and again["duplicate"] is True
    ev = as_("view").get(f"{P}/r1/collab-history").json()["events"]
    g = [e for e in ev if e["kind"] == "redirect.guidance"]
    assert len(g) == 1 and g[0]["actor"] == "eng2"
    assert g[0]["payload"]["content"] == "prefer small commits"
    assert audit_actions(f).count("redirect.recorded") == 1


def test_observer_and_non_participant_cannot_redirect(env):
    as_, _ = env
    lead_run(as_)
    assert as_("view").post(f"{P}/r1/redirect", json={"content": "x"}).status_code == 403
    # team member who never joined
    assert as_("eng2").post(f"{P}/r2/redirect", json={"content": "x"}).status_code == 403


def test_scope_change_needs_reason_and_leader(env):
    as_, f = env
    lead_run(as_)
    c = as_("lead")
    no_reason = c.post(f"{P}/r1/redirect", json={"kind": "scope_change", "content": "also fix B"})
    assert no_reason.status_code == 422
    assert no_reason.json()["detail"]["error"]["code"] == "REASON_REQUIRED"
    ok = c.post(
        f"{P}/r1/redirect",
        json={"kind": "scope_change", "content": "also fix B", "reason": "customer ask"},
    )
    assert ok.status_code == 200
    denied = as_("eng2").post(
        f"{P}/r1/redirect",
        json={"kind": "permission_change", "content": "allow net", "reason": "need pkg"},
    )
    assert denied.status_code == 403 and denied.json()["detail"]["error"]["code"] == "NOT_LEADER"
    acts = audit_actions(f)
    assert "redirect.scope_changed" in acts and "authz.denied" in acts  # denial is evidenced


# ----------------------------------------------------------------------- handoff


def test_handoff_ownership_transfers_only_on_accept(env):
    as_, f = env
    lead_run(as_)
    h = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"}).json()
    # proposer still leads; a second open handoff is refused
    dup = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"})
    assert dup.status_code == 409 and dup.json()["detail"]["error"]["code"] == "HANDOFF_PENDING"
    assert as_("lead").get(f"{P}/r1/participants").json()["leader"] == "lead"
    # only the recipient can decide
    wrong = as_("lead").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": True})
    assert wrong.status_code == 403
    ok = as_("eng2").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": True})
    assert ok.status_code == 200 and ok.json()["leader"] == "eng2"
    roster = as_("lead").get(f"{P}/r1/participants").json()
    assert roster["leader"] == "eng2" and roster["pendingHandoff"] is None
    assert (
        as_("eng2").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": True}).status_code
        == 404
    )
    kinds = [e["kind"] for e in as_("lead").get(f"{P}/r1/collab-history").json()["events"]]
    assert "handoff.requested" in kinds and "handoff.accepted" in kinds  # history preserved


def test_handoff_rejection_keeps_leader_and_bad_recipient_refused(env):
    as_, _ = env
    lead_run(as_)
    assert as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "view"}).status_code == 422
    assert as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "evil"}).status_code == 422
    assert as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "lead"}).status_code == 422
    h = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"}).json()
    as_("eng2").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": False})
    assert as_("lead").get(f"{P}/r1/participants").json()["leader"] == "lead"
    assert as_("eng2").post(f"{P}/r1/handoff", json={"toUserId": "lead"}).status_code == 403


# --------------------------------------------------------------------- approvals

PROPOSAL = {
    "action": "delete",
    "paths": ["src/old.py"],
    "summary": "remove dead module",
    "diff": "-x",
}


def new_approval(as_, user="eng2"):
    r = as_(user).post(f"{P}/r1/approvals", json=PROPOSAL)
    assert r.status_code == 201, r.text
    return r.json()


def test_approval_flow_blocks_until_leader_approves_exact_digest(env):
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    assert a["state"] == "pending" and a["designatedApprover"] == "lead"
    aid, digest = a["id"], a["digest"]
    # cannot execute while pending
    assert (
        as_("eng2").post(f"{P}/r1/approvals/{aid}/claim", json={"digest": digest}).status_code
        == 409
    )
    # non-leader cannot decide
    deny = as_("eng2").post(
        f"{P}/r1/approvals/{aid}/decision", json={"approve": True, "digest": digest}
    )
    assert deny.status_code == 403
    # stale/wrong digest rejected
    bad = as_("lead").post(
        f"{P}/r1/approvals/{aid}/decision", json={"approve": True, "digest": "0" * 64}
    )
    assert bad.status_code == 409 and bad.json()["detail"]["error"]["code"] == "DIGEST_MISMATCH"
    ok = as_("lead").post(
        f"{P}/r1/approvals/{aid}/decision", json={"approve": True, "digest": digest}
    )
    assert ok.json()["state"] == "approved" and ok.json()["decidedBy"] == "lead"
    # replayed decision is refused
    again = as_("lead").post(
        f"{P}/r1/approvals/{aid}/decision", json={"approve": True, "digest": digest}
    )
    assert again.status_code == 409
    # only requester/leader may execute; a bystander cannot
    assert (
        as_("view").post(f"{P}/r1/approvals/{aid}/claim", json={"digest": digest}).status_code
        == 403
    )
    claim = as_("eng2").post(f"{P}/r1/approvals/{aid}/claim", json={"digest": digest})
    assert claim.json()["state"] == "executing"
    assert (
        as_("eng2").post(f"{P}/r1/approvals/{aid}/claim", json={"digest": digest}).status_code
        == 409
    )
    done = as_("eng2").post(
        f"{P}/r1/approvals/{aid}/complete", json={"ok": True, "outcome": "deleted"}
    )
    assert done.json()["state"] == "completed"
    acts = audit_actions(f)
    for a_ in ("approval.requested", "approval.approved", "approval.claimed", "approval.completed"):
        assert a_ in acts


def test_amending_an_approved_proposal_voids_the_approval(env):
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    as_("lead").post(
        f"{P}/r1/approvals/{a['id']}/decision", json={"approve": True, "digest": a["digest"]}
    )
    amended = (
        as_("eng2")
        .put(
            f"{P}/r1/approvals/{a['id']}",
            json={**PROPOSAL, "paths": ["src/old.py", "src/extra.py"]},
        )
        .json()
    )
    assert amended["state"] == "pending" and amended["digest"] != a["digest"]
    # old digest can no longer be claimed or approved
    assert (
        as_("eng2")
        .post(f"{P}/r1/approvals/{a['id']}/claim", json={"digest": a["digest"]})
        .status_code
        == 409
    )
    assert (
        as_("lead")
        .post(f"{P}/r1/approvals/{a['id']}/decision", json={"approve": True, "digest": a["digest"]})
        .status_code
        == 409
    )
    assert "approval.invalidated" in audit_actions(f)
    # only the requester may amend
    assert as_("lead").put(f"{P}/r1/approvals/{a['id']}", json=PROPOSAL).status_code == 403


def test_scope_change_invalidates_open_and_approved_requests(env):
    as_, _ = env
    lead_run(as_)
    a1, _ = new_approval(as_), new_approval(as_)
    as_("lead").post(
        f"{P}/r1/approvals/{a1['id']}/decision", json={"approve": True, "digest": a1["digest"]}
    )
    r = as_("lead").post(
        f"{P}/r1/redirect", json={"kind": "scope_change", "content": "narrow it", "reason": "risk"}
    )
    assert r.json()["invalidatedApprovals"] == 2
    states = {x["id"]: x["state"] for x in as_("lead").get(f"{P}/r1/approvals").json()["approvals"]}
    assert set(states.values()) == {"invalidated"}
    assert (
        as_("eng2")
        .post(f"{P}/r1/approvals/{a1['id']}/claim", json={"digest": a1["digest"]})
        .status_code
        == 409
    )


def test_expired_approval_cannot_be_decided(env, monkeypatch):
    as_, f = env
    lead_run(as_)
    monkeypatch.setenv("HARNESS_APPROVAL_TTL_SECONDS", "1")
    a = new_approval(as_)
    import time

    time.sleep(1.2)
    r = as_("lead").post(
        f"{P}/r1/approvals/{a['id']}/decision", json={"approve": True, "digest": a["digest"]}
    )
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "EXPIRED"
    assert q(f, "SELECT state FROM high_impact_approvals")[0].state == "expired"
    assert "approval.expired" in audit_actions(f)


@pytest.mark.parametrize("paths", [["../etc/passwd"], ["/abs"], ["a/../b"], ["a\\b"], [], [""]])
def test_hostile_paths_rejected(env, paths):
    as_, _ = env
    lead_run(as_)
    r = as_("eng2").post(f"{P}/r1/approvals", json={**PROPOSAL, "paths": paths})
    assert r.status_code == 422


def test_approval_is_scoped_to_its_run(env):
    as_, _ = env
    lead_run(as_)
    a = new_approval(as_)
    as_("lead").post(f"{P}/r2/leader")
    r = as_("lead").post(
        f"{P}/r2/approvals/{a['id']}/decision", json={"approve": True, "digest": a["digest"]}
    )
    assert r.status_code == 404  # r1's approval is invisible through r2


def test_concurrent_decisions_have_one_winner(env):
    """Two simultaneous decisions: the CAS UPDATE lets exactly one through."""
    from harness_contracts.roles import Principal

    from harness_orchestrator import collab

    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    lead = Principal(userId="lead", teamId="team-a", role=Role.ENGINEER)

    async def go():
        return await asyncio.gather(
            collab.decide_approval(f, lead, "r1", a["id"], True, a["digest"]),
            collab.decide_approval(f, lead, "r1", a["id"], False, a["digest"]),
            return_exceptions=True,
        )

    results = asyncio.run(go())
    wins = [r for r in results if isinstance(r, dict)]
    assert len(wins) == 1, results
    assert json.loads(json.dumps(wins[0]))["state"] in ("approved", "rejected")


# ------------------------------------------------- S2 follow-ups: persistence + engine path


class InsertingBus(Bus):
    def __init__(self, factory):
        self.factory = factory

    async def ensure_run(self, project_id, run_id):
        async with self.factory() as db:
            await db.execute(
                text("INSERT INTO agent_runs (id, project_id) VALUES (:r, :p)"),
                {"r": run_id, "p": project_id},
            )
            await db.commit()


def test_memory_only_run_is_persisted_before_join_or_409(env):
    as_, f = env
    svc = orch_api._service
    asyncio.run(svc._ensure_run("r3", project_id="p"))  # in memory, no agent_runs row
    assert not q(f, "SELECT 1 FROM agent_runs WHERE id = 'r3'")
    svc.bus = Bus()  # cannot persist -> clean 409, not an FK 500
    r = as_("lead").post(f"{P}/r3/join")
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "RUN_NOT_PERSISTED"
    svc.bus = InsertingBus(f)
    assert as_("lead").post(f"{P}/r3/leader").status_code == 200
    assert q(f, "SELECT 1 FROM agent_runs WHERE id = 'r3'")


class FakeEngine:
    model = "fake"

    def __init__(self):
        self.goals = []

    def execute_goal(self, goal, session_id):
        self.goals.append(goal)
        return {"status": "error", "finalResponse": "stop here"}


def test_engine_blocked_by_pending_approval_and_receives_guidance_once(env):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from harness_orchestrator import collab
    from harness_orchestrator.state_machine import AgentState

    as_, f = env
    lead_run(as_)
    svc = orch_api._service
    svc._budget_gate = AsyncMock(return_value=False)
    svc.engine = FakeEngine()
    plan = SimpleNamespace(goal="fix bug 12")
    lead = Principal(userId="lead", teamId="team-a", role=Role.ENGINEER)
    eng = Principal(userId="eng2", teamId="team-a", role=Role.ENGINEER)

    async def go():
        rd = await svc._ensure_run("r1")
        rd["state_machine"].state = AgentState.EXECUTING
        a = await collab.request_approval(f, eng, "r1", "delete", ["a.py"], "rm", "")
        await svc._execute(rd, plan)
        blocked = (rd["state_machine"].state, list(svc.engine.goals), rd["engine_result"]["status"])

        await collab.decide_approval(f, lead, "r1", a["id"], False, a["digest"])
        await collab.redirect(f, eng, "r1", "guidance", "use the v2 api", None, None)
        await collab.redirect(f, lead, "r1", "scope_change", "skip tests dir", "time", None)
        await collab.redirect(f, lead, "r1", "permission_change", "SECRETNET allowed", "x", None)
        rd["state_machine"].state = AgentState.EXECUTING
        await svc._execute(rd, plan)
        first = svc.engine.goals[-1]
        rd["state_machine"].state = AgentState.EXECUTING
        await svc._execute(rd, plan)
        return blocked, first, svc.engine.goals[-1]

    blocked, first, second = asyncio.run(go())
    assert blocked == (AgentState.BLOCKED, [], "blocked")  # engine never ran
    assert first.startswith("fix bug 12") and "use the v2 api" in first
    assert "skip tests dir" in first and "SECRETNET" not in first
    assert "<team_guidance>" in first and "from eng2" in first
    assert second == "fix bug 12"  # guidance is delivered once
