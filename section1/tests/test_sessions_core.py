"""Sessions: create, idempotency, visibility (404), members, leaving, lost access, patch."""

import pytest
from sqlalchemy import text

import harness_identity.sessions as sessions_mod
from tests.test_sessions_support import (
    S,
    add_member,
    audit_actions,
    bearer,
    create_session,
    drop_team_membership,
    err,
    make_env,
    seed_team,
)


@pytest.fixture
async def env():
    async with make_env() as (client, factory):
        team = await seed_team(factory, ("admin@a.io", "admin"), ("eng@a.io", "engineer"),
                               ("eng2@a.io", "engineer"), ("view@a.io", "viewer"),
                               ("eng3@a.io", "engineer"))
        other = await seed_team(factory, ("admin@b.io", "admin"), ("eng@b.io", "engineer"),
                                name="other")
        yield client, factory, team, other


async def test_create_owner_member_audit_defaults(env):
    client, factory, team, _ = env
    s = await create_session(client, team["eng@a.io"], "Alpha")
    assert s["state"] == "active" and s["ownerUserId"] == team["eng@a.io"]["userId"]
    assert s["leaderUserId"] is None and s["version"] == 1 and s["retentionDays"] == 30
    r = await client.get(f"{S}/{s['id']}/members", headers=bearer(team["eng@a.io"]))
    (m,) = r.json()["items"]
    assert m["role"] == "owner" and m["status"] == "active" and m["displayName"] == "eng"
    assert len(await audit_actions(factory, "session.created")) == 1


async def test_viewer_cannot_create_unauth_and_title_validation(env):
    client, _, team, _ = env
    r = await client.post(S, json={"title": "Nope"}, headers=bearer(team["view@a.io"]))
    assert r.status_code == 403
    assert (await client.post(S, json={"title": "Nope"})).status_code == 401
    for bad in ("", " ", "a", "x" * 121, "bad\x00title", "tab\ttitle"):
        r = await client.post(S, json={"title": bad}, headers=bearer(team["eng@a.io"]))
        assert r.status_code == 422, bad


async def test_create_is_idempotent_with_client_request_id(env):
    client, factory, team, _ = env
    eng = team["eng@a.io"]
    a = await client.post(S, json={"title": "Once", "clientRequestId": "req-1"}, headers=bearer(eng))  # noqa: E501
    b = await client.post(S, json={"title": "Once", "clientRequestId": "req-1"}, headers=bearer(eng))  # noqa: E501
    assert (a.status_code, b.status_code) == (201, 200) and a.json()["id"] == b.json()["id"]
    assert len(await audit_actions(factory, "session.created")) == 1
    other = await client.post(S, json={"title": "Once", "clientRequestId": "req-1"},
                              headers=bearer(team["eng2@a.io"]))
    assert other.json()["id"] != a.json()["id"]  # the key is per owner


async def test_visibility_is_member_only_and_never_an_oracle(env):
    client, _, team, other = env
    eng = team["eng@a.io"]
    s = await create_session(client, eng, "Secret")
    sid = s["id"]
    for actor in (team["eng2@a.io"], other["admin@b.io"], other["eng@b.io"]):
        for path in ("", "/members", "/runs", "/resume"):
            r = await client.get(f"{S}/{sid}{path}", headers=bearer(actor))
            assert r.status_code == 404 and err(r) == "NOT_FOUND", (actor["userId"], path)
    missing = await client.get(f"{S}/nope", headers=bearer(team["eng2@a.io"]))
    unseen = await client.get(f"{S}/{sid}", headers=bearer(team["eng2@a.io"]))
    assert missing.json() == unseen.json()  # same envelope for unknown and foreign
    assert (await client.get(f"{S}/{sid}", headers=bearer(team["admin@a.io"]))).status_code == 200
    mine = await client.get(S, headers=bearer(eng))
    assert [i["id"] for i in mine.json()["items"]] == [sid]
    assert (await client.get(S, headers=bearer(team["eng2@a.io"]))).json()["items"] == []
    assert (await client.get(S, headers=bearer(other["admin@b.io"]))).json()["items"] == []


async def test_list_filters_and_cursor(env):
    client, _, team, _ = env
    eng = team["eng@a.io"]
    ids = [(await create_session(client, eng, f"Item {i}"))["id"] for i in range(3)]
    await client.post(f"{S}/{ids[0]}/pause", headers=bearer(eng))
    r = await client.get(S, params={"limit": 2}, headers=bearer(eng))
    page1 = r.json()
    assert len(page1["items"]) == 2 and page1["nextCursor"]
    r2 = await client.get(S, params={"limit": 2, "cursor": page1["nextCursor"]}, headers=bearer(eng))  # noqa: E501
    seen = [i["id"] for i in page1["items"]] + [i["id"] for i in r2.json()["items"]]
    assert sorted(seen) == sorted(ids) and r2.json()["nextCursor"] is None
    paused = await client.get(S, params={"state": "paused"}, headers=bearer(eng))
    assert [i["id"] for i in paused.json()["items"]] == [ids[0]]
    found = await client.get(S, params={"q": "item 2"}, headers=bearer(eng))
    assert [i["id"] for i in found.json()["items"]] == [ids[2]]
    assert (await client.get(S, params={"state": "bogus"}, headers=bearer(eng))).status_code == 422


async def test_members_users_agents_and_validation(env):
    client, factory, team, other = env
    eng, e2 = team["eng@a.io"], team["eng2@a.io"]
    sid = (await create_session(client, eng))["id"]
    url = f"{S}/{sid}/members"
    first = await client.post(url, json={"userId": e2["userId"], "role": "observer"},
                              headers=bearer(eng))
    again = await client.post(url, json={"userId": e2["userId"], "role": "observer"},
                              headers=bearer(eng))
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["changed"] is False
    assert len(await audit_actions(factory, "session.member_joined")) == 1
    r = await client.post(url, json={"agentId": "agent:reviewer-1", "displayName": "Reviewer"},
                          headers=bearer(eng))
    assert r.status_code == 201
    roster = (await client.get(url, headers=bearer(eng))).json()["items"]
    agent = next(m for m in roster if m["kind"] == "agent")
    assert agent["agentId"] == "agent:reviewer-1" and agent["role"] == "agent"
    assert agent["userId"] is None and agent["accessLost"] is False
    for body, code in (
        ({}, 422), ({"userId": e2["userId"], "agentId": "a"}, 422),
        ({"userId": "ghost-user"}, 422), ({"userId": e2["userId"], "role": "owner"}, 422),
        ({"agentId": "bad id!"}, 422),
    ):
        r = await client.post(url, json=body, headers=bearer(eng))
        assert r.status_code == code, body
    # not a member of the owning team: the other team's user cannot be added
    r = await client.post(url, json={"userId": other["admin@b.io"]["userId"]},
                          headers=bearer(eng))
    assert r.status_code == 422 and err(r) == "NOT_A_TEAM_MEMBER"
    # only the owner/admin manages members
    r = await client.post(url, json={"userId": team["eng3@a.io"]["userId"]}, headers=bearer(e2))
    assert r.status_code == 403


async def test_member_role_change_owner_fixed_and_leader_rules(env):
    client, factory, team, _ = env
    eng, e2, e3 = team["eng@a.io"], team["eng2@a.io"], team["eng3@a.io"]
    sid = (await create_session(client, eng))["id"]
    await add_member(client, eng, sid, e2)
    await add_member(client, eng, sid, e3, "observer")
    url = f"{S}/{sid}"
    r = await client.put(f"{url}/members/{eng['userId']}", json={"role": "observer"},
                         headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "OWNER_ROLE_FIXED"
    r = await client.patch(url, json={"leaderUserId": e3["userId"]}, headers=bearer(eng))
    assert r.status_code == 422 and err(r) == "LEADER_NOT_ELIGIBLE"  # observer
    r = await client.patch(url, json={"leaderUserId": "stranger"}, headers=bearer(eng))
    assert r.status_code == 422
    r = await client.patch(url, json={"leaderUserId": e2["userId"]}, headers=bearer(eng))
    assert r.status_code == 200 and r.json()["leaderUserId"] == e2["userId"]
    r = await client.put(f"{url}/members/{e2['userId']}", json={"role": "observer"},
                         headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "LEADER_MUST_PARTICIPATE"
    roster = (await client.get(f"{url}/members", headers=bearer(eng))).json()["items"]
    assert [m["userId"] for m in roster if m["isLeader"]] == [e2["userId"]]
    r = await client.patch(url, json={"leaderUserId": None}, headers=bearer(eng))
    assert r.json()["leaderUserId"] is None  # explicit null clears
    r = await client.put(f"{url}/members/{e3['userId']}", json={"role": "participant"},
                         headers=bearer(eng))
    assert r.json()["changed"] is True
    r = await client.put(f"{url}/members/nobody", json={"role": "participant"}, headers=bearer(eng))
    assert r.status_code == 404
    r = await client.put(f"{url}/members/{e3['userId']}", json={"role": "boss"}, headers=bearer(eng))  # noqa: E501
    assert r.status_code == 422


async def test_leaving_keeps_history_and_snapshot_then_rejoin(env):
    client, factory, team, _ = env
    eng, e2 = team["eng@a.io"], team["eng2@a.io"]
    sid = (await create_session(client, eng))["id"]
    await add_member(client, eng, sid, e2)
    await client.patch(f"{S}/{sid}", json={"leaderUserId": e2["userId"]}, headers=bearer(eng))
    r = await client.delete(f"{S}/{sid}/members/{e2['userId']}", headers=bearer(e2))  # self-leave
    assert r.status_code == 204
    # the leaver can no longer read it ...
    assert (await client.get(f"{S}/{sid}", headers=bearer(e2))).status_code == 404
    # ... but the row, its name snapshot and the audit trail stay
    roster = (await client.get(f"{S}/{sid}/members", headers=bearer(eng))).json()["items"]
    gone = next(m for m in roster if m["userId"] == e2["userId"])
    assert gone["status"] == "left" and gone["displayName"] == "eng2" and gone["leftAt"]
    assert (await client.get(f"{S}/{sid}", headers=bearer(eng))).json()["leaderUserId"] is None
    (ev,) = await audit_actions(factory, "session.member_left")
    assert ev.detail["leaderCleared"] is True
    again = await client.delete(f"{S}/{sid}/members/{e2['userId']}", headers=bearer(e2))
    assert again.status_code == 404  # no longer visible to e2: still no oracle
    await add_member(client, eng, sid, e2, "observer")  # rejoin reactivates the SAME row
    roster = (await client.get(f"{S}/{sid}/members", headers=bearer(eng))).json()["items"]
    assert len([m for m in roster if m["userId"] == e2["userId"]]) == 1
    assert next(m for m in roster if m["userId"] == e2["userId"])["status"] == "active"
    assert (await client.get(f"{S}/{sid}", headers=bearer(e2))).status_code == 200


async def test_manager_removal_owner_cannot_leave_and_permissions(env):
    client, factory, team, _ = env
    eng, e2, e3 = team["eng@a.io"], team["eng2@a.io"], team["eng3@a.io"]
    sid = (await create_session(client, eng))["id"]
    await add_member(client, eng, sid, e2)
    await add_member(client, eng, sid, e3)
    r = await client.delete(f"{S}/{sid}/members/{e3['userId']}", headers=bearer(e2))
    assert r.status_code == 403  # participants cannot remove others
    r = await client.delete(f"{S}/{sid}/members/{eng['userId']}", headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "OWNER_CANNOT_LEAVE"
    r = await client.delete(f"{S}/{sid}/members/{e3['userId']}", headers=bearer(eng))
    assert r.status_code == 204
    roster = (await client.get(f"{S}/{sid}/members", params={"status": "removed"},
                               headers=bearer(eng))).json()["items"]
    assert [m["userId"] for m in roster] == [e3["userId"]]
    assert len(await audit_actions(factory, "session.member_removed")) == 1
    r = await client.delete(f"{S}/{sid}/members/ghost", headers=bearer(eng))
    assert r.status_code == 404
    r = await client.delete(f"{S}/{sid}/members/agent-x", headers=bearer(eng))
    assert r.status_code == 404


async def test_losing_team_access_keeps_member_row_and_flags_it(env):
    client, factory, team, _ = env
    eng, e2 = team["eng@a.io"], team["eng2@a.io"]
    sid = (await create_session(client, eng))["id"]
    await add_member(client, eng, sid, e2)
    await drop_team_membership(factory, team["teamId"], e2["userId"])
    roster = (await client.get(f"{S}/{sid}/members", headers=bearer(eng))).json()["items"]
    lost = next(m for m in roster if m["userId"] == e2["userId"])
    assert lost["accessLost"] is True and lost["displayName"] == "eng2" and lost["status"] == "active"  # noqa: E501
    ok = next(m for m in roster if m["userId"] == eng["userId"])
    assert ok["accessLost"] is False


async def test_member_cap(env, monkeypatch):
    client, _, team, _ = env
    monkeypatch.setattr(sessions_mod, "MAX_MEMBERS", 2)
    eng = team["eng@a.io"]
    sid = (await create_session(client, eng))["id"]
    await add_member(client, eng, sid, team["eng2@a.io"])
    r = await client.post(f"{S}/{sid}/members", json={"userId": team["eng3@a.io"]["userId"]},
                          headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "MEMBER_LIMIT_REACHED"


async def test_patch_version_noop_audit_and_authz(env):
    client, factory, team, _ = env
    eng, e2 = team["eng@a.io"], team["eng2@a.io"]
    sid = (await create_session(client, eng, "Before"))["id"]
    await add_member(client, eng, sid, e2)
    url = f"{S}/{sid}"
    r = await client.patch(url, json={"title": "After", "expectedVersion": 1}, headers=bearer(eng))
    assert r.json()["title"] == "After" and r.json()["version"] == 2
    r = await client.patch(url, json={"title": "Stale", "expectedVersion": 1}, headers=bearer(eng))
    assert r.status_code == 409 and err(r) == "VERSION_CONFLICT"
    r = await client.patch(url, json={"title": "After"}, headers=bearer(eng))
    assert r.json()["version"] == 2  # no-op: no bump
    (ev,) = await audit_actions(factory, "session.updated")
    assert ev.detail["before"] == {"title": "Before"} and ev.detail["after"] == {"title": "After"}
    assert (await client.patch(url, json={"title": "Hax"}, headers=bearer(e2))).status_code == 403
    assert (await client.patch(url, json={"retentionDays": 0}, headers=bearer(eng))).status_code == 422  # noqa: E501
    r = await client.patch(url, json={"retentionDays": 90}, headers=bearer(team["admin@a.io"]))
    assert r.json()["retentionDays"] == 90  # admin manages without being a member


async def test_workspace_link_rules(env):
    client, factory, team, other = env
    eng, e2 = team["eng@a.io"], team["eng2@a.io"]
    now = "2026-01-01T00:00:00+00:00"

    async def ws(wid, team_id, state="active", vis="team"):
        async with factory() as db:
            await db.execute(text(
                "INSERT INTO workspaces (id, team_id, name, name_key, state, visibility,"
                " owner_user_id, created_at, updated_at) VALUES (:i,:t,:n,:n,:s,:v,'u',:n2,:n2)"),
                {"i": wid, "t": team_id, "n": wid, "s": state, "v": vis, "n2": now})
            await db.commit()

    await ws("w-ok", team["teamId"])
    await ws("w-other", other["teamId"])
    await ws("w-paused", team["teamId"], "paused")
    await ws("w-priv", team["teamId"], vis="private")
    ok = await client.post(S, json={"title": "In ws", "workspaceId": "w-ok"}, headers=bearer(eng))
    assert ok.status_code == 201 and ok.json()["workspaceId"] == "w-ok"
    for wid, status, code in (("w-other", 404, "NOT_FOUND"), ("nope", 404, "NOT_FOUND"),
                              ("w-priv", 404, "NOT_FOUND"),
                              ("w-paused", 409, "WORKSPACE_NOT_ACTIVE")):
        r = await client.post(S, json={"title": "Bad ws", "workspaceId": wid}, headers=bearer(e2))
        assert (r.status_code, err(r)) == (status, code), wid
    r = await client.get(S, params={"workspaceId": "w-ok"}, headers=bearer(eng))
    assert [i["id"] for i in r.json()["items"]] == [ok.json()["id"]]
