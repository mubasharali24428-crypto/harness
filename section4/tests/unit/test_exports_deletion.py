"""Part 50.007 (lane SE): data deletion requests. A person asks for their own authored data to be
deleted, an admin decides, approval executes the documented list and states the retained audit
exceptions. Sqlite with the REAL migrations. Behaviour: docs/search-and-export.md."""

import json
from pathlib import Path

import pytest

from harness_orchestrator import exports

from . import test_search_support as support
from .test_search_support import P, err

env = support.env  # the shared fixture


def request_deletion(env, user, **body):
    r = env.as_(user).post(f"{P}/deletion-requests", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def approve(env, rid, user="boss", expect=200):
    r = env.as_(user).post(f"{P}/deletion-requests/{rid}/approve", json={})
    assert r.status_code == expect, r.text
    return r.json()


def seed_author(env):
    """eng2 wrote comments on r1 (plain scope) and r2 (audit-sensitive: has an approval)."""
    env.approval("ap2", run="r2")
    mine = env.post("eng2", "run", "r1", "eng2 plain scope comment")["id"]
    kept = env.post("eng2", "run", "r2", "eng2 sensitive scope comment")["id"]
    other = env.post("lead", "run", "r1", "lead note mentioning eng2", mentions=["eng2"])["id"]
    env.as_("eng2").post(f"{P}/runs/r1/comments/{other}/reactions", json={"reaction": "heart"})
    env.as_("lead").post(f"{P}/runs/r1/comments/{mine}/reactions", json={"reaction": "heart"})
    return mine, kept, other


# -------------------------------------------------------------------------- requests


@pytest.mark.parametrize("user", ["lead", "view", "guest", "boss"])
def test_every_role_can_ask_for_its_own_data_and_the_subject_is_the_principal(env, user):
    out = request_deletion(env, user, reason="leaving the team", subjectUserId="somebody-else")
    assert out["subjectUserId"] == user and out["requestedBy"] == user
    assert out["state"] == "pending" and out["decidedBy"] is None
    assert out["policy"]["deletes"] and out["policy"]["retained"]
    stores = {r["store"] for r in out["policy"]["retained"]}
    assert {"audit_log", "run_collab_events", "high_impact_approvals"} <= stores
    row = env.audit("deletion.requested")[0]
    assert json.loads(row.detail)["subjectUserId"] == user and "leaving" not in row.detail


def test_one_open_request_per_person_and_a_retry_with_request_id_is_one_request(env):
    first = request_deletion(env, "eng2", requestId="r-1")
    err(env.as_("eng2").post(f"{P}/deletion-requests", json={}), 409, "DELETION_PENDING")
    again = env.as_("eng2").post(f"{P}/deletion-requests", json={"requestId": "r-1"})
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert again.json()["duplicate"] is True
    reject = env.as_("boss").post(
        f"{P}/deletion-requests/{first['id']}/reject", json={"note": "not now"}
    )
    assert reject.status_code == 200
    assert request_deletion(env, "eng2")["id"] != first["id"]  # a new one after a decision


def test_requests_are_visible_to_their_owner_and_team_admins_only(env):
    mine = request_deletion(env, "eng2")

    def listed(u):
        got = env.as_(u).get(f"{P}/deletion-requests").json()["requests"]
        return [r["id"] for r in got]

    assert listed("eng2") == [mine["id"]] and listed("boss") == [mine["id"]]
    assert listed("lead") == [] and listed("evil") == []
    r = env.as_("evil").post(f"{P}/deletion-requests/{mine['id']}/approve", json={})
    err(r, 404, "DELETION_NOT_FOUND")
    err(
        env.as_("evil").post(f"{P}/deletion-requests/nope/approve", json={}),
        404,
        "DELETION_NOT_FOUND",
    )


# --------------------------------------------------------------------------- decisions


def test_only_admins_decide_and_a_refusal_leaves_an_audit_row(env):
    rid = request_deletion(env, "eng2")["id"]
    for user in ("eng2", "lead", "view"):
        err(env.as_(user).post(f"{P}/deletion-requests/{rid}/approve", json={}), 403, "ROLE_DENIED")
        err(
            env.as_(user).post(f"{P}/deletion-requests/{rid}/reject", json={"note": "no"}),
            403,
            "ROLE_DENIED",
        )
    assert len(env.audit("authz.denied")) == 6
    assert env.as_("boss").get(f"{P}/deletion-requests").json()["requests"][0]["state"] == "pending"


def test_an_admin_cannot_decide_their_own_request_unless_self_approval_is_enabled(env, monkeypatch):
    rid = request_deletion(env, "boss")["id"]
    err(env.as_("boss").post(f"{P}/deletion-requests/{rid}/approve", json={}), 403, "SELF_DECISION")
    monkeypatch.setenv("HARNESS_ALLOW_SELF_APPROVAL", "1")
    assert approve(env, rid)["state"] == "executed"


def test_rejection_needs_a_reason_deletes_nothing_and_cannot_be_executed_later(env):
    mine, _, _ = seed_author(env)
    rid = request_deletion(env, "eng2")["id"]
    err(
        env.as_("boss").post(f"{P}/deletion-requests/{rid}/reject", json={}), 422, "REASON_REQUIRED"
    )
    done = (
        env.as_("boss")
        .post(f"{P}/deletion-requests/{rid}/reject", json={"note": "needed for an investigation"})
        .json()
    )
    assert done["state"] == "rejected" and done["decisionNote"].startswith("needed")
    assert env.q("SELECT state FROM comments WHERE id = :i", i=mine)[0].state == "visible"
    err(env.as_("boss").post(f"{P}/deletion-requests/{rid}/approve", json={}), 409, "NOT_PENDING")
    err(
        env.as_("boss").post(f"{P}/deletion-requests/{rid}/reject", json={"note": "again"}),
        409,
        "NOT_PENDING",
    )
    actions = [a.action for a in env.audit() if a.action.startswith("deletion.")]
    assert actions == ["deletion.requested", "deletion.rejected"]


# -------------------------------------------------------------------------- execution


def test_execution_deletes_the_documented_list_and_keeps_the_documented_exceptions(env):
    mine, kept, other = seed_author(env)
    env.as_("eng2").patch(
        f"{P}/runs/r1/comments/{mine}", json={"body": "edited plain comment", "expectedVersion": 1}
    )
    env.sync()
    assert any(d.source_id == mine for d in env.docs("comment"))
    rid = request_deletion(env, "eng2")["id"]
    out = approve(env, rid)
    assert out["state"] == "executed" and out["decidedBy"] == "boss"
    counts = out["result"]["deleted"]
    assert counts["commentsTombstoned"] == 2 and counts["reactions"] == 1
    assert counts["mentions"] == 1 and counts["commentRevisionsBlanked"] == 2
    assert counts["commentRevisionsRetained"] == 1 and counts["searchDocuments"] >= 1
    assert out["result"]["retained"] == exports.RETAINED
    states = {
        r.id: r
        for r in env.q("SELECT id, state, body, author_name_snapshot, author_user_id FROM comments")
    }
    for cid in (mine, kept):
        assert states[cid].state == "deleted" and states[cid].body == ""
        assert states[cid].author_name_snapshot == "deleted user"
        assert states[cid].author_user_id == "eng2"  # the tombstone keeps the opaque id
    assert states[other].state == "visible" and states[other].body.startswith("lead note")
    revs = {r.comment_id: r.body for r in env.q("SELECT comment_id, body FROM comment_revisions")}
    assert revs[mine] == "" and revs[kept] == "eng2 sensitive scope comment"  # audit-sensitive
    assert env.q("SELECT 1 FROM comment_reactions WHERE user_id = 'eng2'") == []
    assert env.q("SELECT 1 FROM comment_mentions WHERE mentioned_user_id = 'eng2'") == []
    assert all(d.source_id not in (mine, kept) for d in env.docs("comment"))
    audit = env.audit("deletion.executed")
    assert len(audit) == 1
    detail = json.loads(audit[0].detail)
    assert detail["counts"]["commentsTombstoned"] == 2 and "plain scope" not in audit[0].detail
    assert [a.action for a in env.audit() if a.action.startswith("deletion.")] == [
        "deletion.requested",
        "deletion.approved",
        "deletion.executed",
    ]


def test_search_no_longer_finds_the_deleted_text_even_with_a_stale_index(env):
    mine, _, _ = seed_author(env)
    env.sync()
    assert env.hits("lead", q="plain scope comment") != []
    approve(env, request_deletion(env, "eng2")["id"])
    env.sync()
    assert env.hits("lead", q="plain scope comment") == []
    assert env.hits("lead", q="lead note") != []


def test_repeating_the_approval_is_a_no_op_and_a_crash_before_execution_is_resumable(env):
    seed_author(env)
    rid = request_deletion(env, "eng2")["id"]
    # simulate a crash between approval and execution
    env.x(
        "UPDATE deletion_requests SET state = 'approved', decided_by = 'boss' WHERE id = :i", i=rid
    )
    first = approve(env, rid)
    assert first["state"] == "executed"
    again = approve(env, rid)
    assert again["duplicate"] is True and again["executedAt"] == first["executedAt"]
    assert len(env.audit("deletion.executed")) == 1


def test_export_files_of_the_person_and_covering_their_data_are_deleted(env):
    env.post("eng2", "run", "r1", "my words")
    own = env.export("eng2", kind="run_history", scopeId="r1")
    others = env.export("lead", kind="run_history", scopeId="r1")  # holds eng2's comment
    unrelated = env.export("lead", kind="run_history", scopeId="r2")  # holds nothing of eng2
    audit_copy = env.export("boss", kind="audit_evidence")
    store = Path(exports.store_dir())
    assert len(list(store.iterdir())) == 4
    out = approve(env, request_deletion(env, "eng2")["id"])
    assert out["result"]["deleted"]["exportFiles"] == 2
    left = sorted(p.name for p in store.iterdir())
    assert left == sorted([f"{unrelated['id']}.json", f"{audit_copy['id']}.json"])
    for job in (own, others):
        listed = env.as_("boss").get(f"{P}/exports/{job['id']}").json()
        assert listed["state"] == "deleted" and listed["download"] is None
        err(env.as_("boss").get(f"{P}/exports/{job['id']}/download"), 410, "EXPORT_EXPIRED")
    assert env.as_("lead").get(f"{P}/exports/{unrelated['id']}/download").status_code == 200
    # the audit evidence copy is a documented retained exception
    assert env.as_("boss").get(f"{P}/exports/{audit_copy['id']}/download").status_code == 200
    assert env.q("SELECT COUNT(*) AS n FROM export_jobs WHERE storage_ref IS NOT NULL")[0].n == 2


def test_later_exports_show_the_erased_person_as_a_deleted_user(env):
    env.event("redirect.guidance", {"content": "eng2 guidance"}, actor="eng2")
    env.tick(seconds=1)
    approve(env, request_deletion(env, "eng2")["id"])
    env.tick(seconds=1)
    env.event("redirect.guidance", {"content": "after the deletion"}, actor="eng2")
    doc = env.export_doc("lead", env.export("lead", kind="run_history", scopeId="r1")["id"]).json()
    by_text = {
        r["data"]["payload"].get("content"): r["actor"]
        for r in doc["records"]
        if r["stream"] == "collab"
    }
    assert by_text["eng2 guidance"] == {
        "userId": None,
        "displayName": "deleted user",
        "erased": True,
    }
    assert by_text["after the deletion"]["userId"] == "eng2"  # new activity is not retroactive
    erased = [
        r for r in doc["records"] if r["data"].get("payload", {}).get("content") == "eng2 guidance"
    ]
    assert "Name eng2" not in json.dumps(erased)


def test_search_documents_drop_the_person_from_participant_lists(env):
    env.seat("r1", "eng2")
    env.sync()
    assert env.hits("lead", kind="run", participantId="eng2") == [("run", "r1")]
    approve(env, request_deletion(env, "eng2")["id"])
    ids = json.loads(
        env.q(
            "SELECT participant_ids FROM search_documents WHERE kind = 'run' AND source_id = 'r1'"
        )[0].participant_ids
    )
    assert "eng2" not in ids
