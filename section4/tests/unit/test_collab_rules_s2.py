"""Session 2 (parts 05-08, 11, 22-23): rules the pilot depends on, each with a test.

* simultaneous instructions have ONE documented resolution (collab.py docstring,
  docs/audit-and-events.md): every instruction is kept and ordered by `seq`; only
  the leader can change scope/permissions; a scope change voids open approvals
* a reason is required for scope AND permission changes
* consequential changes record before/after, approvals emit product events
* denied lifecycle actions leave `authz.denied` audit evidence
* the four agreed domain counters appear in the scrape after triggering them
"""

import asyncio
import json

import pytest
from harness_contracts.roles import Principal, Role
from sqlalchemy import text

from harness_orchestrator import collab, domain_metrics
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from tests.unit.test_collab_backend import P, audit_actions, lead_run, new_approval, q
from tests.unit.test_collab_backend import env as env  # noqa: F401


def principal(user, team="team-a", role=Role.ENGINEER):
    return Principal(userId=user, teamId=team, role=role)


@pytest.fixture(autouse=True)
def _fresh_counters(monkeypatch):
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    monkeypatch.delenv("HARNESS_ALLOW_SELF_APPROVAL", raising=False)
    domain_metrics.reset()


def audit_rows(f, action):
    rows = q(f, "SELECT detail FROM audit_log WHERE action = :a ORDER BY seq", a=action)
    return [json.loads(r.detail) for r in rows]


# ------------------------------------------------------ simultaneous instructions


def test_simultaneous_guidance_is_all_kept_in_seq_order_and_delivered_once(env):
    as_, f = env
    lead_run(as_)

    async def go():
        return await asyncio.gather(
            collab.redirect(f, principal("eng2"), "r1", "guidance", "use tabs", None, "a"),
            collab.redirect(f, principal("lead"), "r1", "guidance", "use spaces", None, "b"),
            collab.redirect(f, principal("eng2"), "r1", "guidance", "use tabs", None, "a"),
        )

    first, second, dup = asyncio.run(go())
    assert first["seq"] != second["seq"]
    # same requestId = one row; whichever retry lost the race is the duplicate
    assert first["seq"] == dup["seq"] and [first["duplicate"], dup["duplicate"]].count(True) == 1
    block, cursor = asyncio.run(collab.guidance_since(f, "r1", 0))
    # conflicting instructions are BOTH shown to the agent, numbered, in seq order
    assert block.index("use tabs") != -1 and block.index("use spaces") != -1
    order = sorted([(first["seq"], "use tabs"), (second["seq"], "use spaces")])
    assert block.index(order[0][1]) < block.index(order[1][1])
    assert cursor == max(first["seq"], second["seq"])
    assert domain_metrics.value("harness_event_delivery_total", "duplicate") == 1


def test_simultaneous_scope_changes_only_the_leader_counts(env):
    as_, f = env
    lead_run(as_)

    async def go():
        return await asyncio.gather(
            collab.redirect(f, principal("lead"), "r1", "scope_change", "narrow", "risk", None),
            collab.redirect(f, principal("eng2"), "r1", "scope_change", "widen", "hurry", None),
            return_exceptions=True,
        )

    ok, denied = asyncio.run(go())
    assert isinstance(ok, dict) and isinstance(denied, collab.CollabError)
    assert denied.code == "NOT_LEADER"
    kinds = [e["kind"] for e in as_("lead").get(f"{P}/r1/collab-history").json()["events"]]
    assert kinds.count("redirect.scope_change") == 1


def test_scope_change_racing_an_approval_decision_never_leaves_it_usable(env):
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)

    async def go():
        return await asyncio.gather(
            collab.decide_approval(f, principal("lead"), "r1", a["id"], True, a["digest"]),
            collab.redirect(f, principal("lead"), "r1", "scope_change", "narrow", "risk", None),
            return_exceptions=True,
        )

    asyncio.run(go())
    state = q(f, "SELECT state FROM high_impact_approvals WHERE id = :i", i=a["id"])[0].state
    assert state == "invalidated"  # either order ends voided: nothing can be claimed
    claim = as_("eng2").post(f"{P}/r1/approvals/{a['id']}/claim", json={"digest": a["digest"]})
    assert claim.status_code == 409


def test_two_simultaneous_leader_claims_yield_exactly_one_leader(env):
    as_, f = env
    as_("lead").post(f"{P}/r1/join")
    as_("eng2").post(f"{P}/r1/join")

    async def go():
        return await asyncio.gather(
            collab.claim_leader(f, principal("lead"), "r1"),
            collab.claim_leader(f, principal("eng2"), "r1"),
            return_exceptions=True,
        )

    results = asyncio.run(go())
    assert sum(isinstance(r, dict) for r in results) == 1
    leaders = q(f, "SELECT user_id FROM run_participants WHERE run_id='r1' AND role='leader'")
    assert len(leaders) == 1


# --------------------------------------------------------- reason is required


@pytest.mark.parametrize("kind", ["scope_change", "permission_change"])
@pytest.mark.parametrize("reason", [None, "", "   \n\t"])
def test_scope_and_permission_changes_need_a_reason(env, kind, reason):
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    body = {"kind": kind, "content": "do something wider"}
    if reason is not None:
        body["reason"] = reason
    res = as_("lead").post(f"{P}/r1/redirect", json=body)
    assert res.status_code == 422
    assert res.json()["detail"]["error"]["code"] == "REASON_REQUIRED"
    # a refused change changes nothing: no event, no audit row, approval untouched
    assert [e["kind"] for e in as_("lead").get(f"{P}/r1/collab-history").json()["events"]
            if e["kind"].startswith("redirect")] == []
    assert q(
        f, "SELECT state FROM high_impact_approvals WHERE id=:i", i=a["id"]
    )[0].state == "pending"


def test_guidance_needs_no_reason_and_no_confirmation(env):
    as_, _ = env
    lead_run(as_)
    res = as_("eng2").post(f"{P}/r1/redirect", json={"content": "prefer small commits"})
    assert res.status_code == 200 and res.json()["invalidatedApprovals"] == 0


# ---------------------------------------------- before/after values + product events


def test_approval_lifecycle_records_before_after_and_emits_events(env):
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    as_("lead").post(
        f"{P}/r1/approvals/{a['id']}/decision", json={"approve": True, "digest": a["digest"]}
    )
    as_("eng2").post(f"{P}/r1/approvals/{a['id']}/claim", json={"digest": a["digest"]})
    as_("eng2").post(f"{P}/r1/approvals/{a['id']}/complete", json={"ok": True, "outcome": "done"})

    approved = audit_rows(f, "approval.approved")[0]
    assert approved["before"] == {"state": "pending"}
    assert approved["after"] == {"state": "approved", "decidedBy": "lead"}
    assert approved["requester"] == "eng2" and approved["userId"] == "lead"
    claimed = audit_rows(f, "approval.claimed")[0]
    assert claimed["before"] == {"state": "approved"} and claimed["after"]["state"] == "executing"
    done = audit_rows(f, "approval.completed")[0]
    assert done["before"] == {"state": "executing"} and done["after"] == {"state": "completed"}
    requested = audit_rows(f, "approval.requested")[0]
    assert requested["before"] is None and requested["after"] == {"state": "pending"}

    events = as_("view").get(f"{P}/r1/collab-history").json()["events"]
    kinds = [e["kind"] for e in events if e["kind"].startswith("approval.")]
    assert kinds == [
        "approval.requested", "approval.approved", "approval.claimed", "approval.completed"
    ]
    # product events carry ids and states, never the diff or free-text summary
    for e in events:
        if e["kind"].startswith("approval."):
            assert "diff" not in e["payload"] and "summary" not in e["payload"]


def test_amend_and_scope_invalidation_record_what_changed(env):
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    amended = as_("eng2").put(
        f"{P}/r1/approvals/{a['id']}",
        json={**{"action": "delete", "summary": "s", "diff": "-y"}, "paths": ["src/other.py"]},
    ).json()
    row = audit_rows(f, "approval.amended")[0]
    assert row["before"]["digest"] == a["digest"] and row["after"]["digest"] == amended["digest"]
    as_("lead").post(
        f"{P}/r1/redirect", json={"kind": "scope_change", "content": "narrow", "reason": "risk"}
    )
    inv = audit_rows(f, "approval.invalidated")[0]
    assert inv["before"] == [{"id": a["id"], "state": "pending"}]
    assert inv["after"] == {"state": "invalidated"} and inv["why"].startswith("scope_change")
    scope = audit_rows(f, "redirect.scope_changed")[0]
    assert scope["reason"] == "risk" and scope["after"] == {"invalidatedApprovals": 1}


def test_leader_changes_record_before_and_after(env):
    as_, f = env
    lead_run(as_)
    assigned = audit_rows(f, "leader.assigned")[0]
    assert assigned["before"] == {"leader": None} and assigned["after"] == {"leader": "lead"}
    h = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"}).json()
    as_("eng2").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": True})
    accepted = audit_rows(f, "handoff.accepted")[0]
    assert accepted["before"]["leader"] == "lead" and accepted["after"] == {"leader": "eng2"}
    as_("boss").post(f"{P}/r1/join")
    as_("boss").post(f"{P}/r1/leader")
    took = audit_rows(f, "leader.takeover")[0]
    assert took["before"] == {"leader": "eng2"} and took["after"] == {"leader": "boss"}


# --------------------------------------- unauthorized lifecycle actions leave evidence


@pytest.mark.parametrize("verb", ["start", "pause", "resume", "stop"])
def test_viewer_lifecycle_attempt_is_denied_and_audited(env, verb):
    as_, f = env
    before = len(audit_actions(f))
    res = as_("view").post(f"/api/v1/orchestrator/runs/r1/{verb}")
    assert res.status_code == 403 and res.json()["detail"]["error"]["code"] == "ROLE_DENIED"
    added = q(f, "SELECT action, detail FROM audit_log ORDER BY seq")[before:]
    assert [r.action for r in added] == ["authz.denied"]
    detail = json.loads(added[0].detail)
    assert detail["reason"] == "ROLE_DENIED" and detail["userId"] == "view"
    assert detail["teamId"] == "team-a" and verb in detail["attempt"]


@pytest.mark.parametrize("verb", ["start", "pause", "resume", "stop"])
def test_foreign_team_lifecycle_attempt_is_a_404_and_audited(env, verb):
    as_, f = env
    before = len(audit_actions(f))
    res = as_("evil").post(f"/api/v1/orchestrator/runs/r1/{verb}")
    assert res.status_code == 404 and res.json()["detail"]["error"]["code"] == "NOT_FOUND"
    added = q(f, "SELECT action, detail FROM audit_log ORDER BY seq")[before:]
    assert [r.action for r in added] == ["authz.denied"]
    assert json.loads(added[0].detail)["teamId"] == "team-b"  # attempter, not the run's team


def test_pause_after_stop_is_a_409_not_a_500(env):
    as_, _ = env
    assert as_("lead").post("/api/v1/orchestrator/runs/r1/stop").status_code == 200
    res = as_("lead").post("/api/v1/orchestrator/runs/r1/pause")
    assert res.status_code == 409 and res.json()["detail"]["error"]["code"] == "RUN_TERMINAL"


def test_repeated_stop_audits_each_request_but_stops_once(env):
    as_, f = env
    for _ in range(3):
        assert as_("lead").post("/api/v1/orchestrator/runs/r1/stop").status_code == 200
    acts = audit_actions(f)
    assert acts.count("run.cancel_requested") == 3  # every request is evidence
    assert acts.count("run.stopped") == 1 and acts.count("run.cancel_confirmed") == 1


# ------------------------------------------------------------ metrics scrape


def test_agreed_domain_counters_appear_in_the_scrape_after_triggering_them(env, tmp_path):
    as_, f = env
    lead_run(as_)
    # harness_approval_denied_total{reason}: a decision on a stale digest
    a = new_approval(as_)
    stale = as_("lead").post(
        f"{P}/r1/approvals/{a['id']}/decision", json={"approve": True, "digest": "f" * 64}
    )
    assert stale.status_code == 409
    # harness_event_delivery_total{outcome}: an idempotent retry is a suppressed duplicate
    body = {"content": "hello", "requestId": "dup-1"}
    as_("eng2").post(f"{P}/r1/redirect", json=body)
    as_("eng2").post(f"{P}/r1/redirect", json=body)
    # harness_run_cancel_total{outcome}: stop reaches the (stub) engine and is confirmed
    assert as_("lead").post("/api/v1/orchestrator/runs/r1/stop").status_code == 200
    # harness_isolation_violation_total{kind}: a traversal id is refused by the workspace guard
    with pytest.raises(ValueError):
        DeepSeekHarnessWrapper(workspace_dir=str(tmp_path / "ws")).run_workspace("../escape")

    scrape = as_("lead").get("/api/v1/orchestrator/metrics").text
    for series in (
        'harness_approval_denied_total{reason="digest_mismatch"} 1',
        'harness_event_delivery_total{outcome="duplicate"} 1',
        'harness_run_cancel_total{outcome="requested"} 1',
        'harness_run_cancel_total{outcome="confirmed"} 1',
        'harness_isolation_violation_total{kind="bad_session_id"} 1',
    ):
        assert series in scrape, series
    for name in domain_metrics.METRICS:  # declared + typed, so alerts see them from boot
        assert f"# TYPE {name} counter" in scrape


# ------------------------------------------- race-safe decisions, approval timeout


def test_a_leader_demoted_mid_request_cannot_decide(env, monkeypatch):
    """T4b: the leadership check is part of the decision UPDATE itself, so a stale
    pre-check (leader demoted between check and write) still cannot approve."""
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)

    async def demote():
        async with f() as db:
            await db.execute(
                text("UPDATE run_participants SET role='participant' WHERE user_id='lead'")
            )
            await db.commit()

    async def stale_check_passes(*args, **kwargs):
        return None

    asyncio.run(demote())
    monkeypatch.setattr(collab, "_require_leader", stale_check_passes)
    res = as_("lead").post(
        f"{P}/r1/approvals/{a['id']}/decision", json={"approve": True, "digest": a["digest"]}
    )
    assert res.status_code == 403 and res.json()["detail"]["error"]["code"] == "NOT_LEADER"
    assert q(
        f, "SELECT state FROM high_impact_approvals WHERE id=:i", i=a["id"]
    )[0].state == "pending"
    assert "authz.denied" in audit_actions(f)
    assert domain_metrics.value("harness_approval_denied_total", "not_leader") == 1


def test_unanswered_approval_expires_stops_blocking_and_leaves_evidence(env):
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    assert asyncio.run(collab.pending_approval(f, "r1")) == a["id"]  # blocks while live

    async def age():
        async with f() as db:
            await db.execute(text("UPDATE high_impact_approvals SET expires_at = '2000-01-01'"))
            await db.commit()

    asyncio.run(age())
    assert asyncio.run(collab.pending_approval(f, "r1")) is None  # timeout releases the gate
    assert q(
        f, "SELECT state FROM high_impact_approvals WHERE id=:i", i=a["id"]
    )[0].state == "expired"
    row = audit_rows(f, "approval.expired")[0]
    assert row["before"] == {"state": "pending"} and row["after"] == {"state": "expired"}
    assert row["reason"] == "ttl" and row["userId"] == collab.EXPIRY_ACTOR
    kinds = [e["kind"] for e in as_("view").get(f"{P}/r1/collab-history").json()["events"]]
    assert "approval.expired" in kinds
    # and it can never be used afterwards
    body = {"approve": True, "digest": a["digest"]}
    assert as_("lead").post(f"{P}/r1/approvals/{a['id']}/decision", json=body).status_code == 409
    claim = as_("eng2").post(f"{P}/r1/approvals/{a['id']}/claim", json={"digest": a["digest"]})
    assert claim.status_code == 409
    assert asyncio.run(collab.expire_stale_approvals(f, "r1")) == []  # idempotent


# --------------------------------------------- attribution, secrets, handoff history


def test_forged_actor_fields_in_bodies_are_ignored(env):
    """T5: identity comes only from the authenticated principal, never from a body."""
    as_, f = env
    lead_run(as_)
    forged = {"userId": "lead", "actor": "lead", "requester": "lead", "decidedBy": "lead",
              "requesterUserId": "lead", "teamId": "team-b"}
    as_("eng2").post(f"{P}/r1/redirect", json={"content": "hello", **forged})
    ev = [e for e in as_("lead").get(f"{P}/r1/collab-history").json()["events"]
          if e["kind"] == "redirect.guidance"]
    assert ev[0]["actor"] == "eng2"
    a = as_("eng2").post(
        f"{P}/r1/approvals",
        json={"action": "delete", "paths": ["a.py"], "summary": "s", "diff": "", **forged},
    ).json()
    assert a["requester"] == "eng2"
    as_("lead").post(
        f"{P}/r1/approvals/{a['id']}/decision",
        json={"approve": True, "digest": a["digest"], **forged},
    )
    assert q(
        f, "SELECT decided_by FROM high_impact_approvals WHERE id=:i", i=a["id"]
    )[0].decided_by == "lead"
    row = audit_rows(f, "redirect.recorded")[0]
    assert row["userId"] == "eng2" and row["teamId"] == "team-a"


def test_secret_shaped_keys_never_reach_audit_or_events(env):
    from harness_orchestrator import collab as c

    as_, f = env
    lead_run(as_)

    async def go():
        async with f() as db:
            await c._audit(db, principal("lead"), "participant.joined", "r1",
                           {"apiKey": "sk-123", "password": "pw", "note": "kept"})
            await c._event(db, principal("lead"), "r1", "participant.joined",
                           {"token": "t-123", "note": "kept"}, None)
            await db.commit()

    asyncio.run(go())
    detail = audit_rows(f, "participant.joined")[-1]
    assert detail["note"] == "kept" and "apiKey" not in detail and "password" not in detail
    blob = json.dumps(as_("lead").get(f"{P}/r1/collab-history").json())
    assert "t-123" not in blob and "sk-123" not in blob


def test_history_before_a_handoff_survives_it_unchanged(env):
    as_, _ = env
    lead_run(as_)
    as_("eng2").post(f"{P}/r1/redirect", json={"content": "before the handoff"})
    before = as_("lead").get(f"{P}/r1/collab-history").json()["events"]
    h = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"}).json()
    as_("eng2").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": True})
    after = as_("eng2").get(f"{P}/r1/collab-history").json()["events"]
    assert after[: len(before)] == before  # nothing rewritten, only appended
    assert [e["kind"] for e in after[len(before):]] == ["handoff.requested", "handoff.accepted"]
