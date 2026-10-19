"""Part 47 inbox: isolation, paging, states, snooze with a fake clock, no sensitive preview,
and the link that re-checks authorization on every open (Session 5 lane N)."""

import json
from itertools import chain

import pytest
from sqlalchemy import text

from harness_identity import notifications
from tests.test_notifications_support import (
    T0,
    N,
    add_member,
    audit_actions,
    bearer,
    create_session,
    create_ws,
    err,
    make_env,
    seed_item,
    seed_project,
    seed_run,
    seed_team,
)

H = bearer


@pytest.fixture
async def env(monkeypatch):
    async with make_env(monkeypatch) as (client, factory, clock):
        team = await seed_team(factory, ("admin@a.io", "admin"), ("eng@a.io", "engineer"),
                               ("eng2@a.io", "engineer"), ("view@a.io", "viewer"))
        other = await seed_team(factory, ("eve@b.io", "admin"), name="other")
        yield client, factory, clock, team, other


# ------------------------------------------------------------------ vocabulary pins


def test_closed_vocabulary_is_pinned():
    assert notifications.KINDS == ("approval", "mention", "handoff", "failure", "blocked",
                                   "control_request", "suggestion")
    codes = set(chain.from_iterable(notifications.SUMMARY_CODES.values()))
    assert codes == set(notifications.PREVIEWS)  # every code has fixed preview text
    assert set(notifications.SUMMARY_CODES) == set(notifications.KINDS)


# ------------------------------------------------------------------ isolation


async def test_inbox_is_private_to_the_user_and_the_team(env):
    client, factory, _, team, other = env
    eng, eng2, eve = team["eng@a.io"], team["eng2@a.io"], other["eve@b.io"]
    mine = await seed_item(factory, eng)
    theirs = await seed_item(factory, eng2)
    assert [i["id"] for i in (await client.get(N, headers=H(eng))).json()["items"]] == [mine]
    assert (await client.get(N, headers=H(eve))).json()["items"] == []
    # another person in the SAME team and a person in ANOTHER team get the same 404 as for an
    # id that never existed: no existence oracle on any per-item route
    for who in (eng2, eve):
        for verb in ("read", "dismiss"):
            r = await client.post(f"{N}/{mine}/{verb}", headers=H(who))
            assert r.status_code == 404 and err(r) == "NOT_FOUND"
        r = await client.post(f"{N}/{mine}/snooze", json={"minutes": 5}, headers=H(who))
        assert r.status_code == 404
        r = await client.get(f"{N}/link/{mine}", headers=H(who))
        assert r.status_code == 404
    missing = await client.get(f"{N}/link/does-not-exist", headers=H(eng))
    foreign = await client.get(f"{N}/link/{mine}", headers=H(eng2))
    assert missing.json() == foreign.json()
    assert (await client.get(f"{N}/unread-count", headers=H(eve))).json()["unread"] == 0
    assert (await client.get(f"{N}/unread-count", headers=H(eng))).json()["unread"] == 1
    assert theirs  # untouched by eng
    assert (await client.get(f"{N}/unread-count", headers=H(eng2))).json()["unread"] == 1


async def test_viewers_have_an_inbox_too(env):
    client, factory, _, team, _ = env
    view = team["view@a.io"]
    await seed_item(factory, view, kind="failure", source_kind="run")
    assert (await client.get(f"{N}/unread-count", headers=H(view))).json()["unread"] == 1


# ------------------------------------------------------------------ paging and filters


async def test_cursor_paging_has_no_gaps_or_duplicates(env):
    client, factory, _, team, _ = env
    eng = team["eng@a.io"]
    from datetime import timedelta
    ids = [await seed_item(factory, eng, created=T0 + timedelta(minutes=i)) for i in range(5)]
    ids.append(await seed_item(factory, eng, created=T0 + timedelta(minutes=4)))  # same instant
    seen, cursor = [], None
    for _ in range(10):
        r = await client.get(N, params={"limit": 2, **({"cursor": cursor} if cursor else {})},
                             headers=H(eng))
        assert r.status_code == 200
        body = r.json()
        seen += [i["id"] for i in body["items"]]
        cursor = body["nextCursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen)) == 6
    updated = [i["updatedAt"] for i in (await client.get(N, params={"limit": 100},
                                                         headers=H(eng))).json()["items"]]
    assert updated == sorted(updated, reverse=True)
    for bad in ({"cursor": "%%%"}, {"limit": 0}, {"limit": 101}, {"kind": "nope"},
                {"state": "weird"}):
        assert (await client.get(N, params=bad, headers=H(eng))).status_code == 422


async def test_filters_unread_kind_state_and_dismissed_hidden_by_default(env):
    client, factory, _, team, _ = env
    eng = team["eng@a.io"]
    a = await seed_item(factory, eng, kind="approval")
    m = await seed_item(factory, eng, kind="mention", source_kind="comment")
    r_ = await seed_item(factory, eng, kind="handoff", state="read", source_kind="handoff")
    d = await seed_item(factory, eng, kind="failure", state="dismissed", source_kind="run")

    async def ids(**params):
        r = await client.get(N, params=params, headers=H(eng))
        return {i["id"] for i in r.json()["items"]}

    assert await ids() == {a, m, r_}
    assert await ids(unread="true") == {a, m}
    assert await ids(kind="mention") == {m}
    assert await ids(state="dismissed") == {d}  # kept, just out of the way
    assert await ids(state="read") == {r_}


# ------------------------------------------------------------------ states, snooze, clock


async def test_read_dismiss_snooze_state_machine(env):
    client, factory, clock, team, _ = env
    eng = team["eng@a.io"]
    nid = await seed_item(factory, eng)
    assert (await client.post(f"{N}/{nid}/read", headers=H(eng))).json()["state"] == "read"
    assert (await client.post(f"{N}/{nid}/read", headers=H(eng))).json()["state"] == "read"
    r = await client.post(f"{N}/{nid}/snooze", json={"minutes": 30}, headers=H(eng))
    assert r.status_code == 200 and r.json()["state"] == "snoozed"
    assert r.json()["snoozedUntil"] == "2030-01-01T12:30:00+00:00"
    d = await client.post(f"{N}/{nid}/dismiss", headers=H(eng))
    assert d.json()["state"] == "dismissed"
    assert (await client.post(f"{N}/{nid}/read", headers=H(eng))).json()["state"] == "dismissed"
    r = await client.post(f"{N}/{nid}/snooze", json={"minutes": 5}, headers=H(eng))
    assert r.status_code == 409 and err(r) == "ILLEGAL_STATE"
    # a dismissed row is kept (audit and history), never deleted
    assert (await row_count(factory)) == 1


async def row_count(factory) -> int:
    async with factory() as db:
        return (await db.execute(text("SELECT COUNT(*) FROM notification_inbox"))).scalar()


async def test_snooze_validation_and_return_with_fake_clock(env):
    client, factory, clock, team, _ = env
    eng = team["eng@a.io"]
    nid = await seed_item(factory, eng, urgent=True)
    for body in ({}, {"minutes": 5, "until": "2030-01-01T13:00:00+00:00"}, {"minutes": 0},
                 {"until": "2029-12-31T00:00:00+00:00"}, {"until": "2031-06-01T00:00:00+00:00"},
                 {"minutes": 5, "extra": 1}):
        r = await client.post(f"{N}/{nid}/snooze", json=body, headers=H(eng))
        assert r.status_code == 422, body
    assert (await client.post(f"{N}/{nid}/snooze", json={"minutes": 60},
                              headers=H(eng))).status_code == 200
    count = lambda: client.get(f"{N}/unread-count", headers=H(eng))  # noqa: E731
    assert (await count()).json() == {"unread": 0, "urgentUnread": 0}
    listed = await client.get(N, headers=H(eng))
    assert listed.json()["items"] == []  # snoozed items leave the list
    snoozed = await client.get(N, params={"state": "snoozed"}, headers=H(eng))
    assert [i["id"] for i in snoozed.json()["items"]] == [nid]
    clock.advance(minutes=59)
    assert (await count()).json()["unread"] == 0
    clock.advance(minutes=2)
    assert (await count()).json() == {"unread": 1, "urgentUnread": 1}
    back = (await client.get(N, headers=H(eng))).json()["items"]
    assert back[0]["id"] == nid and back[0]["state"] == "unread"
    snoozes = await audit_actions(factory, "notification.snoozed")
    assert len(snoozes) == 1 and snoozes[0].detail["kind"] == "approval"
    assert "summary" not in json.dumps(snoozes[0].detail)


async def test_read_all_only_touches_my_live_items(env):
    client, factory, _, team, _ = env
    eng, eng2 = team["eng@a.io"], team["eng2@a.io"]
    a = await seed_item(factory, eng, kind="approval")
    m = await seed_item(factory, eng, kind="mention", source_kind="comment")
    other = await seed_item(factory, eng2)
    r = await client.post(f"{N}/read-all", json={"kind": "mention"}, headers=H(eng))
    assert r.json() == {"updated": 1}
    assert (await client.get(f"{N}/unread-count", headers=H(eng))).json()["unread"] == 1
    assert (await client.post(f"{N}/read-all", headers=H(eng))).json() == {"updated": 1}
    assert (await client.post(f"{N}/read-all", headers=H(eng))).json() == {"updated": 0}
    assert (await client.get(f"{N}/unread-count", headers=H(eng2))).json()["unread"] == 1
    assert {a, m, other}
    r = await client.post(f"{N}/read-all", json={"kind": "bogus"}, headers=H(eng))
    assert r.status_code == 422


# ------------------------------------------------------------------ no sensitive preview (006)


async def test_preview_is_fixed_text_never_content(env):
    client, factory, _, team, _ = env
    eng = team["eng@a.io"]
    secret = "Project Falcon layoffs plan"
    sess = await create_session(client, eng, title=secret, description="confidential brief")
    nid = await seed_item(factory, eng, kind="mention", source_kind="comment",
                          source_id="comment-77", session_id=sess["id"], count=3)
    body = (await client.get(N, headers=H(eng))).json()
    blob = json.dumps(body)
    assert secret not in blob and "confidential" not in blob
    item = body["items"][0]
    assert item["id"] == nid and item["preview"] == "You were mentioned 3 times"
    assert item["summaryCode"] == "mentioned" and item["count"] == 3
    link = (await client.get(f"{N}/link/{nid}", headers=H(eng))).json()
    assert secret not in json.dumps(link)
    for codes in notifications.SUMMARY_CODES.values():
        for code in codes:
            assert "{n}" not in notifications._preview(code, 5)
            assert "_" not in notifications._preview(code, 1)  # prose, not a raw code


# ------------------------------------------------------------------ link re-check (007)


async def test_link_returns_the_run_path_then_404_after_access_is_lost(env):
    client, factory, _, team, _ = env
    eng, eng2 = team["eng@a.io"], team["eng2@a.io"]
    project = await seed_project(factory, team["teamId"])
    run = await seed_run(factory, project)
    sess = await create_session(client, eng)
    await add_member(client, eng, sess["id"], eng2)
    r = await client.post(f"/api/v1/sessions/{sess['id']}/runs", json={"runId": run},
                          headers=H(eng))
    assert r.status_code == 201, r.text
    nid = await seed_item(factory, eng2, run_id=run, session_id=sess["id"], source_id="ap-1")
    ok = await client.get(f"{N}/link/{nid}", headers=H(eng2))
    assert ok.status_code == 200
    assert ok.json() == {"notificationId": nid,
                         "path": f"/projects/{project}/runs/{run}?focus=approval:ap-1"}
    # access is lost: the member is removed from the session
    gone = await client.delete(f"/api/v1/sessions/{sess['id']}/members/{eng2['userId']}",
                               headers=H(eng))
    assert gone.status_code in (200, 204), gone.text
    after = await client.get(f"{N}/link/{nid}", headers=H(eng2))
    assert after.status_code == 404 and err(after) == "NOT_FOUND"
    # the same notification still sits in the inbox (history), but opening it is refused
    assert [i["id"] for i in (await client.get(N, headers=H(eng2))).json()["items"]] == [nid]
    # a run that is derived from the session alone is re-checked too (no session id on the row)
    nid2 = await seed_item(factory, eng2, run_id=run)
    assert (await client.get(f"{N}/link/{nid2}", headers=H(eng2))).status_code == 404


async def test_link_for_session_workspace_and_deleted_scopes(env):
    client, factory, _, team, _ = env
    eng, eng2 = team["eng@a.io"], team["eng2@a.io"]
    sess = await create_session(client, eng)
    ws = await create_ws(client, eng, "Private WS", visibility="private")
    s_nid = await seed_item(factory, eng, session_id=sess["id"], source_kind="session")
    w_nid = await seed_item(factory, eng, workspace_id=ws["id"], source_kind="workspace")
    t_nid = await seed_item(factory, eng, source_kind="team")
    assert (await client.get(f"{N}/link/{s_nid}", headers=H(eng))).json()["path"] == \
        f"/sessions/{sess['id']}"
    assert (await client.get(f"{N}/link/{w_nid}", headers=H(eng))).json()["path"] == \
        f"/workspaces/{ws['id']}"
    assert (await client.get(f"{N}/link/{t_nid}", headers=H(eng))).json()["path"] == \
        "/notifications"
    # a person who is not in the private workspace / session never gets a path
    s2 = await seed_item(factory, eng2, session_id=sess["id"], source_kind="session")
    w2 = await seed_item(factory, eng2, workspace_id=ws["id"], source_kind="workspace")
    for nid in (s2, w2):
        assert (await client.get(f"{N}/link/{nid}", headers=H(eng2))).status_code == 404
    # a session that was archived and deleted is not reachable for a non-admin
    assert (await client.post(f"/api/v1/sessions/{sess['id']}/archive", json={},
                              headers=H(eng))).status_code == 200
    r = await client.post(f"/api/v1/sessions/{sess['id']}/delete", json={"reason": "cleanup"},
                          headers=H(team["admin@a.io"]))
    assert r.status_code == 200, r.text
    assert (await client.get(f"{N}/link/{s_nid}", headers=H(eng))).status_code == 404
