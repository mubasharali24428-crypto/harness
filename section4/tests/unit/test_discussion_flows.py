"""Part 43 (lane D), second half: promote (the only road from discussion to agent guidance),
search, export, retention, settings and races. Same fixture as test_discussion.py."""

import asyncio
import json

from harness_orchestrator import discussion

from . import test_discussion_support as support
from .test_discussion_support import P, base, err

env = support.env  # the shared fixture

GUIDE = {"action": "send_as_guidance", "expectedVersion": 1}


def promote_url(scope, sid, cid):
    return f"{base(scope, sid)}/{cid}/promote"


def guidance(env, run="r1"):
    rows = env.q("SELECT payload FROM run_collab_events WHERE run_id=:r"
                 " AND kind='redirect.guidance' ORDER BY seq", r=run)
    return [json.loads(r.payload) for r in rows]


def hold(env, user="lead", run="r1"):
    r = env.as_(user).post(f"{P}/runs/{run}/control/acquire")
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------- promote


def test_promote_is_an_explicit_steer_action_that_writes_ordinary_guidance(env):
    hold(env)
    cid = env.post("eng2", "run", "r1", "please also update the changelog")["id"]
    assert guidance(env) == []  # discussion alone never reaches the agent
    url = promote_url("run", "r1", cid)
    bare = env.as_("lead").post(url, json={"expectedVersion": 1})  # no explicit action
    assert bare.status_code == 422
    assert env.as_("lead").post(url, json={"action": "approve", "expectedVersion": 1}
                                ).status_code == 422
    assert guidance(env) == [] and env.q("SELECT 1 FROM comment_promotions") == []
    out = env.as_("lead").post(url, json=GUIDE)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["runId"] == "r1" and body["duplicate"] is False and body["queued"] is False
    assert guidance(env) == [{"content": "please also update the changelog",
                              "reason": f"comment:{cid}"}]
    link = env.q("SELECT run_id, promoted_by, comment_version, event_seq FROM comment_promotions")
    assert [(x.run_id, x.promoted_by, x.comment_version, x.event_seq) for x in link] == [
        ("r1", "lead", 1, body["seq"])]
    # the same path as POST redirect: its audit row and its event kind
    assert any(r.action == "redirect.recorded" for r in env.audit())
    promoted = env.audit("comment.promoted")
    assert len(promoted) == 1 and "changelog" not in promoted[0].detail
    shown = env.listing("eng2", "run", "r1")["comments"][0]
    assert shown["promotion"]["by"] == "lead" and shown["promotion"]["seq"] == body["seq"]
    assert shown["can"]["promote"] is False


def test_promote_is_idempotent_and_a_retry_heals_a_half_done_promotion(env):
    hold(env)
    cid = env.post("eng2", "run", "r1", "once only")["id"]
    url = promote_url("run", "r1", cid)
    first = env.as_("lead").post(url, json=GUIDE).json()
    again = env.as_("lead").post(url, json=GUIDE).json()
    assert again["duplicate"] is True and again["seq"] == first["seq"]
    assert len(guidance(env)) == 1 and len(env.audit("comment.promoted")) == 1
    # crash between the redirect and the link: the link row is missing, the instruction exists
    env.x("DELETE FROM comment_promotions")
    healed = env.as_("lead").post(url, json=GUIDE).json()
    assert healed["seq"] == first["seq"] and len(guidance(env)) == 1  # no second instruction
    assert len(env.q("SELECT 1 FROM comment_promotions")) == 1


def test_two_simultaneous_promotes_send_one_instruction(env):
    hold(env)
    cid = env.post("eng2", "run", "r1", "race me")["id"]
    p = env.principal("lead")
    req = discussion_promote_body()

    async def go():
        return await asyncio.gather(
            discussion.promote(env.factory, p, "run", "r1", cid, req),
            discussion.promote(env.factory, p, "run", "r1", cid, req),
        )

    a, b = asyncio.run(go())
    assert a["seq"] == b["seq"] and len(guidance(env)) == 1
    assert len(env.q("SELECT 1 FROM comment_promotions")) == 1
    assert len(env.audit("comment.promoted")) == 1


def discussion_promote_body():
    return discussion.PromoteBody(action="send_as_guidance", expectedVersion=1)


def test_promote_needs_the_comment_as_the_promoter_saw_it(env):
    hold(env)
    cid = env.post("lead", "run", "r1", "looks harmless")["id"]
    env.as_("lead").patch(f"{base('run', 'r1')}/{cid}",
                          json={"body": "switched text", "expectedVersion": 1})
    stale = env.as_("lead").post(promote_url("run", "r1", cid), json=GUIDE)
    assert err(stale, 409, "STALE_COMMENT_VERSION")["currentVersion"] == 2
    assert guidance(env) == []
    ok = env.as_("lead").post(promote_url("run", "r1", cid),
                              json={"action": "send_as_guidance", "expectedVersion": 2})
    assert ok.status_code == 200 and guidance(env)[0]["content"] == "switched text"


def test_promote_refuses_hidden_and_deleted_comments_and_non_steerers(env):
    hold(env)
    a = env.post("eng2", "run", "r1", "a")["id"]
    b = env.post("eng2", "run", "r1", "b")["id"]
    hid = env.as_("boss").post(f"{base('run', 'r1')}/{a}/moderate",
                               json={"action": "hide", "reason": "not ok"})
    assert hid.status_code == 200
    env.as_("eng2").delete(f"{base('run', 'r1')}/{b}")
    for cid in (a, b):
        err(env.as_("lead").post(promote_url("run", "r1", cid), json=GUIDE), 409,
            "COMMENT_UNAVAILABLE")
    c = env.post("eng2", "run", "r1", "c")["id"]
    err(env.as_("view").post(promote_url("run", "r1", c), json=GUIDE), 403, "ROLE_DENIED")
    err(env.as_("guest").post(promote_url("run", "r1", c), json=GUIDE), 403, "ROLE_DENIED")
    assert guidance(env) == []


def test_a_non_holder_promote_is_queued_for_the_holder_like_any_guidance(env):
    hold(env, "lead")
    assert env.as_("eng2").post(f"{P}/runs/r1/join").status_code == 200
    cid = env.post("eng3", "run", "r1", "suggestion from the thread")["id"]
    out = env.as_("eng2").post(promote_url("run", "r1", cid), json=GUIDE).json()
    assert out["queued"] is True and out["seq"] is None and out["instructionId"]
    assert guidance(env) == []  # nothing reaches the agent until the holder accepts
    queued = env.q("SELECT content, state, request_id FROM control_instructions")
    assert [(q.content, q.state, q.request_id) for q in queued] == [
        ("suggestion from the thread", "queued", f"promote:{cid}")]
    again = env.as_("eng2").post(promote_url("run", "r1", cid), json=GUIDE).json()
    assert again["duplicate"] is True and len(env.q("SELECT 1 FROM control_instructions")) == 1
    acc = env.as_("lead").post(f"{P}/runs/r1/control/decide", json={
        "instructionId": out["instructionId"], "decision": "accept"})
    assert acc.status_code == 200 and guidance(env)[0]["content"] == "suggestion from the thread"


def test_promote_requires_joining_the_run_first(env):
    cid = env.post("eng3", "run", "r1", "x")["id"]
    err(env.as_("eng2").post(promote_url("run", "r1", cid), json=GUIDE), 403, "NOT_PARTICIPANT")
    assert any(r.action == "authz.denied" for r in env.audit())
    assert env.q("SELECT 1 FROM comment_promotions") == []


def test_session_comments_promote_only_into_the_sessions_own_runs(env):
    hold(env)
    cid = env.post("eng2", "session", "s1", "steer the build")["id"]
    url = promote_url("session", "s1", cid)
    for body in ({}, {"runId": "r2"}, {"runId": "r3"}, {"runId": "nope"}):
        err(env.as_("lead").post(url, json={**GUIDE, **body}), 422, "BAD_RUN")
    assert guidance(env) == []
    ok = env.as_("lead").post(url, json={**GUIDE, "runId": "r1"})
    assert ok.status_code == 200 and ok.json()["runId"] == "r1"
    assert guidance(env)[0]["content"] == "steer the build"
    run_cid = env.post("eng2", "run", "r1", "x")["id"]
    err(env.as_("lead").post(promote_url("run", "r1", run_cid), json={**GUIDE, "runId": "r2"}),
        422, "BAD_RUN")


# ----------------------------------------------------------------------- search


def test_search_covers_only_scopes_the_caller_may_see(env):
    env.post("lead", "run", "r1", "alpha needle in a run")
    env.post("lead", "session", "s1", "alpha needle in a shared session")
    env.post("lead", "session", "spriv", "alpha needle in a private session")
    env.post("evil", "run", "r3", "alpha needle in the other team")
    env.post("evil", "session", "sb", "alpha needle in the other team session")

    def hits(user, **kw):
        r = env.as_(user).get(f"{P}/discussions/search", params={"q": "needle", **kw})
        assert r.status_code == 200, r.text
        return {(x["scopeKind"], x["scopeId"]) for x in r.json()["results"]}

    assert hits("eng2") == {("run", "r1"), ("session", "s1")}  # no private, no other team
    assert hits("lead") == {("run", "r1"), ("session", "s1"), ("session", "spriv")}
    assert hits("boss") == {("run", "r1"), ("session", "s1"), ("session", "spriv")}
    assert hits("evil") == {("run", "r3"), ("session", "sb")}
    assert hits("eng3") == {("run", "r1")}  # in the team but in no session
    assert hits("lead", scopeKind="session") == {("session", "s1"), ("session", "spriv")}
    r = env.as_("eng2").get(f"{P}/discussions/search", params={"q": "needle"}).json()
    assert "SECRET TITLE" not in json.dumps(r)  # no session titles in results


def test_search_skips_hidden_deleted_and_expired_comments_and_escapes_wildcards(env):
    a = env.post("lead", "run", "r1", "Needle One 100% done")["id"]
    b = env.post("lead", "run", "r1", "needle two")["id"]
    c = env.post("lead", "run", "r1", "needle three")["id"]
    env.post("lead", "run", "r1", "unrelated")

    def found(q):
        r = env.as_("eng2").get(f"{P}/discussions/search", params={"q": q})
        return {x["commentId"] for x in r.json()["results"]}

    assert found("NEEDLE") == {a, b, c}  # case-insensitive
    assert found("100%") == {a}  # % is literal, not a wildcard
    assert found("n_edle") == set()
    env.as_("boss").post(f"{base('run', 'r1')}/{b}/moderate",
                         json={"action": "hide", "reason": "not ok"})
    env.as_("lead").delete(f"{base('run', 'r1')}/{c}")
    assert found("needle") == {a}
    assert env.as_("eng2").get(f"{P}/discussions/search", params={"q": "x"}).status_code == 422
    env.as_("boss").put(f"{P}/discussion-settings", json={"retentionDays": 30})
    env.tick(days=31)
    assert found("needle") == set()  # past retention, even before a purge job ran


def test_search_pages_newest_first_without_gaps(env):
    ids = []
    for i in range(5):
        ids.append(env.post("lead", "run", "r1", f"page {i}")["id"])
        env.tick(seconds=1)
    seen, before = [], None
    for _ in range(5):
        r = env.as_("lead").get(f"{P}/discussions/search", params={
            "q": "page", "limit": 2, **({"before": before} if before else {})}).json()
        seen += [x["commentId"] for x in r["results"]]
        before = r["nextCursor"]
        if before is None:
            break
    assert seen == ids[::-1]


def test_pilot_allowlist_hides_run_discussion_from_other_teams(env, monkeypatch):
    env.post("lead", "run", "r1", "pilot needle")
    env.post("lead", "session", "s1", "session needle")
    monkeypatch.setenv("HARNESS_PILOT_TEAMS", "team-z")
    err(env.as_("lead").get(base("run", "r1")), 404, "PILOT_NOT_ENABLED")
    assert env.as_("lead").get(base("session", "s1")).status_code == 200
    r = env.as_("lead").get(f"{P}/discussions/search", params={"q": "needle"}).json()
    assert {x["scopeKind"] for x in r["results"]} == {"session"}


# ----------------------------------------------------------------------- export


def test_export_is_own_scope_own_team_and_redacted_by_role(env):
    env.post("lead", "run", "r1", "visible text", mentions=["eng2"])
    hid = env.post("eng2", "run", "r1", "hidden text")["id"]
    env.post("lead", "run", "r2", "another scope")
    env.as_("boss").post(f"{base('run', 'r1')}/{hid}/moderate",
                         json={"action": "hide", "reason": "private"})
    # rows that must never come out: another team's comment under the same scope id, and a
    # mention of a person of another team
    env.x("INSERT INTO comments (id, scope_kind, scope_id, team_id, author_user_id,"
          " author_name_snapshot, body, created_at) VALUES ('foreign', 'run', 'r1', 'team-b',"
          " 'evil', 'Name evil', 'FOREIGN TEXT', :t)", t=support.T0)
    env.x("INSERT INTO comment_mentions VALUES"
          " ((SELECT id FROM comments WHERE body='visible text'), 'evil', :t)", t=support.T0)
    eng = env.as_("lead").get(f"{base('run', 'r1')}/export")
    assert eng.status_code == 200
    out = eng.json()
    blob = json.dumps(out)
    assert "FOREIGN TEXT" not in blob and "evil" not in blob and "another scope" not in blob
    bodies = {c["body"] for c in out["comments"]}
    assert bodies == {"visible text", None}  # a hidden comment's text stays hidden from engineers
    assert out["scope"] == {"kind": "run", "id": "r1"} and out["teamId"] == "team-a"
    assert out["count"] == 2 and out["exportedBy"] == "lead" and out["truncated"] is False
    assert "text only" in out["attachments"]
    first = next(c for c in out["comments"] if c["body"])
    assert [m["userId"] for m in first["mentions"]] == ["eng2"]
    assert first["revisions"][0]["body"] == "visible text"
    full = env.as_("boss").get(f"{base('run', 'r1')}/export").json()
    assert {c["body"] for c in full["comments"]} == {"visible text", "hidden text"}
    assert "FOREIGN TEXT" not in json.dumps(full)
    audits = env.audit("comment.exported")
    assert len(audits) == 2 and json.loads(audits[0].detail)["count"] == 2
    assert "visible text" not in audits[0].detail


def test_session_export_needs_membership_and_includes_promotion_links(env):
    hold(env)
    cid = env.post("eng2", "session", "s1", "export me")["id"]
    env.as_("lead").post(promote_url("session", "s1", cid), json={**GUIDE, "runId": "r1"})
    err(env.as_("eng3").get(f"{base('session', 's1')}/export"), 404, "NOT_FOUND")
    out = env.as_("lead").get(f"{base('session', 's1')}/export").json()
    assert out["comments"][0]["promotion"]["runId"] == "r1"


def test_export_never_includes_text_past_retention(env):
    env.post("lead", "run", "r1", "old words")
    env.as_("boss").put(f"{P}/discussion-settings", json={"retentionDays": 30})
    env.tick(days=31)
    out = env.as_("boss").get(f"{base('run', 'r1')}/export").json()
    c = out["comments"][0]
    assert c["purged"] is True and c["body"] is None and c["revisions"][0]["body"] is None


# -------------------------------------------------------------------- retention


def test_retention_is_enforced_at_read_and_a_purge_blanks_the_text(env):
    cid = env.post("lead", "run", "r1", "short lived", mentions=["eng2"])["id"]
    env.as_("lead").patch(f"{base('run', 'r1')}/{cid}", json={"body": "short lived 2",
                                                               "expectedVersion": 1})
    env.tick(days=40)
    fresh = env.post("lead", "run", "r1", "fresh")["id"]
    assert env.as_("boss").put(f"{P}/discussion-settings", json={"retentionDays": 30}
                               ).json()["retentionDays"] == 30
    rows = {c["id"]: c for c in env.listing("eng2", "run", "r1")["comments"]}
    assert rows[cid]["purged"] is True and rows[cid]["body"] is None
    assert rows[fresh]["purged"] is False and rows[fresh]["body"] == "fresh"
    err(env.as_("lead").patch(f"{base('run', 'r1')}/{cid}",
                              json={"body": "zombie", "expectedVersion": 2}), 409,
        "COMMENT_UNAVAILABLE")
    hist = env.as_("lead").get(f"{base('run', 'r1')}/{cid}/history").json()
    assert hist["purged"] is True and [r["body"] for r in hist["revisions"]] == [None, None]
    assert env.q("SELECT body FROM comments WHERE id=:c", c=cid)[0].body == "short lived 2"
    assert asyncio.run(discussion.purge_expired(env.factory)) == 1
    row = env.q("SELECT body, purged_at, author_user_id FROM comments WHERE id=:c", c=cid)[0]
    assert row.body == "" and row.purged_at is not None and row.author_user_id == "lead"
    assert [r.body for r in env.q("SELECT body FROM comment_revisions WHERE comment_id=:c",
                                  c=cid)] == ["", ""]
    assert asyncio.run(discussion.purge_expired(env.factory)) == 0  # idempotent
    purged = env.audit("comment.purged")
    assert len(purged) == 1 and json.loads(purged[0].detail)["count"] == 1
    assert env.q("SELECT body FROM comments WHERE id=:c", c=fresh)[0].body == "fresh"


def test_retention_zero_keeps_comments_forever_and_purge_respects_each_team(env):
    env.post("lead", "run", "r1", "keep me")
    env.post("evil", "run", "r3", "other team")
    env.as_("boss").put(f"{P}/discussion-settings", json={"retentionDays": 0})
    env.tick(days=4000)  # team-b keeps the 365 day default, team-a said "forever"
    assert env.listing("lead", "run", "r1")["comments"][0]["body"] == "keep me"
    assert asyncio.run(discussion.purge_expired(env.factory)) == 1
    assert env.q("SELECT body FROM comments WHERE team_id='team-a'")[0].body == "keep me"
    assert env.q("SELECT body FROM comments WHERE team_id='team-b'")[0].body == ""
    assert asyncio.run(discussion.purge_expired(env.factory, team_id="team-a")) == 0


# --------------------------------------------------------------------- settings


def test_settings_are_team_scoped_admin_written_validated_and_audited(env):
    got = env.as_("eng2").get(f"{P}/discussion-settings").json()
    assert got == {"teamId": "team-a", "editWindowSeconds": 900, "retentionDays": 365,
                   "auditSensitive": "auto", "updatedBy": None, "updatedAt": None,
                   "isDefault": True}
    err(env.as_("lead").put(f"{P}/discussion-settings", json={"retentionDays": 10}), 403,
        "ROLE_DENIED")
    for bad in ({"editWindowSeconds": -1}, {"editWindowSeconds": 86401}, {"retentionDays": -1},
                {"retentionDays": 3651}, {"auditSensitive": "never"}):
        assert env.as_("boss").put(f"{P}/discussion-settings", json=bad).status_code == 422
    out = env.as_("boss").put(f"{P}/discussion-settings", json={"editWindowSeconds": 60}).json()
    assert (out["editWindowSeconds"], out["retentionDays"], out["updatedBy"]) == (60, 365, "boss")
    n = len(env.audit("discussion.settings_changed"))
    env.as_("boss").put(f"{P}/discussion-settings", json={"editWindowSeconds": 60})
    assert len(env.audit("discussion.settings_changed")) == n  # no-op writes no audit
    assert env.as_("evil").get(f"{P}/discussion-settings").json()["editWindowSeconds"] == 900
    row = json.loads(env.audit("discussion.settings_changed")[0].detail)
    assert row["before"]["editWindowSeconds"] == 900 and row["after"]["editWindowSeconds"] == 60


# ------------------------------------------------------------------------ races


def test_two_simultaneous_edits_with_one_expected_version_cannot_both_win(env):
    cid = env.post("lead", "run", "r1", "base")["id"]
    p = env.principal("lead")

    def body(text):
        return discussion.EditBody(body=text, expectedVersion=1)

    async def go():
        return await asyncio.gather(
            discussion.edit(env.factory, p, "run", "r1", cid, body("one")),
            discussion.edit(env.factory, p, "run", "r1", cid, body("two")),
            return_exceptions=True,
        )

    results = asyncio.run(go())
    wins = [r for r in results if isinstance(r, dict)]
    losses = [r for r in results if not isinstance(r, dict)]
    assert len(wins) == 1 and len(losses) == 1
    assert losses[0].code == "STALE_COMMENT_VERSION"
    assert len(env.q("SELECT 1 FROM comment_revisions WHERE comment_id=:c", c=cid)) == 2


def test_two_simultaneous_creates_with_one_request_id_make_one_comment(env):
    p = env.principal("lead")

    def req():
        return discussion.CreateBody(body="same", requestId="rq-race")

    async def go():
        return await asyncio.gather(
            discussion.create(env.factory, p, "run", "r1", req()),
            discussion.create(env.factory, p, "run", "r1", req()),
        )

    (a, _), (b, _) = asyncio.run(go())
    assert a["id"] == b["id"] and len(env.q("SELECT 1 FROM comments")) == 1


def test_another_teams_row_under_the_same_scope_id_is_invisible_and_untouchable(env):
    env.x("INSERT INTO comments (id, scope_kind, scope_id, team_id, author_user_id,"
          " author_name_snapshot, body, created_at) VALUES ('foreign', 'run', 'r1', 'team-b',"
          " 'lead', 'x', 'FOREIGN TEXT', :t)", t=support.T0)
    url = f"{base('run', 'r1')}/foreign"
    assert env.listing("lead", "run", "r1")["comments"] == []
    for call in (env.as_("lead").patch(url, json={"body": "y", "expectedVersion": 1}),
                 env.as_("lead").delete(url), env.as_("lead").get(url + "/history"),
                 env.as_("lead").post(url + "/reactions", json={"reaction": "heart"}),
                 env.as_("boss").post(url + "/moderate", json={"action": "hide", "reason": "abc"})):
        err(call, 404, "COMMENT_NOT_FOUND")
    assert env.q("SELECT body, state FROM comments WHERE id = 'foreign'")[0].body == "FOREIGN TEXT"


def test_openapi_has_exactly_the_contracted_discussion_paths(env):
    paths = env.client.app.openapi()["paths"]
    mine = {p: sorted(m for m in ops) for p, ops in paths.items()
            if "comments" in p or "discussion" in p}
    expected = {}
    for scope in ("runs", "sessions"):
        b = f"{P}/{scope}/{{scopeId}}/comments"
        expected[b] = ["get", "post"]
        expected[b + "/export"] = ["get"]
        expected[b + "/{commentId}"] = ["delete", "patch"]
        expected[b + "/{commentId}/history"] = ["get"]
        for tail in ("reactions", "promote", "moderate"):
            expected[f"{b}/{{commentId}}/{tail}"] = ["post"]
    expected[f"{P}/discussions/search"] = ["get"]
    expected[f"{P}/discussion-settings"] = ["get", "put"]
    assert mine == expected


def test_every_discussion_route_has_the_policy_permission_the_lead_classified(env):
    from harness_contracts.policy_enforce import ORCHESTRATOR_RULES, classify

    want = {
        ("GET", ""): "read", ("POST", ""): "comment", ("GET", "/export"): "export",
        ("PATCH", "/c1"): "comment", ("DELETE", "/c1"): "comment", ("GET", "/c1/history"): "read",
        ("POST", "/c1/reactions"): "comment", ("POST", "/c1/promote"): "steer",
        ("POST", "/c1/moderate"): "administer",
    }
    for scope in ("run", "session"):
        for (method, tail), perm in want.items():
            assert classify(ORCHESTRATOR_RULES, method, base(scope, "x1") + tail) == perm, (
                scope, method, tail)
    assert classify(ORCHESTRATOR_RULES, "GET", f"{P}/discussions/search") == "read"
    assert classify(ORCHESTRATOR_RULES, "GET", f"{P}/discussion-settings") == "read"
    assert classify(ORCHESTRATOR_RULES, "PUT", f"{P}/discussion-settings") == "administer"


def test_the_migration_is_rerunnable_and_uses_no_postgres_hostile_syntax(env):
    sql = "\n".join(ln for ln in (support.MIGRATIONS / "0045_discussion.sql").read_text()
                    .splitlines() if not ln.lstrip().startswith("--"))
    assert "ADD COLUMN IF NOT EXISTS" not in sql and "$$" not in sql and "now()" not in sql
    mine = [s for s in support.migration_statements() if "discussion_settings" in s
            or " comment" in s.lower()]

    async def again(db):
        from sqlalchemy import text

        for stmt in mine:
            await db.execute(text(stmt))

    env.run(again)  # IF NOT EXISTS everywhere: a second run changes nothing
    assert env.q("SELECT 1 FROM comments") == []


def test_mentions_land_in_lane_ns_real_inbox_with_ids_only(env, monkeypatch):
    """The contract with lane N's notify.emit, end to end: real module, real migration 0046."""
    import importlib
    import sys

    from sqlalchemy import text

    monkeypatch.delitem(sys.modules, "harness_orchestrator.notify", raising=False)
    monkeypatch.delattr(support.harness_orchestrator, "notify", raising=False)
    real = importlib.import_module("harness_orchestrator.notify")
    body = "\n".join(ln for ln in (support.MIGRATIONS / "0046_notifications.sql").read_text()
                     .splitlines() if not ln.lstrip().startswith("--"))

    async def setup(db):
        for stmt in (s.strip() for s in body.split(";")):
            if stmt:
                await db.execute(text(stmt))
        await db.commit()

    env.run(setup)
    out = env.post("lead", "session", "s1", "a body with SECRET TEXT", mentions=["eng2"])
    rows = env.q("SELECT user_id, kind, source_kind, source_id, session_id, summary_code,"
                 " state FROM notification_inbox")
    assert [(r.user_id, r.kind, r.source_kind, r.source_id, r.session_id, r.state)
            for r in rows] == [("eng2", "mention", "comment", out["id"], "s1", "unread")]
    assert rows[0].summary_code == "mentioned" and real.KINDS
    dump = json.dumps([tuple(r) for r in env.q("SELECT * FROM notification_inbox")], default=str)
    assert "SECRET TEXT" not in dump and "SECRET TITLE" not in dump
