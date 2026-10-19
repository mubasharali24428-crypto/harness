"""Part 43 (lane D): comments on runs and sessions. Sqlite with the REAL migrations 0041, 0043
and 0045, a fake clock, a fake lane-N notify module. Behaviour: docs/discussion.md."""

import json
import sys

import pytest
from sqlalchemy.exc import IntegrityError

from . import test_discussion_support as support
from .test_discussion_support import P, base, err

env = support.env  # the shared fixture

RESERVED = {
    "comment.created", "comment.edited", "comment.deleted", "comment.hidden",
    "comment.restored", "comment.promoted", "comment.exported", "comment.purged",
    "discussion.settings_changed",
}
SCOPES = (("run", "r1", "r3"), ("session", "s1", "sb"))  # scope, own id, other team's id


# ----------------------------------------------------------- isolation, no oracle


def test_foreign_and_unknown_scopes_answer_the_same_404_on_every_route(env):
    for scope, own, _other in SCOPES:
        env.post("lead", scope, own, "hello")
        cid = env.listing("lead", scope, own)["comments"][0]["id"]
        calls = [
            ("get", "", None), ("post", "", {"body": "x"}),
            ("patch", f"/{cid}", {"body": "y", "expectedVersion": 1}), ("delete", f"/{cid}", None),
            ("get", f"/{cid}/history", None), ("get", "/export", None),
            ("post", f"/{cid}/reactions", {"reaction": "heart"}),
            ("post", f"/{cid}/promote", {"action": "send_as_guidance", "expectedVersion": 1}),
            ("post", f"/{cid}/moderate", {"action": "hide", "reason": "no"}),
        ]
        for method, tail, body in calls:
            kw = {"json": body} if body else {}
            unknown = getattr(env.as_("evil"), method)(base(scope, "nope") + tail, **kw)
            other = getattr(env.as_("evil"), method)(base(scope, own) + tail, **kw)
            assert unknown.status_code == 404, (scope, method, tail, unknown.text)
            assert other.json() == unknown.json(), (scope, method, tail)
    assert env.q("SELECT 1 FROM comments WHERE team_id = 'team-b'") == []


def test_a_private_session_is_a_404_for_non_members_like_an_unknown_session(env):
    env.post("lead", "session", "spriv", "private plan")
    c = env.as_("eng2")  # same team, not a member
    unknown = c.get(base("session", "nope"))
    assert c.get(base("session", "spriv")).json() == unknown.json()
    assert c.post(base("session", "spriv"), json={"body": "x"}).json() == unknown.json()
    assert env.as_("boss").get(base("session", "spriv")).status_code == 200  # admins see all


def test_deleted_session_is_admin_only_and_archived_session_is_read_only(env):
    env.post("lead", "session", "s1", "before")
    env.x("UPDATE collab_sessions SET state = 'archived' WHERE id = 's1'")
    assert env.as_("lead").get(base("session", "s1")).status_code == 200
    err(env.as_("lead").post(base("session", "s1"), json={"body": "x"}), 409, "SCOPE_READ_ONLY")
    env.x("UPDATE collab_sessions SET state = 'deleted' WHERE id = 's1'")
    assert env.as_("lead").get(base("session", "s1")).status_code == 404
    assert env.as_("boss").get(base("session", "s1")).status_code == 200


def test_role_gates_come_before_ownership_and_leave_an_audit_row(env):
    # a guest cannot tell a foreign run from its own: both 403, never 404 vs 403
    for rid in ("r1", "r3", "nope"):
        err(env.as_("guest").post(base("run", rid), json={"body": "x"}), 403, "ROLE_DENIED")
    assert any(r.action == "authz.denied" for r in env.audit())
    env.post("lead", "run", "r1", "hi")
    cid = env.listing("lead", "run", "r1")["comments"][0]["id"]
    promote = {"action": "send_as_guidance", "expectedVersion": 1}
    url = f"{base('run', 'r1')}/{cid}"
    err(env.as_("view").post(url + "/promote", json=promote), 403, "ROLE_DENIED")
    mod = {"action": "hide", "reason": "off topic"}
    err(env.as_("lead").post(url + "/moderate", json=mod), 403, "ROLE_DENIED")
    err(env.as_("view").get(f"{base('run', 'r1')}/export"), 403, "ROLE_DENIED")
    assert env.as_("view").post(base("run", "r1"), json={"body": "viewers may comment"}
                                ).status_code == 201


# --------------------------------------------------- create, replies, authorship


def test_create_list_and_authorship_are_visible(env):
    out = env.post("lead", "run", "r1", "  first comment  ")
    assert out["body"] == "first comment" and out["state"] == "visible"
    assert out["author"] == {"userId": "lead", "name": "Name lead"}
    assert out["createdAt"].startswith("2030-01-01T12:00:00") and out["editedAt"] is None
    assert out["version"] == 1 and out["edited"] is False and out["duplicate"] is False
    env.x("UPDATE team_members SET display_name = 'Renamed' WHERE user_id = 'lead'")
    got = env.listing("eng2", "run", "r1")
    assert got["comments"][0]["author"]["name"] == "Name lead"  # snapshot survives a rename
    assert got["policy"]["editWindowSeconds"] == 900 and got["policy"]["maxReplyDepth"] == 2
    assert "thumbs_up" in got["policy"]["reactions"]


def test_replies_are_bounded_and_stay_in_their_scope(env):
    a = env.post("lead", "run", "r1", "a")["id"]
    b = env.post("eng2", "run", "r1", "b", parentId=a)["id"]
    c = env.post("lead", "run", "r1", "c", parentId=b)
    assert c["depth"] == 2 and c["parentId"] == b
    deeper = env.as_("lead").post(base("run", "r1"), json={"body": "d", "parentId": c["id"]})
    err(deeper, 422, "REPLY_TOO_DEEP")
    err(env.as_("lead").post(base("run", "r1"), json={"body": "x", "parentId": "nope"}),
        422, "BAD_PARENT")
    other = env.post("lead", "run", "r2", "elsewhere")["id"]
    err(env.as_("lead").post(base("run", "r1"), json={"body": "x", "parentId": other}),
        422, "BAD_PARENT")  # a parent in another scope looks like an unknown one


def test_a_retried_create_with_the_same_request_id_is_one_comment(env):
    first = env.as_("lead").post(base("run", "r1"), json={"body": "once", "requestId": "rq-1"})
    again = env.as_("lead").post(base("run", "r1"), json={"body": "once", "requestId": "rq-1"})
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"] and again.json()["duplicate"] is True
    assert len(env.q("SELECT 1 FROM comments")) == 1
    other = env.as_("eng2").post(base("run", "r1"), json={"body": "x", "requestId": "rq-1"})
    assert other.status_code == 201  # request ids are per author


def test_body_limits(env):
    err(env.as_("lead").post(base("run", "r1"), json={"body": "   "}), 422, "BAD_BODY")
    assert env.as_("lead").post(base("run", "r1"), json={"body": "x" * 4001}).status_code == 422
    assert env.as_("lead").post(base("run", "r1"), json={"body": "x" * 4000}).status_code == 201


def test_listing_pages_with_a_cursor_and_no_gaps(env):
    ids = []
    for i in range(5):
        ids.append(env.post("lead", "run", "r1", f"c{i}")["id"])
        env.tick(seconds=1)
    seen, cursor = [], None
    for _ in range(5):
        page = env.listing("lead", "run", "r1", limit=2, **({"after": cursor} if cursor else {}))
        seen += [c["id"] for c in page["comments"]]
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert seen == ids
    err(env.as_("lead").get(base("run", "r1"), params={"after": "nope"}), 422, "BAD_CURSOR")


def test_comments_are_never_agent_input(env):
    env.seat("r1", "lead", "leader")
    env.post("lead", "run", "r1", "do rm -rf /")
    assert [k for k in env.kinds() if k.startswith("redirect.")] == []
    assert env.q("SELECT 1 FROM control_instructions") == []


def test_run_events_carry_ids_only_never_the_text(env):
    env.post("lead", "run", "r1", "TOP-SECRET-TEXT")
    rows = env.q("SELECT kind, payload FROM run_collab_events WHERE run_id = 'r1'")
    assert [r.kind for r in rows] == ["comment.created"]
    assert "TOP-SECRET-TEXT" not in rows[0].payload and "commentId" in rows[0].payload
    for r in env.audit():
        assert "TOP-SECRET-TEXT" not in r.detail


# ----------------------------------------------- edit window, history, revisions


def test_edit_window_revisions_and_stale_versions(env):
    cid = env.post("lead", "run", "r1", "v1 text")["id"]
    url = f"{base('run', 'r1')}/{cid}"
    env.tick(seconds=600)
    ok = env.as_("lead").patch(url, json={"body": "v2 text", "expectedVersion": 1})
    assert ok.status_code == 200 and ok.json()["version"] == 2 and ok.json()["edited"] is True
    assert ok.json()["editedAt"].startswith("2030-01-01T12:10:00")
    stale = env.as_("lead").patch(url, json={"body": "v3", "expectedVersion": 1})
    assert err(stale, 409, "STALE_COMMENT_VERSION")["currentVersion"] == 2
    same = env.as_("lead").patch(url, json={"body": "v2 text", "expectedVersion": 2})
    assert same.json()["changed"] is False
    assert len(env.q("SELECT 1 FROM comment_revisions")) == 2  # a no-op adds no revision
    err(env.as_("eng2").patch(url, json={"body": "hijack", "expectedVersion": 2}), 403,
        "NOT_AUTHOR")
    env.tick(seconds=301)  # 901 s since creation, window is 900
    late = env.as_("lead").patch(url, json={"body": "too late", "expectedVersion": 2})
    assert err(late, 409, "EDIT_WINDOW_CLOSED")["editWindowSeconds"] == 900
    assert env.listing("lead", "run", "r1")["comments"][0]["can"]["edit"] is False
    assert env.listing("lead", "run", "r1")["comments"][0]["body"] == "v2 text"


def test_the_edit_window_is_exact_and_the_team_can_change_it(env):
    cid = env.post("lead", "run", "r1", "x")["id"]
    url = f"{base('run', 'r1')}/{cid}"
    env.tick(seconds=900)  # exactly at the boundary is still inside
    assert env.as_("lead").patch(url, json={"body": "y", "expectedVersion": 1}).status_code == 200
    assert env.as_("boss").put(f"{P}/discussion-settings", json={"editWindowSeconds": 0}
                               ).status_code == 200
    err(env.as_("lead").patch(url, json={"body": "z", "expectedVersion": 2}), 409,
        "EDIT_WINDOW_CLOSED")
    assert env.as_("boss").put(f"{P}/discussion-settings", json={"editWindowSeconds": 86400}
                              ).status_code == 200
    env.tick(hours=20)
    assert env.as_("lead").patch(url, json={"body": "z", "expectedVersion": 2}).status_code == 200


def test_history_lists_every_revision_with_author_and_time(env):
    cid = env.post("lead", "run", "r1", "one")["id"]
    url = f"{base('run', 'r1')}/{cid}"
    env.tick(seconds=60)
    env.as_("lead").patch(url, json={"body": "two", "expectedVersion": 1})
    h = env.as_("eng2").get(url + "/history").json()
    assert [(r["version"], r["body"], r["editedBy"]) for r in h["revisions"]] == [
        (1, "one", "lead"), (2, "two", "lead")]
    assert h["revisions"][1]["editedAt"].startswith("2030-01-01T12:01:00")
    assert h["currentVersion"] == 2 and h["author"]["userId"] == "lead"


# ------------------------------------------------- delete: tombstones and audit


def test_delete_is_a_tombstone_and_blanks_revisions_in_an_ordinary_scope(env):
    cid = env.post("lead", "run", "r1", "oops private")["id"]
    reply = env.post("eng2", "run", "r1", "a reply", parentId=cid)["id"]
    url = f"{base('run', 'r1')}/{cid}"
    err(env.as_("eng2").delete(url), 403, "NOT_AUTHOR")
    assert env.as_("lead").delete(url).json() == {"id": cid, "state": "deleted", "changed": True}
    assert env.as_("lead").delete(url).json()["changed"] is False  # idempotent
    rows = {c["id"]: c for c in env.listing("eng2", "run", "r1")["comments"]}
    assert rows[cid]["state"] == "deleted" and rows[cid]["body"] is None
    assert rows[cid]["deleted"]["by"] == "lead" and rows[reply]["parentId"] == cid
    assert env.q("SELECT body FROM comment_revisions WHERE comment_id=:c", c=cid)[0].body == ""
    assert env.q("SELECT body FROM comments WHERE id=:c", c=cid)[0].body == ""
    err(env.as_("lead").patch(url, json={"body": "undo", "expectedVersion": 1}), 409,
        "COMMENT_UNAVAILABLE")
    gone = [a for a in env.audit("comment.deleted")]
    assert len(gone) == 1 and json.loads(gone[0].detail)["revisionsKept"] is False


def test_delete_in_an_audit_sensitive_scope_keeps_revisions_for_admins(env):
    env.approval("ap1", "r1")  # a high-impact approval makes the run audit-sensitive
    cid = env.post("lead", "run", "r1", "the approval is fine")["id"]
    url = f"{base('run', 'r1')}/{cid}"
    env.as_("lead").patch(url, json={"body": "the approval is wrong", "expectedVersion": 1})
    assert env.as_("lead").delete(url).status_code == 200
    kept = env.q("SELECT version, body FROM comment_revisions WHERE comment_id=:c ORDER BY version",
                 c=cid)
    assert [(r.version, r.body) for r in kept] == [(1, "the approval is fine"),
                                                    (2, "the approval is wrong")]
    assert env.q("SELECT body FROM comments WHERE id=:c", c=cid)[0].body == ""
    admin = env.as_("boss").get(url + "/history").json()
    assert [r["body"] for r in admin["revisions"]] == ["the approval is fine",
                                                       "the approval is wrong"]
    other = env.as_("eng2").get(url + "/history").json()
    assert [r["body"] for r in other["revisions"]] == [None, None] and other["state"] == "deleted"
    assert json.loads(env.audit("comment.deleted")[0].detail)["revisionsKept"] is True


def test_the_team_can_force_every_scope_audit_sensitive(env):
    env.as_("boss").put(f"{P}/discussion-settings", json={"auditSensitive": "always"})
    cid = env.post("lead", "run", "r2", "kept")["id"]
    env.as_("lead").delete(f"{base('run', 'r2')}/{cid}")
    assert env.q("SELECT body FROM comment_revisions WHERE comment_id=:c", c=cid)[0].body == "kept"


# ------------------------------------------------------------------ moderation


def test_moderation_hide_restore_delete_with_visible_state_and_audit(env):
    cid = env.post("eng2", "run", "r1", "spam spam")["id"]
    url = f"{base('run', 'r1')}/{cid}/moderate"
    assert env.as_("boss").post(url, json={"action": "hide", "reason": "off topic"}).json() == {
        "id": cid, "state": "hidden", "changed": True}
    assert env.as_("boss").post(url, json={"action": "hide", "reason": "again"}
                                ).json()["changed"] is False
    seen = {c["id"]: c for c in env.listing("lead", "run", "r1")["comments"]}[cid]
    assert seen["state"] == "hidden" and seen["body"] is None
    assert seen["moderation"]["reason"] == "off topic" and seen["moderation"]["by"] == "boss"
    assert env.listing("eng2", "run", "r1")["comments"][0]["body"] == "spam spam"  # the author
    assert env.listing("boss", "run", "r1")["comments"][0]["body"] == "spam spam"  # a moderator
    # a hidden comment takes no replies, reactions or promotion
    err(env.as_("lead").post(base("run", "r1"), json={"body": "r", "parentId": cid}), 409,
        "PARENT_UNAVAILABLE")
    err(env.as_("lead").post(f"{base('run', 'r1')}/{cid}/reactions", json={"reaction": "heart"}),
        409, "COMMENT_UNAVAILABLE")
    assert env.as_("boss").post(url, json={"action": "restore", "reason": "my mistake"}
                                ).json()["state"] == "visible"
    assert env.listing("lead", "run", "r1")["comments"][0]["body"] == "spam spam"
    assert env.as_("boss").post(url, json={"action": "delete", "reason": "abuse"}
                                ).json()["state"] == "deleted"
    err(env.as_("boss").post(url, json={"action": "restore", "reason": "undo"}), 409,
        "COMMENT_DELETED")
    final = env.listing("lead", "run", "r1")["comments"][0]
    assert final["body"] is None and final["moderation"]["reason"] == "abuse"
    assert [r.action for r in env.audit() if r.action.startswith("comment.")] == [
        "comment.created", "comment.hidden", "comment.restored", "comment.deleted"]


def test_moderation_needs_a_reason_and_stays_in_the_admins_team(env):
    cid = env.post("eng2", "run", "r1", "x")["id"]
    url = f"{base('run', 'r1')}/{cid}/moderate"
    assert env.as_("boss").post(url, json={"action": "hide", "reason": "ab"}).status_code == 422
    assert env.as_("boss").post(url, json={"action": "nuke", "reason": "abcd"}).status_code == 422
    assert env.as_("evil").post(url, json={"action": "hide", "reason": "abcd"}).status_code == 404


# ------------------------------------------------------------------- reactions


def test_reactions_are_a_closed_set_idempotent_and_counted(env):
    cid = env.post("lead", "run", "r1", "react to me")["id"]
    url = f"{base('run', 'r1')}/{cid}/reactions"
    assert env.as_("eng2").post(url, json={"reaction": "heart"}).status_code == 200
    again = env.as_("eng2").post(url, json={"reaction": "heart"}).json()
    assert again["reactions"] == {"heart": {"count": 1, "mine": True}}
    env.as_("view").post(url, json={"reaction": "heart"})
    env.as_("view").post(url, json={"reaction": "eyes"})
    got = env.listing("lead", "run", "r1")["comments"][0]["reactions"]
    assert got == {"heart": {"count": 2, "mine": False}, "eyes": {"count": 1, "mine": False}}
    assert env.as_("view").post(url, json={"reaction": "heart", "present": False}
                                ).json()["reactions"]["heart"]["count"] == 1
    assert env.as_("view").post(url, json={"reaction": "poop"}).status_code == 422
    assert env.as_("view").post(url, json={}).status_code == 422
    with pytest.raises(IntegrityError):  # the database enforces the set as well
        env.x("INSERT INTO comment_reactions VALUES (:c, 'u', 'poop', :t)", c=cid, t=support.T0)


# -------------------------------------------------------------------- mentions


def test_mentions_reach_only_people_who_can_see_the_scope_and_leak_nothing(env):
    out = env.post("lead", "session", "spriv", "eyes only",
                   mentions=["eng2", "lead", "evil", "ghost"])
    assert out["mentions"] == [] and out["ignoredMentions"] == 3  # self is dropped, not counted
    assert env.q("SELECT 1 FROM comment_mentions") == [] and env.notified == []
    ok = env.post("lead", "session", "s1", "hello team", mentions=["eng2", "view", "evil", "ghost"])
    assert [m["userId"] for m in ok["mentions"]] == ["eng2", "view"] and ok["ignoredMentions"] == 2
    assert {n["user_id"] for n in env.notified} == {"eng2", "view"}
    n = env.notified[0]
    assert n["kind"] == "mention" and n["source_kind"] == "comment" and n["source_id"] == ok["id"]
    assert n["session_id"] == "s1" and n["run_id"] is None and n["team_id"] == "team-a"
    assert n["dedupe_key"] == f"mention:{ok['id']}:{n['user_id']}"
    blob = json.dumps(env.notified, default=str)
    assert "hello team" not in blob and "SECRET TITLE" not in blob  # ids only, no text or title
    # the unknown, the foreign and the not-allowed users are indistinguishable to the author
    assert env.post("lead", "session", "spriv", "t", mentions=["ghost"])["ignoredMentions"] == 1
    assert env.post("lead", "session", "spriv", "t", mentions=["evil"])["ignoredMentions"] == 1
    # an admin sees every session of the team, so an admin can be mentioned
    adm = env.post("lead", "session", "spriv", "for you", mentions=["boss"])
    assert [m["userId"] for m in adm["mentions"]] == ["boss"]


def test_run_mentions_are_team_members_only(env):
    out = env.post("lead", "run", "r1", "ping", mentions=["eng3", "evil2", "ghost"])
    assert [m["userId"] for m in out["mentions"]] == ["eng3"] and out["ignoredMentions"] == 2
    assert env.notified[0]["run_id"] == "r1" and env.notified[0]["session_id"] is None


def test_an_edit_notifies_only_newly_mentioned_people(env):
    cid = env.post("lead", "run", "r1", "hi", mentions=["eng2"])["id"]
    assert len(env.notified) == 1
    url = f"{base('run', 'r1')}/{cid}"
    out = env.as_("lead").patch(
        url, json={"body": "hi again", "expectedVersion": 1, "mentions": ["eng2", "eng3"]}).json()
    assert [m["userId"] for m in out["mentions"]] == ["eng2", "eng3"]
    assert [n["user_id"] for n in env.notified] == ["eng2", "eng3"]
    out = env.as_("lead").patch(
        url, json={"body": "hi again", "expectedVersion": 2, "mentions": ["eng3"]}).json()
    assert [m["userId"] for m in out["mentions"]] == ["eng3"] and len(env.notified) == 2


def test_no_notify_module_degrades_to_no_notification(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "harness_orchestrator.notify", None)
    monkeypatch.delattr(support.harness_orchestrator, "notify", raising=False)
    out = env.post("lead", "run", "r1", "ping", mentions=["eng2"])
    assert [m["userId"] for m in out["mentions"]] == ["eng2"] and env.notified == []


def test_a_failing_notify_never_fails_or_loses_the_comment(env, monkeypatch):
    async def boom(db, **kw):
        raise RuntimeError("inbox down")

    monkeypatch.setattr(sys.modules["harness_orchestrator.notify"], "emit", boom)
    out = env.post("lead", "run", "r1", "ping", mentions=["eng2"])
    assert out["id"] and len(env.q("SELECT 1 FROM comments")) == 1
    assert len(env.q("SELECT 1 FROM comment_mentions")) == 1


# ------------------------------------------------------------------ references


def test_references_are_checked_on_write_and_reauthorized_on_every_read(env):
    env.approval("ap1", "r1")
    env.approval("apx", "r3", team="team-b")
    env.x("INSERT INTO agent_plans (id, run_id) VALUES ('pl1', 'r1')")
    env.x("INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind, ts)"
          " VALUES ('r1', 'team-a', 'lead', 'x', :t)", t=support.T0)
    seq = env.q("SELECT seq FROM run_collab_events WHERE kind = 'x'")[0].seq
    refs = [("approval", "ap1", None), ("plan", "pl1", None), ("diff", "ap1", 3),
            ("event", "r1", seq), ("file", "src/app.py", 12)]
    for kind, rid, pos in refs:
        env.post("lead", "run", "r1", f"see {kind}", refKind=kind, refId=rid, refPos=pos)
    got = {c["ref"]["kind"]: c["ref"] for c in env.listing("lead", "run", "r1")["comments"]}
    assert all(r["available"] for r in got.values()) and got["diff"]["pos"] == 3
    bad = [("approval", "apx", None), ("approval", "nope", None), ("plan", "nope", None),
           ("event", "r1", 99999), ("event", "r2", seq), ("diff", "ap1x", None),
           ("file", "../etc/passwd", None), ("file", "/abs", None), ("event", "r1", None)]
    for kind, rid, pos in bad:
        r = env.as_("lead").post(base("run", "r1"), json={
            "body": "x", "refKind": kind, "refId": rid, "refPos": pos})
        assert r.status_code == 422 and r.json()["detail"]["error"]["code"] in (
            "BAD_REF", "BAD_PATHS"), (kind, rid, r.text)
    err(env.as_("lead").post(base("run", "r1"), json={"body": "x", "refId": "ap1"}), 422, "BAD_REF")
    assert env.as_("lead").post(base("run", "r1"), json={"body": "x", "refKind": "movie",
                                                         "refId": "a"}).status_code == 422
    # access changes later: the approval moves to another team's run, the plan disappears
    env.x("UPDATE high_impact_approvals SET run_id = 'r3', team_id = 'team-b' WHERE id = 'ap1'")
    env.x("DELETE FROM agent_plans")
    after = {c["ref"]["kind"]: c["ref"] for c in env.listing("lead", "run", "r1")["comments"]}
    for kind in ("approval", "plan", "diff"):
        assert after[kind] == {"kind": kind, "available": False}  # no id, no position
    assert after["file"]["available"] and after["event"]["available"]


def test_session_references_resolve_through_the_sessions_runs(env):
    env.approval("ap1", "r1")  # r1 is linked to s1
    env.approval("ap2", "r2")  # r2 is not
    env.post("lead", "session", "s1", "see", refKind="approval", refId="ap1")
    err(env.as_("lead").post(base("session", "s1"), json={
        "body": "x", "refKind": "approval", "refId": "ap2"}), 422, "BAD_REF")


# --------------------------------------------------------------- audit, closed set


def test_every_audit_action_is_a_reserved_name_and_no_text_is_logged(env):
    cid = env.post("lead", "run", "r1", "AUDIT-LEAK-CHECK", mentions=["eng2"])["id"]
    url = f"{base('run', 'r1')}/{cid}"
    env.as_("lead").patch(url, json={"body": "AUDIT-LEAK-CHECK2", "expectedVersion": 1})
    env.as_("boss").post(url + "/moderate", json={"action": "hide", "reason": "AUDIT-LEAK-REASON"})
    env.as_("boss").post(url + "/moderate", json={"action": "restore", "reason": "ok fine"})
    env.as_("boss").get(f"{base('run', 'r1')}/export")
    env.as_("lead").delete(url)
    env.as_("boss").put(f"{P}/discussion-settings", json={"retentionDays": 90})
    actions = {r.action for r in env.audit()}
    assert actions <= RESERVED | {"authz.denied"}, actions
    assert RESERVED - {"comment.promoted", "comment.purged"} <= actions
    for r in env.audit():
        assert "AUDIT-LEAK" not in r.detail
        d = json.loads(r.detail)
        assert d["teamId"] == "team-a" and d["userId"] in ("lead", "boss")
    changed = json.loads(env.audit("discussion.settings_changed")[0].detail)
    assert changed["before"]["retentionDays"] == 365 and changed["after"]["retentionDays"] == 90
