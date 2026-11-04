"""Part 42 (lane C): control leases, request queue, shared steering, versioned intent,
conflicts and forced re-review. Sqlite with the REAL migration 0043 plus the 0035 tables
(shared DDL from test_collab_backend), a fake clock for expiry, and the presence table
with its own minimal DDL (lane P owns the real one).
"""

import asyncio
import json
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

from harness_orchestrator import api as orch_api
from harness_orchestrator import collab, control
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.service import OrchestratorService
from tests.unit.test_collab_backend import DDL, PROPOSAL, USERS, Bus, audit_actions, q

MIGRATION = Path(__file__).resolve().parents[3] / "section1" / "migrations" / "0043_control.sql"
PRESENCE_DDL = (
    "CREATE TABLE presence_records (scope_kind VARCHAR(8) NOT NULL, scope_id VARCHAR(36) NOT NULL,"
    " team_id VARCHAR(36) NOT NULL, user_id VARCHAR(36) NOT NULL, device_id VARCHAR(64) NOT NULL,"
    " state VARCHAR(8) NOT NULL, last_seen TIMESTAMP NOT NULL, expires_at TIMESTAMP NOT NULL,"
    " PRIMARY KEY (scope_kind, scope_id, user_id, device_id))",
    "CREATE TABLE team_presence_settings (team_id VARCHAR(36) PRIMARY KEY,"
    " mode VARCHAR(8) NOT NULL,"
    " stale_seconds INTEGER NOT NULL DEFAULT 90, max_participants INTEGER NOT NULL DEFAULT 20,"
    " updated_by VARCHAR(36), updated_at TIMESTAMP)",
)
ALL_USERS = {**USERS, "eng3": ("team-a", Role.ENGINEER)}
C = "/api/v1/orchestrator/runs"
T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
RESERVED_CONTROL = {
    "control.requested", "control.acquired", "control.renewed", "control.released",
    "control.transferred", "control.revoked", "control.expired", "control.conflict_detected",
    "control.conflict_resolved", "control.instruction_queued", "control.instruction_rejected",
}


def migration_statements() -> list[str]:
    out = []
    for chunk in MIGRATION.read_text().split(";"):
        body = "\n".join(x for x in chunk.splitlines() if not x.strip().startswith("--")).strip()
        if body:
            out.append(body)
    return out


class Clock:
    def __init__(self):
        self.t = T0

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


def principal(user):
    team, role = ALL_USERS[user]
    return Principal(userId=user, teamId=team, role=role)


@pytest.fixture()
def ctl(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    monkeypatch.delenv("HARNESS_ALLOW_SELF_APPROVAL", raising=False)
    clock = Clock()
    monkeypatch.setattr(collab, "db_now", lambda: clock.t)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ctl.db")

    async def setup():
        async with engine.begin() as conn:
            for d in (*DDL, *PRESENCE_DDL, *migration_statements()):
                await conn.execute(text(d))
            await conn.execute(text("INSERT INTO projects (id, team_id) VALUES ('p', 'team-a')"))
            await conn.execute(
                text("INSERT INTO agent_runs (id, project_id) VALUES ('r1', 'p'), ('r2', 'p')")
            )
            for u, (t, role) in ALL_USERS.items():
                await conn.execute(
                    text("INSERT INTO team_members (id, team_id, user_id, role)"
                         " VALUES (:i, :t, :u, :r)"),
                    {"i": f"m-{u}", "t": t, "u": u, "r": role.value},
                )

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=Bus())
    monkeypatch.setattr(orch_api, "_service", service)
    monkeypatch.setattr(control, "_service", service)
    app = FastAPI()
    app.include_router(orch_router)
    app.include_router(control.router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: principal(who["u"])
    client = TestClient(app)

    def as_(user):
        who["u"] = user
        return client

    as_.clock = clock
    yield as_, factory
    asyncio.run(engine.dispose())


def run(coro):
    return asyncio.run(coro)


def sql(factory, stmt, **kw):
    async def go():
        async with factory() as db:
            await db.execute(text(stmt), kw)
            await db.commit()

    run(go())


def hold(as_, user="lead", **body):
    r = as_(user).post(f"{C}/r1/control/acquire", json=body or None)
    assert r.status_code == 200, r.text
    return r.json()


def join(as_, *users):
    for u in users:
        assert as_(u).post(f"{C}/r1/join").status_code == 200


def view(as_, user="lead"):
    r = as_(user).get(f"{C}/r1/control")
    assert r.status_code == 200, r.text
    return r.json()


def leaders(f):
    return [r.user_id for r in q(f, "SELECT user_id FROM run_participants WHERE role='leader'")]


def err(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["detail"]["error"]["code"] == code, resp.text
    return resp.json()["detail"]


def audit(f, action):
    return [json.loads(r.detail) for r in
            q(f, "SELECT detail FROM audit_log WHERE action = :a ORDER BY seq", a=action)]


def events(as_, run="r1", user="lead"):
    return as_(user).get(f"{C}/{run}/collab-history").json()["events"]


# ------------------------------------------------------------------- lease basics


def test_migration_loads_and_acquire_creates_one_timed_lease(ctl):
    as_, f = ctl
    out = hold(as_)
    assert out["holder"] == "lead" and not out["takeover"]
    assert leaders(f) == ["lead"]
    lease = q(f, "SELECT holder_user_id, version FROM control_leases WHERE run_id='r1'")
    assert [(x.holder_user_id, x.version) for x in lease] == [("lead", 1)]
    v = view(as_)
    assert v["holder"]["userId"] == "lead" and v["holder"]["expiresInSeconds"] == 900
    assert v["viewer"]["isHolder"] and not v["viewer"]["canAcquire"]
    assert "lead holds control until" in v["announcement"]
    assert "control.acquired" in audit_actions(f)
    assert any(e["kind"] == "control.acquired" for e in events(as_))
    # the holder acquiring again is an idempotent renew, never a second lease
    again = hold(as_)
    assert again["holder"] == "lead" and leaders(f) == ["lead"]


def test_view_for_a_non_holder_says_how_to_request(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    v = view(as_, "eng2")
    assert v["viewer"]["canRequest"] and not v["viewer"]["canAcquire"]
    assert v["how"]["request"].endswith("/runs/r1/control/request")
    assert view(as_, "view")["viewer"] == {
        "userId": "view", "isHolder": False, "canAcquire": False, "canRequest": False,
        "canSuggest": False, "queuePosition": None}


def test_second_acquire_is_refused_and_request_queues_fifo_idempotently(ctl):
    as_, f = ctl
    hold(as_)
    d = err(as_("eng2").post(f"{C}/r1/control/acquire"), 409, "LEASE_HELD")
    assert d["holder"] == "lead"
    r1 = as_("eng2").post(f"{C}/r1/control/request").json()
    assert r1["position"] == 1 and not r1["duplicate"]
    r2 = as_("eng2").post(f"{C}/r1/control/request").json()
    assert r2["duplicate"] and r2["requestId"] == r1["requestId"]
    as_.clock.advance(1)  # FIFO is by timestamp: keep the two requests apart
    assert as_("eng3").post(f"{C}/r1/control/request").json()["position"] == 2
    q_ = view(as_)["queue"]
    assert [x["userId"] for x in q_] == ["eng2", "eng3"]
    assert len(q(f, "SELECT id FROM control_requests")) == 2
    err(as_("lead").post(f"{C}/r1/control/request"), 409, "ALREADY_HOLDER")
    assert audit(f, "control.requested")[0]["after"] == {"state": "pending"}


def test_request_when_free_points_at_acquire_and_viewers_cannot_hold(ctl):
    as_, f = ctl
    err(as_("eng2").post(f"{C}/r1/control/request"), 409, "CONTROL_FREE")
    err(as_("view").post(f"{C}/r1/control/acquire"), 403, "ROLE_DENIED")  # guard: read-only role
    assert leaders(f) == []


def test_lease_expiry_vacates_the_leader_and_leader_paths_respect_it(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    a = as_("eng2").post(f"{C}/r1/approvals", json=PROPOSAL).json()
    as_.clock.advance(899)
    ok = as_("lead").post(f"{C}/r1/redirect", json={"kind": "scope_change", "content": "narrow",
                                                    "reason": "risk"})
    assert ok.status_code == 200  # still inside the lease; this scope change voided the approval
    assert astate(f, a["id"]) == "invalidated"
    b = as_("eng2").post(f"{C}/r1/approvals", json=PROPOSAL).json()
    as_.clock.advance(2)  # now past 900 s since acquire
    # every leader-only path refuses an expired holder: redirect, decision, handoff
    err(as_("lead").post(f"{C}/r1/redirect", json={"kind": "scope_change", "content": "x",
                                                   "reason": "r"}), 403, "NOT_LEADER")
    err(as_("lead").post(f"{C}/r1/approvals/{b['id']}/decision",
                         json={"approve": True, "digest": b["digest"]}), 403, "NOT_LEADER")
    err(as_("lead").post(f"{C}/r1/handoff", json={"toUserId": "eng2"}), 403, "NOT_LEADER")
    assert leaders(f) == []
    assert q(f, "SELECT 1 FROM control_leases") == []
    exp = audit(f, "control.expired")
    assert len(exp) == 1 and exp[0]["before"] == {"holder": "lead"}
    assert exp[0]["after"] == {"holder": None}
    assert len([e for e in events(as_) if e["kind"] == "control.expired"]) == 1
    assert view(as_)["holder"] is None and "Nobody" in view(as_)["announcement"]
    # the roster agrees, and another engineer can now take over
    assert as_("eng2").get(f"{C}/r1/participants").json()["leader"] is None
    assert hold(as_, "eng2")["holder"] == "eng2"


def test_expiry_cancels_a_pending_handoff(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    assert as_("lead").post(f"{C}/r1/handoff", json={"toUserId": "eng2"}).status_code == 200
    as_.clock.advance(901)
    view(as_)
    assert q(f, "SELECT state FROM run_handoffs")[0].state == "cancelled"


def test_renew_extends_only_for_the_live_holder(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    as_.clock.advance(600)
    out = as_("lead").post(f"{C}/r1/control/renew", json={"ttlSeconds": 1200}).json()
    assert out["holder"] == "lead"
    as_.clock.advance(1000)  # past the ORIGINAL expiry, inside the renewed one
    assert view(as_)["holder"]["userId"] == "lead"
    err(as_("eng2").post(f"{C}/r1/control/renew"), 403, "NOT_LEADER")
    assert q(f, "SELECT version FROM control_leases")[0].version == 2
    assert audit(f, "control.renewed")
    as_.clock.advance(300)  # lapsed
    err(as_("lead").post(f"{C}/r1/control/renew"), 403, "NOT_LEADER")


def test_ttl_is_bounded(ctl):
    as_, _ = ctl
    assert as_("lead").post(f"{C}/r1/control/acquire", json={"ttlSeconds": 5}).status_code == 422
    too_long = as_("lead").post(f"{C}/r1/control/acquire", json={"ttlSeconds": 99999})
    assert too_long.status_code == 422


def test_release_frees_control_and_the_queue_head_goes_first(ctl):
    as_, f = ctl
    hold(as_)
    as_("eng2").post(f"{C}/r1/control/request")
    as_.clock.advance(1)
    as_("eng3").post(f"{C}/r1/control/request")
    out = as_("lead").post(f"{C}/r1/control/release").json()
    assert out == {"holder": None, "nextInQueue": "eng2"}
    assert leaders(f) == [] and q(f, "SELECT 1 FROM control_leases") == []
    d = err(as_("eng3").post(f"{C}/r1/control/acquire"), 409, "QUEUE_ORDER")
    assert d["nextInQueue"] == "eng2"
    assert hold(as_, "eng2")["holder"] == "eng2"
    states = {r.user_id: r.state for r in q(f, "SELECT user_id, state FROM control_requests")}
    assert states == {"eng2": "granted", "eng3": "pending"}
    err(as_("eng3").post(f"{C}/r1/control/release"), 403, "NOT_LEADER")
    assert audit(f, "control.released")[0]["after"] == {"holder": None}


def test_transfer_is_immediate_and_atomic(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2", "view")
    err(as_("lead").post(f"{C}/r1/control/transfer", json={"toUserId": "view"}), 422,
        "BAD_RECIPIENT")
    err(as_("lead").post(f"{C}/r1/control/transfer", json={"toUserId": "nobody"}), 422,
        "BAD_RECIPIENT")
    err(as_("lead").post(f"{C}/r1/control/transfer", json={"toUserId": "lead"}), 422,
        "SELF_TRANSFER")
    as_.clock.advance(100)
    out = as_("lead").post(f"{C}/r1/control/transfer", json={"toUserId": "eng2"}).json()
    assert out["holder"] == "eng2"
    assert leaders(f) == ["eng2"]
    assert q(f, "SELECT holder_user_id FROM control_leases")[0].holder_user_id == "eng2"
    err(as_("lead").post(f"{C}/r1/control/transfer", json={"toUserId": "eng2"}), 403, "NOT_LEADER")
    t = audit(f, "control.transferred")[0]
    assert t["before"] == {"holder": "lead"} and t["after"] == {"holder": "eng2"}


def test_revoke_is_admin_only_needs_a_reason_and_frees_control(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "boss")
    err(as_("eng2").post(f"{C}/r1/control/revoke", json={"reason": "x"}), 403, "ROLE_DENIED")
    assert as_("boss").post(f"{C}/r1/control/revoke", json={"reason": ""}).status_code == 422
    as_("lead").post(f"{C}/r1/handoff", json={"toUserId": "boss"})
    out = as_("boss").post(f"{C}/r1/control/revoke", json={"reason": "holder is away"}).json()
    assert out == {"holder": None, "revokedFrom": "lead"}
    assert leaders(f) == [] and q(f, "SELECT 1 FROM control_leases") == []
    assert q(f, "SELECT state FROM run_handoffs")[0].state == "cancelled"
    rv = audit(f, "control.revoked")[0]
    assert rv["reason"] == "holder is away" and rv["from"] == "lead" and rv["userId"] == "boss"
    err(as_("boss").post(f"{C}/r1/control/revoke", json={"reason": "again"}), 409, "NO_HOLDER")


# ------------------------------------------------------- decide (requests + queue)


def test_holder_grants_declines_and_requester_cancels(ctl):
    as_, f = ctl
    hold(as_)
    r2 = as_("eng2").post(f"{C}/r1/control/request").json()["requestId"]
    r3 = as_("eng3").post(f"{C}/r1/control/request").json()["requestId"]
    err(as_("eng2").post(f"{C}/r1/control/decide", json={"requestId": r3, "decision": "grant"}),
        403, "NOT_LEADER")
    err(as_("eng2").post(f"{C}/r1/control/decide", json={"requestId": r3, "decision": "cancel"}),
        403, "NOT_REQUESTER")
    cancelled = as_("eng3").post(f"{C}/r1/control/decide",
                                 json={"requestId": r3, "decision": "cancel"})
    assert cancelled.json()["state"] == "cancelled"
    err(as_("lead").post(f"{C}/r1/control/decide", json={"requestId": r3, "decision": "grant"}),
        409, "NOT_PENDING")
    as_("eng3").post(f"{C}/r1/control/request")  # a cancelled request can be refiled
    err(as_("lead").post(f"{C}/r1/control/decide", json={"decision": "grant"}), 422, "BAD_DECISION")
    err(as_("lead").post(f"{C}/r1/control/decide", json={"requestId": r2, "decision": "accept"}),
        422, "BAD_DECISION")
    out = as_("lead").post(f"{C}/r1/control/decide", json={"requestId": r2, "decision": "grant"})
    assert out.json()["holder"] == "eng2" and leaders(f) == ["eng2"]
    # the old holder is now a plain participant: the new holder declines eng3
    r3b = [x for x in view(as_, "eng2")["queue"] if x["userId"] == "eng3"][0]["requestId"]
    dec = as_("eng2").post(f"{C}/r1/control/decide", json={"requestId": r3b, "decision": "decline"})
    assert dec.json()["state"] == "declined"
    after = [a["after"] for a in audit(f, "control.requested")]
    assert {"state": "declined"} in after and {"state": "cancelled"} in after


def test_requests_expire_and_can_be_refiled(ctl):
    as_, f = ctl
    hold(as_, ttlSeconds=3600)
    rid = as_("eng2").post(f"{C}/r1/control/request").json()["requestId"]
    as_.clock.advance(601)
    assert view(as_)["queue"] == []
    err(as_("lead").post(f"{C}/r1/control/decide", json={"requestId": rid, "decision": "grant"}),
        409, "NOT_PENDING")
    assert as_("eng2").post(f"{C}/r1/control/request").json()["position"] == 1


# ---------------------------------------------------------------------- disconnect


def presence(f, user, expires_in, team="team-a"):
    sql(f, "INSERT INTO presence_records VALUES ('run','r1',:t,:u,'d1','online',:ls,:x)",
        t=team, u=user, ls=T0, x=T0 + timedelta(seconds=expires_in))


def test_disconnected_holder_can_be_taken_over_only_after_the_grace(ctl):
    as_, f = ctl
    hold(as_, ttlSeconds=3600)
    join(as_, "eng2")
    presence(f, "lead", 30)  # heartbeat that lapses at +30 s
    as_.clock.advance(20)
    assert view(as_)["holder"]["connected"] is True
    err(as_("eng2").post(f"{C}/r1/control/acquire"), 409, "LEASE_HELD")
    as_.clock.advance(30)  # presence lapsed, but the 60 s grace since renewal has not
    assert view(as_)["holder"]["connected"] is False and not view(as_)["holder"]["takeable"]
    err(as_("eng2").post(f"{C}/r1/control/acquire"), 409, "LEASE_HELD")
    as_.clock.advance(20)
    h = view(as_, "eng2")
    assert h["holder"]["takeable"] and h["viewer"]["canAcquire"]
    out = hold(as_, "eng2")
    assert out["takeover"] and leaders(f) == ["eng2"]
    ev = audit(f, "control.acquired")[-1]
    assert ev["takeover"] == "holder_disconnected" and ev["before"] == {"holder": "lead"}


def test_a_renewing_or_present_holder_is_never_taken_over(ctl):
    as_, f = ctl
    hold(as_, ttlSeconds=3600)
    presence(f, "lead", 600)
    as_.clock.advance(300)
    err(as_("eng2").post(f"{C}/r1/control/acquire"), 409, "LEASE_HELD")
    sql(f, "DELETE FROM presence_records")
    as_("lead").post(f"{C}/r1/control/renew")  # renewal resets the grace
    as_.clock.advance(30)
    err(as_("eng2").post(f"{C}/r1/control/acquire"), 409, "LEASE_HELD")


def test_unknown_or_switched_off_presence_is_not_disconnect(ctl):
    as_, f = ctl
    hold(as_, ttlSeconds=3600)
    as_.clock.advance(300)  # no presence row at all, presence mode unset -> disconnected
    assert view(as_)["holder"]["connected"] is False
    sql(f, "INSERT INTO team_presence_settings (team_id, mode, updated_at)"
        " VALUES ('team-a', 'off', :n)", n=T0)
    assert view(as_)["holder"]["connected"] is None
    err(as_("eng2").post(f"{C}/r1/control/acquire"), 409, "LEASE_HELD")
    sql(f, "DROP TABLE presence_records")  # lane P not installed
    assert view(as_)["holder"]["connected"] is None
    err(as_("eng2").post(f"{C}/r1/control/acquire"), 409, "LEASE_HELD")


# --------------------------------------------------- legacy paths keep the lease


def test_claim_handoff_and_takeover_paths_create_and_move_the_lease(ctl):
    as_, f = ctl
    assert as_("lead").post(f"{C}/r1/leader").status_code == 200
    holder = lambda: [r.holder_user_id for r in q(f, "SELECT holder_user_id FROM control_leases")]  # noqa: E731
    assert holder() == ["lead"]
    join(as_, "eng2", "boss")
    h = as_("lead").post(f"{C}/r1/handoff", json={"toUserId": "eng2"}).json()["handoffId"]
    assert holder() == ["lead"]  # proposer keeps control until accepted
    assert as_("eng2").post(f"{C}/r1/handoff/{h}", json={"accept": True}).status_code == 200
    assert holder() == ["eng2"] and leaders(f) == ["eng2"]
    assert as_("boss").post(f"{C}/r1/leader").status_code == 200  # admin takeover
    assert holder() == ["boss"] and leaders(f) == ["boss"]
    as_.clock.advance(901)  # a claim_leader-made lease expires like any other
    assert as_("lead").get(f"{C}/r1/participants").json()["leader"] is None


def test_a_pre_0043_leader_is_untimed_until_it_acquires(ctl):
    as_, f = ctl
    as_("lead").post(f"{C}/r1/leader")
    sql(f, "DELETE FROM control_leases")  # as if the leader predates the migration
    as_.clock.advance(100000)
    v = view(as_)
    assert v["holder"]["userId"] == "lead" and v["holder"]["untimed"]
    assert as_("lead").post(f"{C}/r1/redirect", json={"kind": "scope_change", "content": "c",
                                                      "reason": "r"}).status_code == 200
    hold(as_)  # acquiring turns it into a timed lease
    assert len(q(f, "SELECT 1 FROM control_leases")) == 1


# -------------------------------------------------------------------- shared steering


def test_non_holder_guidance_is_queued_then_accepted_into_the_agent_stream(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    r = as_("eng2").post(f"{C}/r1/redirect", json={"content": "use tabs", "requestId": "g1"})
    body = r.json()
    assert body["queued"] and body["seq"] is None
    assert body["takesEffect"] == "after_acceptance"  # W23
    again = as_("eng2").post(f"{C}/r1/redirect", json={"content": "use tabs", "requestId": "g1"})
    assert again.json()["duplicate"] and again.json()["instructionId"] == body["instructionId"]
    # not delivered to the agent, not in history as guidance
    assert run(collab.guidance_since(f, "r1", 0))[0] == ""
    assert not [e for e in events(as_) if e["kind"] == "redirect.guidance"]
    v = view(as_)
    assert v["queuedInstructions"][0]["content"] == "use tabs"
    iid = body["instructionId"]
    err(as_("eng2").post(f"{C}/r1/control/decide", json={"instructionId": iid,
                                                         "decision": "accept"}), 403, "NOT_LEADER")
    out = as_("lead").post(f"{C}/r1/control/decide", json={"instructionId": iid,
                                                           "decision": "accept"}).json()
    assert out["state"] == "accepted" and out["seq"]
    assert out["takesEffect"] == "next_start"  # r1 has not started: its first start reads it
    block, cursor = run(collab.guidance_since(f, "r1", 0))
    assert "use tabs" in block and cursor == out["seq"]
    err(as_("lead").post(f"{C}/r1/control/decide", json={"instructionId": iid,
                                                         "decision": "reject"}), 409, "NOT_PENDING")
    assert view(as_)["queuedInstructions"] == []
    assert audit(f, "control.instruction_queued")
    assert audit(f, "redirect.recorded")[-1]["suggestedBy"] == "eng2"


def test_holder_can_reject_a_suggestion_with_a_reason(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    sug = as_("eng2").post(f"{C}/r1/redirect", json={"content": "rewrite all"})
    iid = sug.json()["instructionId"]
    out = as_("lead").post(f"{C}/r1/control/decide", json={
        "instructionId": iid, "decision": "reject", "reason": "out of scope"}).json()
    assert out["state"] == "rejected"
    rej = audit(f, "control.instruction_rejected")[0]
    assert rej["reason"] == "out of scope" and rej["author"] == "eng2"
    assert run(collab.guidance_since(f, "r1", 0))[0] == ""


def test_holder_guidance_and_leaderless_guidance_stay_direct(ctl):
    as_, f = ctl
    join(as_, "eng2")
    assert as_("eng2").post(f"{C}/r1/redirect", json={"content": "no holder"}).json()["seq"]
    hold(as_)
    assert as_("lead").post(f"{C}/r1/redirect", json={"content": "mine"}).json()["seq"]
    as_.clock.advance(901)  # lease lapsed -> nobody to queue for
    assert as_("eng2").post(f"{C}/r1/redirect", json={"content": "later"}).json()["seq"]


def test_suggestions_expire_and_the_queue_is_bounded(ctl, monkeypatch):
    as_, f = ctl
    hold(as_, ttlSeconds=3600)
    join(as_, "eng2")
    iid = as_("eng2").post(f"{C}/r1/redirect", json={"content": "old"}).json()["instructionId"]
    as_.clock.advance(1800)
    as_("lead").post(f"{C}/r1/control/renew", json={"ttlSeconds": 3600})
    as_.clock.advance(1801)  # suggestion is now older than its 1 h life, the lease is not
    assert view(as_)["queuedInstructions"] == []
    err(as_("lead").post(f"{C}/r1/control/decide", json={"instructionId": iid,
                                                         "decision": "accept"}), 409, "NOT_PENDING")
    monkeypatch.setattr(control, "MAX_QUEUED_INSTRUCTIONS", 2)
    for i in range(2):
        as_("eng2").post(f"{C}/r1/redirect", json={"content": f"s{i}"})
    err(as_("eng2").post(f"{C}/r1/redirect", json={"content": "s3"}), 409, "QUEUE_FULL")


# ------------------------------------------------------------------------- intent


def put(as_, user, expected, run_id="r1", **fields):
    return as_(user).put(f"{C}/{run_id}/intent", json={"expectedVersion": expected, **fields})


def test_intent_versions_apply_in_order_and_stale_edits_are_rejected(ctl):
    as_, f = ctl
    hold(as_)
    got = as_("lead").get(f"{C}/r1/intent").json()
    assert got["intent"]["version"] == 0 and got["intent"]["goal"] == ""
    r = put(as_, "lead", 0, goal="ship it")
    assert r.status_code == 200 and r.json()["version"] == 1
    # an edit based on v0 of a field nobody touched is still not applied silently
    stale = put(as_, "lead", 0, scopePaths=["src", "docs", "src"])
    d = err(stale, 409, "STALE_INTENT_VERSION")
    assert d["currentVersion"] == 1 and d["current"]["goal"] == "ship it"
    assert d["conflictFields"] == []
    assert not q(f, "SELECT 1 FROM control_conflicts")  # disjoint: rebase, no conflict record
    r = put(as_, "lead", 1, scopePaths=["src", "docs", "src"],
            permissions=["net.egress", "fs.write"])
    assert r.json()["version"] == 2 and r.json()["intent"]["scopePaths"] == ["docs", "src"]
    assert put(as_, "lead", 2, goal="ship it").json()["unchanged"]  # no-op, no new version
    assert put(as_, "lead", 2, goal="v3").json()["version"] == 3
    assert err(put(as_, "lead", 9, goal="x"), 422, "BAD_VERSION")
    hist = as_("lead").get(f"{C}/r1/intent").json()["history"]
    assert [h["version"] for h in hist] == [3, 2, 1] and hist[0]["changed"] == ["goal"]
    upd = audit(f, "intent.updated")
    assert upd[-1]["before"] == {"goal": "ship it"} and upd[-1]["after"] == {"goal": "v3"}
    # an applied goal/scope edit reaches the agent as scope guidance
    block, _ = run(collab.guidance_since(f, "r1", 0))
    assert "Run intent v1" in block and "Run intent v3" in block and "goal: v3" in block


def test_intent_validation_and_roles(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2", "view")
    assert put(as_, "lead", 0).status_code == 422  # nothing to change
    err(put(as_, "lead", 0, scopePaths=["../etc"]), 422, "BAD_PATHS")
    err(put(as_, "lead", 0, permissions=["Bad Token"]), 422, "BAD_PERMISSIONS")
    assert put(as_, "lead", 0, scopePaths=[]).json()["unchanged"]
    assert put(as_, "view", 0, goal="x").status_code == 403  # read-only role
    # a never-joined engineer is not a participant
    err(put(as_, "eng3", 0, goal="x"), 403, "NOT_PARTICIPANT")
    assert not q(f, "SELECT 1 FROM run_intent_versions")


def test_intent_request_id_makes_retries_idempotent(ctl):
    as_, f = ctl
    hold(as_)
    a = put(as_, "lead", 0, goal="g", requestId="k1").json()
    b = put(as_, "lead", 0, goal="g", requestId="k1").json()
    assert a["version"] == b["version"] == 1 and b["duplicate"]
    assert len(q(f, "SELECT 1 FROM run_intent_versions")) == 1


def test_non_holder_edit_is_queued_as_a_proposal_for_the_holder(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    put(as_, "lead", 0, goal="base")
    r = put(as_, "eng2", 1, goal="eng2 wants this")
    assert r.status_code == 202 and r.json()["queued"]
    assert as_("lead").get(f"{C}/r1/intent").json()["intent"]["goal"] == "base"
    cv = as_("lead").get(f"{C}/r1/intent/conflicts").json()
    prop = cv["conflicts"][0]
    assert prop["kind"] == "proposal" and prop["fields"][0]["current"] == "base"
    assert prop["fields"][0]["proposed"] == "eng2 wants this"
    assert not prop["fields"][0]["conflicting"]
    assert audit(f, "control.instruction_queued")
    err(as_("eng2").post(f"{C}/r1/intent/conflicts/{prop['id']}/resolve",
                         json={"resolution": "take_proposed", "expectedVersion": 1}), 403,
        "NOT_LEADER")
    out = as_("lead").post(f"{C}/r1/intent/conflicts/{prop['id']}/resolve",
                           json={"resolution": "take_proposed", "expectedVersion": 1}).json()
    assert out["version"] == 2 and out["intent"]["goal"] == "eng2 wants this"
    assert as_("lead").get(f"{C}/r1/intent/conflicts").json()["conflicts"] == []


def test_conflicting_stale_edit_creates_a_conflict_with_the_current_approved_state(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    put(as_, "lead", 0, goal="A", permissions=["fs.write"])
    put(as_, "lead", 1, goal="C")  # v2: lead moved on while eng2 was editing v1
    d = err(put(as_, "eng2", 1, goal="B", permissions=["fs.write", "net.egress"]), 409,
            "INTENT_CONFLICT")
    assert d["conflictFields"] == ["goal"] and d["currentVersion"] == 2 and d["conflictId"]
    det = audit(f, "control.conflict_detected")
    assert len(det) == 1 and det[0]["fields"] == ["goal"]
    assert len(audit(f, "authz.denied")) == 0  # the explicit audit row is the only one
    cv = as_("lead").get(f"{C}/r1/intent/conflicts").json()
    assert cv["currentApproved"]["version"] == 2 and cv["currentApproved"]["goal"] == "C"
    c = cv["conflicts"][0]
    by = {x["field"]: x for x in c["fields"]}
    assert by["goal"] == {"field": "goal", "base": "A", "current": "C", "proposed": "B",
                          "conflicting": True}
    assert by["permissions"]["conflicting"] is False and c["stale"]
    assert as_("eng2").get(f"{C}/r1/intent/conflicts").status_code == 200  # visible to the team
    kinds = [e["kind"] for e in events(as_) if "conflict" in e["kind"]]
    assert kinds == ["control.conflict_detected"]


def approved(as_, f):
    a = as_("eng2").post(f"{C}/r1/approvals", json=PROPOSAL).json()
    r = as_("lead").post(f"{C}/r1/approvals/{a['id']}/decision",
                         json={"approve": True, "digest": a["digest"]})
    assert r.status_code == 200, r.text
    return a


def astate(f, aid):
    return q(f, "SELECT state FROM high_impact_approvals WHERE id=:i", i=aid)[0].state


def conflict(as_):
    put(as_, "lead", 0, goal="A")
    put(as_, "lead", 1, goal="C")
    return err(put(as_, "eng2", 1, goal="B"), 409, "INTENT_CONFLICT")["conflictId"]


def test_resolution_that_changes_state_forces_renewed_review(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    cid = conflict(as_)
    a = approved(as_, f)
    cv = as_("lead").get(f"{C}/r1/intent/conflicts").json()
    assert [x["id"] for x in cv["openApprovals"]] == [a["id"]] and "voids" in cv["note"]
    # resolving against a version the resolver did not review is refused
    err(as_("lead").post(f"{C}/r1/intent/conflicts/{cid}/resolve",
                         json={"resolution": "take_proposed", "expectedVersion": 1}), 409,
        "STALE_INTENT_VERSION")
    out = as_("lead").post(f"{C}/r1/intent/conflicts/{cid}/resolve",
                           json={"resolution": "take_proposed", "expectedVersion": 2}).json()
    assert out["version"] == 3 and out["invalidatedApprovals"] == 1 and out["reviewRequired"]
    assert astate(f, a["id"]) == "invalidated"
    # the old approval can no longer be claimed
    err(as_("eng2").post(f"{C}/r1/approvals/{a['id']}/claim", json={"digest": a["digest"]}), 409,
        "NOT_APPROVED")
    res = audit(f, "control.conflict_resolved")[0]
    assert res["resolution"] == "take_proposed" and res["before"] == {"goal": "C"}
    assert res["after"] == {"goal": "B"} and res["invalidatedApprovals"] == 1
    err(as_("lead").post(f"{C}/r1/intent/conflicts/{cid}/resolve",
                         json={"resolution": "keep_current", "expectedVersion": 3}), 409,
        "ALREADY_RESOLVED")
    assert as_("lead").get(f"{C}/r1/intent/conflicts?state=resolved").json()["conflicts"][0][
        "resolution"] == "take_proposed"


def test_keep_current_changes_nothing_and_leaves_approvals_alone(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    cid = conflict(as_)
    a = approved(as_, f)
    out = as_("lead").post(f"{C}/r1/intent/conflicts/{cid}/resolve",
                           json={"resolution": "keep_current", "expectedVersion": 2}).json()
    assert out["version"] == 2 and out["invalidatedApprovals"] == 0 and not out["reviewRequired"]
    assert astate(f, a["id"]) == "approved"
    assert audit(f, "control.conflict_resolved")[0]["resolution"] == "keep_current"


def test_merge_resolution_writes_the_merged_values(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    cid = conflict(as_)
    a = approved(as_, f)
    err(as_("lead").post(f"{C}/r1/intent/conflicts/{cid}/resolve",
                         json={"resolution": "merge", "expectedVersion": 2}), 422, "EMPTY_EDIT")
    out = as_("lead").post(f"{C}/r1/intent/conflicts/{cid}/resolve", json={
        "resolution": "merge", "expectedVersion": 2,
        "merged": {"goal": "B and C", "scopePaths": ["src"]}}).json()
    assert out["intent"]["goal"] == "B and C" and out["intent"]["scopePaths"] == ["src"]
    assert astate(f, a["id"]) == "invalidated"


def test_direct_holder_edits_void_approvals_only_for_scope_and_permissions(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    a = approved(as_, f)
    assert put(as_, "lead", 0, goal="reword").json()["invalidatedApprovals"] == 0
    assert astate(f, a["id"]) == "approved"
    out = put(as_, "lead", 1, scopePaths=["src"]).json()
    assert out["invalidatedApprovals"] == 1 and out["reviewRequired"]
    assert astate(f, a["id"]) == "invalidated"
    b = approved(as_, f)
    put(as_, "lead", 2, permissions=["fs.delete"])
    assert astate(f, b["id"]) == "invalidated"


def test_only_holder_or_admin_resolves_and_foreign_conflicts_are_404(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2", "boss")
    cid = conflict(as_)
    body = {"resolution": "keep_current", "expectedVersion": 2}
    err(as_("eng2").post(f"{C}/r1/intent/conflicts/{cid}/resolve", json=body), 403, "NOT_LEADER")
    err(as_("lead").post(f"{C}/r1/intent/conflicts/nope/resolve", json=body), 404, "NOT_FOUND")
    sql(f, "INSERT INTO agent_runs (id, project_id) VALUES ('r3', 'p')")
    sql(f, "INSERT INTO run_participants (run_id, team_id, user_id, role) VALUES"
           " ('r3', 'team-a', 'boss', 'participant')")
    err(as_("boss").post(f"{C}/r3/intent/conflicts/{cid}/resolve", json=body), 404, "NOT_FOUND")
    assert as_("boss").post(f"{C}/r1/intent/conflicts/{cid}/resolve", json=body).status_code == 200


# ----------------------------------------------------------------------- races


def test_two_simultaneous_acquires_yield_exactly_one_holder(ctl):
    as_, f = ctl
    join(as_, "eng2", "eng3")

    async def go():
        return await asyncio.gather(
            control.acquire(f, principal("eng2"), "r1", None),
            control.acquire(f, principal("eng3"), "r1", None),
            control.acquire(f, principal("lead"), "r1", None),
            return_exceptions=True,
        )

    results = run(go())
    assert sum(isinstance(r, dict) for r in results) == 1, results
    assert all(isinstance(r, collab.CollabError) for r in results if not isinstance(r, dict))
    assert len(leaders(f)) == 1
    lease = q(f, "SELECT holder_user_id FROM control_leases")
    assert [x.holder_user_id for x in lease] == leaders(f)


def test_renew_racing_expiry_never_leaves_two_holders_or_a_zombie(ctl):
    as_, f = ctl
    hold(as_)
    as_.clock.advance(901)  # the moment of truth: renew and a takeover arrive together

    async def go():
        return await asyncio.gather(
            control.renew(f, principal("lead"), "r1", None),
            control.acquire(f, principal("eng2"), "r1", None),
            return_exceptions=True,
        )

    renewed, taken = run(go())
    assert isinstance(renewed, collab.CollabError) and renewed.code == "NOT_LEADER"
    assert isinstance(taken, dict) and leaders(f) == ["eng2"]
    assert [x.holder_user_id for x in q(f, "SELECT holder_user_id FROM control_leases")] == ["eng2"]
    assert len(audit(f, "control.expired")) == 1


def test_transfer_racing_release_keeps_at_most_one_holder(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")

    async def go():
        return await asyncio.gather(
            control.transfer(f, principal("lead"), "r1", "eng2"),
            control.release(f, principal("lead"), "r1"),
            return_exceptions=True,
        )

    run(go())
    ls = leaders(f)
    assert len(ls) <= 1
    assert [x.holder_user_id for x in q(f, "SELECT holder_user_id FROM control_leases")] == ls


def test_simultaneous_intent_edits_one_wins_the_other_is_stale(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    put(as_, "lead", 0, goal="base")

    async def go():
        return await asyncio.gather(
            control.put_intent(f, principal("lead"), "r1", 1, {"goal": "L"}, None),
            control.put_intent(f, principal("lead"), "r1", 1, {"goal": "M"}, None),
            return_exceptions=True,
        )

    results = run(go())
    wins = [r for r in results if isinstance(r, dict)]
    assert len(wins) == 1, results
    loser = next(r for r in results if not isinstance(r, dict))
    assert isinstance(loser, collab.CollabError) and loser.code in (
        "STALE_INTENT_VERSION", "INTENT_CONFLICT")
    versions = q(f, "SELECT version FROM run_intent_versions ORDER BY version")
    assert [r.version for r in versions] == [1, 2]


def test_scope_edit_racing_an_approval_decision_never_leaves_it_usable(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    a = as_("eng2").post(f"{C}/r1/approvals", json=PROPOSAL).json()

    async def go():
        return await asyncio.gather(
            collab.decide_approval(f, principal("lead"), "r1", a["id"], True, a["digest"]),
            control.put_intent(f, principal("lead"), "r1", 0, {"scopePaths": ["src"]}, None),
            return_exceptions=True,
        )

    run(go())
    assert astate(f, a["id"]) == "invalidated"


# ---------------------------------------------------------- auth, audit, hooks


ROUTES = [
    ("get", "/control", None), ("post", "/control/request", None),
    ("post", "/control/acquire", None), ("post", "/control/renew", None),
    ("post", "/control/release", None),
    ("post", "/control/transfer", {"toUserId": "eng2"}),
    ("post", "/control/revoke", {"reason": "x"}),
    ("post", "/control/decide", {"requestId": "x", "decision": "grant"}),
    ("get", "/intent", None), ("put", "/intent", {"expectedVersion": 0, "goal": "x"}),
    ("get", "/intent/conflicts", None),
    ("post", "/intent/conflicts/c1/resolve", {"resolution": "keep_current", "expectedVersion": 0}),
]


def test_every_control_route_is_pinned_and_foreign_or_unknown_runs_are_404(ctl):
    as_, f = ctl
    assert len(control.router.routes) == len(ROUTES)  # a new route must be added to ROUTES
    codes = set()
    for method, path, body in ROUTES:
        for who, run_id in (("evil", "r1"), ("lead", "nope")):
            r = getattr(as_(who), method)(f"{C}/{run_id}{path}", **({"json": body} if body else {}))
            assert r.status_code == 404, (method, path, who, r.text)
            codes.add(json.dumps(r.json(), sort_keys=True))
    assert len(codes) == 1  # same envelope for foreign and unknown: no existence oracle
    assert not q(f, "SELECT 1 FROM control_leases")


def test_viewers_cannot_use_write_routes_and_the_pilot_gate_applies(ctl, monkeypatch):
    as_, f = ctl
    for method, path, body in ROUTES:
        if method == "get":
            continue
        r = getattr(as_("view"), method)(f"{C}/r1{path}", **({"json": body} if body else {}))
        assert r.status_code == 403, (path, r.text)
    assert as_("view").get(f"{C}/r1/control").status_code == 200
    monkeypatch.setenv("HARNESS_PILOT_TEAMS", "other-team")
    err(as_("lead").get(f"{C}/r1/control"), 404, "PILOT_NOT_ENABLED")
    err(as_("lead").post(f"{C}/r1/control/acquire"), 404, "PILOT_NOT_ENABLED")


def test_refusals_leave_denial_audit_rows(ctl):
    as_, f = ctl
    hold(as_)
    as_("eng2").post(f"{C}/r1/control/acquire")
    rows = audit(f, "authz.denied")
    assert any(r["reason"] == "LEASE_HELD" for r in rows)


def test_client_supplied_identity_is_ignored(ctl):
    as_, f = ctl
    join(as_, "eng2")
    r = as_("eng2").post(f"{C}/r1/control/acquire", json={"userId": "lead", "holder": "lead"})
    assert r.status_code == 200 and leaders(f) == ["eng2"]


def test_every_control_audit_action_is_a_reserved_name_and_pairs_with_an_event(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2", "boss")
    as_("eng2").post(f"{C}/r1/control/request")
    iid = as_("eng2").post(f"{C}/r1/redirect", json={"content": "s"}).json()["instructionId"]
    as_("lead").post(f"{C}/r1/control/decide", json={"instructionId": iid, "decision": "reject"})
    as_("lead").post(f"{C}/r1/control/renew")
    as_("lead").post(f"{C}/r1/control/transfer", json={"toUserId": "eng2"})
    as_("boss").post(f"{C}/r1/control/revoke", json={"reason": "r"})
    hold(as_, "boss")
    as_("boss").post(f"{C}/r1/control/release")
    hold(as_, "eng2")
    as_.clock.advance(901)
    view(as_)
    names = {a for a in audit_actions(f) if a.startswith("control.")}
    # conflict events are exercised in the intent tests; everything else fired here
    assert names == RESERVED_CONTROL - {"control.conflict_detected", "control.conflict_resolved"}
    assert names <= RESERVED_CONTROL
    kinds = {e["kind"] for e in events(as_)}
    assert names <= kinds


def test_join_respects_lane_p_capacity_and_degrades_when_absent(ctl, monkeypatch):
    as_, f = ctl
    mod = types.ModuleType("harness_orchestrator.participation")
    calls = []

    async def capacity_ok(db, run_id, team_id, user_id):
        calls.append((run_id, team_id, user_id))
        return user_id != "eng3"

    mod.capacity_ok = capacity_ok
    monkeypatch.setitem(sys.modules, "harness_orchestrator.participation", mod)
    assert as_("eng2").post(f"{C}/r1/join").status_code == 200
    err(as_("eng3").post(f"{C}/r1/join"), 409, "SESSION_FULL")
    err(as_("eng3").post(f"{C}/r1/control/acquire"), 409, "SESSION_FULL")
    assert as_("eng2").post(f"{C}/r1/join").status_code == 200  # existing member: no re-check
    assert calls == [("r1", "team-a", "eng2"), ("r1", "team-a", "eng3"), ("r1", "team-a", "eng3")]
    assert not q(f, "SELECT 1 FROM run_participants WHERE user_id='eng3'")
    monkeypatch.setitem(sys.modules, "harness_orchestrator.participation", None)  # not importable
    assert as_("eng3").post(f"{C}/r1/join").status_code == 200


def test_an_expired_holder_cannot_claim_or_decide_approvals(ctl):
    as_, f = ctl
    hold(as_)
    join(as_, "eng2")
    a = approved(as_, f)  # eng2 requested, lead approved
    as_.clock.advance(901)
    # claim/execute is requester-or-leader: the lapsed ex-leader is neither
    err(as_("lead").post(f"{C}/r1/approvals/{a['id']}/claim", json={"digest": a["digest"]}),
        403, "NOT_LEADER")
    assert as_("eng2").post(f"{C}/r1/approvals/{a['id']}/claim",
                            json={"digest": a["digest"]}).status_code == 200


# ------------------------------------------------------------- W23 steering timing

_TAKES_EFFECT = {
    "idle": "next_start", "planning": "next_start", "awaiting_plan_approval": "next_start",
    "paused": "next_start", "blocked": "next_start",  # resume reads it (service._execute)
    "executing": "next_execution",  # the call in flight read its guidance when it started
    "awaiting_done_signal": "next_execution", "reviewing": "next_execution",
    "pr_opened": "next_execution",
    "done": "never",
}


def test_every_state_has_a_steering_timing():
    from harness_orchestrator.state_machine import AgentState

    assert set(_TAKES_EFFECT) == {s.value for s in AgentState}


@pytest.mark.parametrize("state", sorted(_TAKES_EFFECT))
@pytest.mark.parametrize("queued", [True, False])
def test_takes_effect_every_state_and_queue(state, queued):
    expected = "after_acceptance" if queued else _TAKES_EFFECT[state]
    assert collab.takes_effect(state, queued) == expected


def test_direct_guidance_says_when_and_permission_changes_do_not(ctl):
    as_, _ = ctl
    hold(as_)
    g = as_("lead").post(f"{C}/r1/redirect", json={"content": "use tabs"}).json()
    assert g["takesEffect"] == "next_start" and not g.get("queued")
    perm = as_("lead").post(f"{C}/r1/redirect", json={"kind": "permission_change",
                                                     "content": "allow net", "reason": "r"})
    assert perm.status_code == 200, perm.text
    assert "takesEffect" not in perm.json()  # not injected into the engine prompt
