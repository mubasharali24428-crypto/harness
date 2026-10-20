"""Part 44 (lane PL), directive 003: plan comments, proposed edits, the approval lock. Sqlite with
the REAL migration 0047, a fake clock, a principal override. Behaviour: docs/plans.md."""

import json

from harness_orchestrator import plans

from . import test_plans_support as support
from .test_plans import race
from .test_plans_support import CONTENT, runp
from .test_plans_support import err as err

env = support.env  # the shared fixture

ADD_STEP = [{"op": "add", "path": "/steps/-", "value": "Update the docs"}]
NEW_GOAL = [{"op": "replace", "path": "/goal", "value": "Retry with jitter"}]


def comment(env, user, n=1, body="looks risky", run="r1", **extra):
    return env.as_(user).post(
        runp(run, f"/plan/versions/{n}/comments"), json={"body": body, **extra}
    )


# --------------------------------------------------------------------- comments


def test_reviewers_comment_on_a_version_and_the_list_pages(env):
    env.new_version(expected=0)
    ids = []
    for i, user in enumerate(("view", "eng2", "boss")):
        env.tick(seconds=1)
        r = comment(env, user, body=f"note {i}", fieldPath="steps/1" if i == 0 else None)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    first = env.as_("lead").get(runp("r1", "/plan/versions/1/comments"), params={"limit": 2}).json()
    assert [c["body"] for c in first["comments"]] == ["note 0", "note 1"]
    assert first["comments"][0]["fieldPath"] == "steps/1"
    assert first["comments"][0]["author"] == {"userId": "view", "name": "Name view"}
    assert first["nextCursor"] == ids[1]
    rest = (
        env.as_("lead")
        .get(runp("r1", "/plan/versions/1/comments"), params={"after": first["nextCursor"]})
        .json()
    )
    assert [c["body"] for c in rest["comments"]] == ["note 2"] and rest["nextCursor"] is None
    other = env.as_("lead").get(runp("r1", "/plan/versions/1/comments"), params={"after": "zz"})
    err(other, 422, "BAD_CURSOR")
    assert (
        env.as_("lead").get(runp("r1", "/plan/versions")).json()["versions"][0]["commentCount"] == 3
    )


def test_comments_are_audited_by_id_never_by_text(env):
    env.new_version(expected=0)
    comment(env, "view", body="the secret rollout plan is wrong")
    rows = env.audit("plan.comment_added")
    assert len(rows) == 1 and "secret rollout" not in rows[0].detail
    assert json.loads(rows[0].detail)["planVersion"] == 1
    assert "plan.comment_added" in env.kinds()
    payloads = env.q("SELECT payload FROM run_collab_events WHERE kind = 'plan.comment_added'")
    assert "secret rollout" not in payloads[0].payload


def test_comment_rules(env):
    env.new_version(expected=0)
    err(comment(env, "guest"), 403, "ROLE_DENIED")
    err(comment(env, "lead", n=4), 404, "VERSION_NOT_FOUND")
    err(comment(env, "lead", fieldPath="nope"), 422, "BAD_FIELD_PATH")
    assert comment(env, "lead", body="").status_code == 422
    assert comment(env, "lead", body="x" * 4001).status_code == 422
    assert comment(env, "lead", fieldPath="../etc").status_code == 422
    assert env.q("SELECT 1 FROM plan_comments") == []


def test_a_retried_comment_is_one_comment(env):
    env.new_version(expected=0)
    a = comment(env, "lead", requestId="c-1")
    b = comment(env, "lead", requestId="c-1")
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert len(env.q("SELECT 1 FROM plan_comments")) == 1
    assert len(env.audit("plan.comment_added")) == 1


def test_a_version_takes_a_bounded_number_of_comments(env, monkeypatch):
    monkeypatch.setattr(plans, "MAX_COMMENTS", 2)
    env.new_version(expected=0)
    comment(env, "lead")
    comment(env, "lead")
    err(comment(env, "lead"), 409, "TOO_MANY_COMMENTS")


def test_a_comment_cursor_from_another_version_is_refused(env):
    env.new_version(expected=0)
    env.new_version(expected=1, content={**CONTENT, "goal": "g2"})
    cid = comment(env, "lead", n=1).json()["id"]
    r = env.as_("lead").get(runp("r1", "/plan/versions/2/comments"), params={"after": cid})
    err(r, 422, "BAD_CURSOR")


# ------------------------------------------------------------------------ edits


def test_an_edit_is_proposed_decided_and_a_new_version_appears(env):
    env.new_version(expected=0)
    e = env.propose("eng2", 1, ADD_STEP + NEW_GOAL, rationale="docs and jitter")
    assert e["state"] == "pending" and e["baseVersion"] == 1 and e["stale"] is False
    assert e["patch"][0]["path"] == "/steps/-" and e["proposedBy"] == "eng2"
    assert env.versions()["versions"][0]["pendingEditCount"] == 1
    env.tick(minutes=1)
    r = env.decide("boss", 1, e["id"], note="good")
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["state"] == "accepted" and done["decision"]["resultingVersion"] == 2
    assert done["decision"]["by"] == "boss" and done["decision"]["note"] == "good"
    v2 = env.as_("view").get(runp("r1", "/plan/versions/2")).json()
    assert v2["parentVersion"] == 1 and v2["fromEditId"] == e["id"] and v2["state"] == "proposed"
    assert v2["content"]["goal"] == "Retry with jitter"
    assert v2["content"]["steps"] == [*CONTENT["steps"], "Update the docs"]
    assert v2["author"] == {"kind": "user", "id": "boss"}
    assert env.as_("view").get(runp("r1", "/plan/versions/1")).json()["state"] == "superseded"
    assert env.as_("view").get(runp("r1", "/plan/versions/1")).json()["content"] == CONTENT
    diff = env.as_("view").get(runp("r1", "/plan/versions/2/diff")).json()
    assert diff["changedFields"] == ["goal", "steps"]
    kinds = env.kinds()
    assert "plan.edit_proposed" in kinds and "plan.edit_decided" in kinds
    assert kinds.count("plan.version_created") == 2


def test_edit_audit_rows_carry_ids_and_states_only(env):
    env.new_version(expected=0)
    e = env.propose("eng2", 1, [{"op": "replace", "path": "/goal", "value": "TOPSECRETGOAL"}])
    env.decide("boss", 1, e["id"])
    prop, dec = env.audit("plan.edit_proposed"), env.audit("plan.edit_decided")
    assert len(prop) == 1 and len(dec) == 1
    for row in (*prop, *dec, *env.audit("plan.version_created")):
        assert "TOPSECRETGOAL" not in row.detail and row.target_id == "r1"
    d = json.loads(dec[0].detail)
    assert d["before"] == {"state": "pending"} and d["after"] == {"state": "accepted"}
    assert d["resultingVersion"] == 2 and d["editId"] == e["id"] and d["userId"] == "boss"


def test_a_rejected_edit_creates_nothing(env):
    env.new_version(expected=0)
    e = env.propose("eng2", 1, NEW_GOAL)
    r = env.decide("boss", 1, e["id"], "reject", note="no")
    assert r.json()["state"] == "rejected" and r.json()["decision"]["resultingVersion"] is None
    assert len(env.versions()["versions"]) == 1
    assert env.as_("lead").get(runp("r1", "/plan/versions/1")).json()["state"] == "proposed"
    assert (
        env.as_("lead").get(runp("r1", "/plan/versions/1/edits")).json()["edits"][0]["state"]
        == "rejected"
    )


def test_who_may_propose_and_who_may_decide(env):
    env.new_version(expected=0)
    err(
        env.as_("guest").post(runp("r1", "/plan/versions/1/edits"), json={"patch": NEW_GOAL}),
        403,
        "ROLE_DENIED",
    )
    e = env.propose("view", 1, NEW_GOAL)  # a viewer may propose, like comment
    for user in ("view", "guest"):
        err(env.decide(user, 1, e["id"]), 403, "ROLE_DENIED")
    assert (
        env.as_("lead").get(runp("r1", "/plan/versions/1/edits")).json()["edits"][0]["state"]
        == "pending"
    )
    assert env.decide("eng3", 1, e["id"]).status_code == 200


def test_nobody_accepts_their_own_proposal_but_may_withdraw_it(env, monkeypatch):
    env.new_version(expected=0)
    e = env.propose("eng2", 1, NEW_GOAL)
    err(env.decide("eng2", 1, e["id"]), 403, "SELF_APPROVAL")
    assert len(env.audit("authz.denied")) == 1
    assert env.as_("lead").get(runp("r1", "/plan/versions")).json()["latestVersion"] == 1
    assert env.decide("eng2", 1, e["id"], "reject").json()["state"] == "rejected"
    e2 = env.propose("eng2", 1, ADD_STEP)
    monkeypatch.setenv("HARNESS_ALLOW_SELF_APPROVAL", "1")  # the single-person-team opt-out
    assert env.decide("eng2", 1, e2["id"]).json()["state"] == "accepted"


def test_decisions_are_idempotent_for_the_same_person_and_final_for_others(env):
    env.new_version(expected=0)
    e = env.propose("eng2", 1, NEW_GOAL)
    assert env.decide("boss", 1, e["id"]).status_code == 200
    again = env.decide("boss", 1, e["id"])
    assert again.status_code == 200 and again.json()["decision"]["resultingVersion"] == 2
    assert len(env.versions()["versions"]) == 2
    err(env.decide("boss", 1, e["id"], "reject"), 409, "EDIT_ALREADY_DECIDED")
    err(env.decide("eng3", 1, e["id"]), 409, "EDIT_ALREADY_DECIDED")


def test_two_approvers_deciding_one_edit_make_one_version(env):
    env.new_version(expected=0)
    e = env.propose("view", 1, NEW_GOAL)
    a, b = env.principal("boss"), env.principal("eng3")
    body = plans.DecideBody(decision="accept")
    out = race(
        env,
        lambda db: plans.decide_edit(db, a, "r1", 1, e["id"], body),
        lambda db: plans.decide_edit(db, b, "r1", 1, e["id"], body),
    )
    wins = [o for o in out if not isinstance(o, plans.PlansError)]
    assert len(wins) == 1
    assert [o.code for o in out if isinstance(o, plans.PlansError)] == ["EDIT_ALREADY_DECIDED"]
    assert len(env.q("SELECT 1 FROM plan_versions")) == 2


def test_a_second_edit_on_the_same_base_goes_stale_once_the_first_is_accepted(env):
    env.new_version(expected=0)
    first = env.propose("eng2", 1, NEW_GOAL)
    second = env.propose("eng3", 1, ADD_STEP)
    env.decide("boss", 1, first["id"])
    listed = env.as_("lead").get(runp("r1", "/plan/versions/1/edits")).json()["edits"]
    assert {e["id"]: e["stale"] for e in listed} == {first["id"]: False, second["id"]: True}
    assert err(env.decide("boss", 1, second["id"]), 409, "STALE_EDIT")["currentVersion"] == 2
    after = env.as_("lead").get(runp("r1", "/plan/versions/1/edits")).json()["edits"]
    assert {e["id"]: e["state"] for e in after}[second["id"]] == "pending"
    assert env.decide("boss", 1, second["id"], "reject").status_code == 200  # still closable


def test_an_edit_needs_the_latest_open_version(env):
    env.new_version(expected=0)
    env.new_version(expected=1, content={**CONTENT, "goal": "g2"})
    r = env.as_("lead").post(runp("r1", "/plan/versions/1/edits"), json={"patch": NEW_GOAL})
    assert err(r, 409, "VERSION_NOT_OPEN")["currentVersion"] == 2
    err(
        env.as_("lead").post(runp("r1", "/plan/versions/9/edits"), json={"patch": NEW_GOAL}),
        404,
        "VERSION_NOT_FOUND",
    )


def test_a_bad_patch_is_refused_before_it_is_stored(env):
    env.new_version(expected=0)

    def post(patch, **kw):
        return env.as_("lead").post(
            runp("r1", "/plan/versions/1/edits"), json={"patch": patch, **kw}
        )

    err(post([{"op": "replace", "path": "/steps/9", "value": "x"}]), 422, "BAD_PATCH")
    err(post([{"op": "remove", "path": "/steps/9"}]), 422, "BAD_PATCH")
    err(post([{"op": "add", "path": "/steps/9", "value": "x"}]), 422, "BAD_PATCH")
    err(post([{"op": "add", "path": "/goal", "value": "x"}]), 422, "BAD_PATCH")
    err(post([{"op": "remove", "path": "/goal"}]), 422, "BAD_PATCH")
    err(post([{"op": "replace", "path": "/nope", "value": "x"}]), 422, "BAD_PATCH")
    err(post([{"op": "replace", "path": "/goal/0", "value": "x"}]), 422, "BAD_PATCH")
    err(post([{"op": "replace", "path": "/steps/0"}]), 422, "BAD_PATCH")
    err(post([{"op": "replace", "path": "/goal", "value": ""}]), 422, "BAD_PLAN")
    err(post([{"op": "add", "path": "/filesLikelyToChange/-", "value": "../x"}]), 422, "BAD_PLAN")
    err(post([{"op": "add", "path": "/steps/-", "value": 5}]), 422, "BAD_PLAN")
    err(post([{"op": "replace", "path": "/goal", "value": CONTENT["goal"]}]), 422, "NO_CHANGE")
    assert post([]).status_code == 422
    assert post(NEW_GOAL * 21).status_code == 422
    assert post([{"op": "move", "path": "/goal", "value": "x"}]).status_code == 422
    assert post([{"op": "replace", "path": "goal", "value": "x"}]).status_code == 422
    assert post(NEW_GOAL, rationale="r" * 1001).status_code == 422
    assert env.q("SELECT 1 FROM plan_edits") == []


def test_ops_apply_in_order_and_list_indexes_see_earlier_ops(env):
    env.new_version(expected=0)
    e = env.propose(
        "eng2",
        1,
        [
            {"op": "remove", "path": "/steps/0"},
            {"op": "add", "path": "/steps/0", "value": "First now"},
            {"op": "replace", "path": "/steps/2", "value": "Last replaced"},
            {"op": "replace", "path": "/tests", "value": ["a", "b"]},
            {"op": "replace", "path": "/estimatedCostUsd", "value": 1.5},
        ],
    )
    env.decide("boss", 1, e["id"])
    c = env.as_("lead").get(runp("r1", "/plan/versions/2")).json()["content"]
    assert c["steps"] == ["First now", "Add retry loop", "Last replaced"]
    assert c["tests"] == ["a", "b"] and c["estimatedCostUsd"] == 1.5


def test_a_removed_value_is_not_kept_and_a_patch_has_a_size_cap(env):
    env.new_version(expected=0)
    e = env.propose("eng2", 1, [{"op": "remove", "path": "/steps/0", "value": "Z" * 5000}])
    assert e["patch"][0]["value"] is None
    big = [{"op": "replace", "path": "/goal", "value": "g" * 4000}] * 20
    err(
        env.as_("eng2").post(runp("r1", "/plan/versions/1/edits"), json={"patch": big}),
        422,
        "PATCH_TOO_LARGE",
    )


def test_a_retried_proposal_is_one_edit(env):
    env.new_version(expected=0)
    a = env.propose("eng2", 1, NEW_GOAL, requestId="e-1")
    r = env.as_("eng2").post(
        runp("r1", "/plan/versions/1/edits"), json={"patch": NEW_GOAL, "requestId": "e-1"}
    )
    assert r.status_code == 200 and r.json()["id"] == a["id"]
    assert len(env.q("SELECT 1 FROM plan_edits")) == 1 and len(env.audit("plan.edit_proposed")) == 1


def test_a_version_takes_a_bounded_number_of_pending_edits(env, monkeypatch):
    monkeypatch.setattr(plans, "MAX_PENDING_EDITS", 2)
    env.new_version(expected=0)
    env.propose("eng2", 1, NEW_GOAL)
    env.propose("eng2", 1, ADD_STEP)
    r = env.as_("eng2").post(runp("r1", "/plan/versions/1/edits"), json={"patch": ADD_STEP})
    err(r, 409, "TOO_MANY_EDITS")


def test_an_edit_id_from_another_version_or_run_is_a_404(env):
    env.new_version(expected=0)
    env.new_version("evil", "r3", 0)
    e = env.propose("eng2", 1, NEW_GOAL)
    err(env.decide("boss", 2, e["id"]), 404, "EDIT_NOT_FOUND")
    err(env.decide("boss", 1, "nope"), 404, "EDIT_NOT_FOUND")
    err(env.decide("boss", 1, e["id"], run="r3"), 404, "NOT_FOUND")
    err(env.decide("evil", 1, e["id"], run="r3"), 404, "EDIT_NOT_FOUND")
    err(env.decide("evil", 1, e["id"], run="r1"), 404, "NOT_FOUND")
    assert (
        env.as_("lead").get(runp("r1", "/plan/versions/1/edits")).json()["edits"][0]["state"]
        == "pending"
    )


# -------------------------------------------------------------- the approval lock


def test_everything_is_refused_once_the_plan_is_approved(env):
    env.legacy()
    env.versions()
    e = env.propose("eng2", 1, NEW_GOAL)
    c = comment(env, "lead").json()
    env.set_legacy("approved")

    def locked(resp):
        assert err(resp, 409, "PLAN_LOCKED")["approvedVersion"] == 1

    locked(
        env.as_("lead").post(
            runp("r1", "/plan/versions"), json={"content": CONTENT, "expectedVersion": 1}
        )
    )
    locked(comment(env, "lead", body="late"))
    locked(env.as_("eng2").post(runp("r1", "/plan/versions/1/edits"), json={"patch": ADD_STEP}))
    locked(env.decide("boss", 1, e["id"]))
    locked(env.decide("boss", 1, e["id"], "reject"))
    assert len(env.q("SELECT 1 FROM plan_versions")) == 1
    assert len(env.q("SELECT 1 FROM plan_comments")) == 1
    assert len(env.q("SELECT 1 FROM plan_edits WHERE state = 'pending'")) == 1
    # reading keeps working, and the old pending edit says it can no longer land
    listed = env.as_("view").get(runp("r1", "/plan/versions/1/edits")).json()["edits"]
    assert listed[0]["stale"] is True
    assert (
        env.as_("view").get(runp("r1", "/plan/versions/1/comments")).json()["comments"][0]["id"]
        == c["id"]
    )
    one = env.as_("lead").get(runp("r1", "/plan/versions/1")).json()
    assert one["can"] == {
        "comment": False,
        "proposeEdit": False,
        "decideEdit": False,
        "approve": False,
        "newVersion": False,
    }


def test_a_retried_create_after_approval_still_answers_with_the_original(env):
    env.new_version(expected=0, requestId="r-1")
    env.legacy()  # the engine proposes its own plan later: v2 mirrors it
    env.versions()
    env.set_legacy("approved")
    r = env.as_("lead").post(
        runp("r1", "/plan/versions"),
        json={"content": CONTENT, "expectedVersion": 0, "requestId": "r-1"},
    )
    assert r.status_code == 200 and r.json()["version"] == 1


def test_the_can_flags_follow_the_role_and_the_state(env):
    env.new_version(expected=0)
    can = lambda user: env.as_(user).get(runp("r1", "/plan/versions/1")).json()["can"]  # noqa: E731
    assert can("boss") == {
        "comment": True,
        "proposeEdit": True,
        "decideEdit": True,
        "approve": True,
        "newVersion": True,
    }
    assert can("view") == {
        "comment": True,
        "proposeEdit": True,
        "decideEdit": False,
        "approve": False,
        "newVersion": False,
    }
    assert can("guest") == {
        "comment": False,
        "proposeEdit": False,
        "decideEdit": False,
        "approve": False,
        "newVersion": False,
    }
    env.new_version(expected=1, content={**CONTENT, "goal": "g2"})
    assert can("boss")["proposeEdit"] is False  # version 1 is superseded
