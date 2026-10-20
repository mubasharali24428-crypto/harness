"""Part 47 preferences, precedence, mute and unsubscribe (Session 5 lane N)."""

import json
from datetime import timedelta

import pytest
from harness_contracts import hash_token
from sqlalchemy import text

from harness_identity.notifications import resolve
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
    put_prefs,
    seed_item,
    seed_project,
    seed_run,
    seed_team,
)

H = bearer
SCOPE = "/preferences/scopes"


@pytest.fixture
async def env(monkeypatch):
    async with make_env(monkeypatch) as (client, factory, clock):
        team = await seed_team(factory, ("admin@a.io", "admin"), ("eng@a.io", "engineer"),
                               ("eng2@a.io", "engineer"))
        other = await seed_team(factory, ("eve@b.io", "admin"), name="other")
        yield client, factory, clock, team, other


async def sql(factory, stmt, **kw):
    async with factory() as db:
        res = await db.execute(text(stmt), kw)
        await db.commit()
        return res


async def fetch(factory, stmt, **kw):
    async with factory() as db:
        return (await db.execute(text(stmt), kw)).mappings().all()


# the same table runs in section4 tests/unit/test_notify.py::test_resolve_table
RESOLVE_TABLE = [
    ([], "approval", ("on", "off")),
    ([{"email": "digest"}], "approval", ("on", "digest")),
    ([{"email": "digest", "events": {"approval": {"email": "immediate"}}}], "approval",
     ("on", "immediate")),
    ([{"email": "digest", "events": {"approval": {"email": "immediate"}}}], "mention",
     ("on", "digest")),
    ([{"events": {"approval": {"email": "immediate"}}}, {"email": "off"}], "approval",
     ("on", "off")),
    ([{"email": "off"}, {"email": "off", "events": {"approval": {"email": "urgent"}}}],
     "approval", ("on", "urgent")),
    ([{"inapp": "off"}, {"events": {"mention": {"inapp": "on"}}}], "mention", ("on", "off")),
    ([{"inapp": "off"}, {"events": {"mention": {"inapp": "on"}}}], "failure", ("off", "off")),
    ([{"email": "immediate"}, {"email": None}, {"email": "off"}], "handoff", ("on", "off")),
]


@pytest.mark.parametrize("rows_,kind,expected", RESOLVE_TABLE)
def test_resolve_table(rows_, kind, expected):
    assert resolve(rows_, kind) == expected


# ------------------------------------------------------------------ defaults (010)


async def test_defaults_are_quiet_email_off_and_documented_kinds(env):
    client, _, _, team, _ = env
    body = (await client.get(f"{N}/preferences", headers=H(team["eng@a.io"]))).json()
    assert body["version"] == 0 and body["inapp"] is None and body["email"] is None
    assert body["quietHours"] == {"enabled": False, "start": "22:00", "end": "07:00"}
    assert body["digest"] == {"period": "daily", "hour": 8, "lastSentAt": None}
    assert body["timezone"] == "UTC" and body["mutes"] == []
    assert body["kinds"] == ["approval", "mention", "handoff", "failure", "blocked",
                             "control_request", "suggestion", "budget"]
    # built-in defaults: the inbox is on for every kind, EMAIL IS OFF for every kind (opt-in)
    assert {v["inapp"] for v in body["effective"].values()} == {"on"}
    assert {v["email"] for v in body["effective"].values()} == {"off"}


async def test_put_preferences_validates_audits_and_is_idempotent(env):
    client, factory, _, team, _ = env
    eng = team["eng@a.io"]
    body = await put_prefs(client, eng, {
        "email": "urgent", "timezone": "Asia/Karachi",
        "quietHours": {"enabled": True, "start": "23:00", "end": "06:30"},
        "digest": {"period": "weekly", "hour": 9},
        "events": {"mention": {"email": "digest"}}})
    assert body["version"] == 1 and body["email"] == "urgent"
    assert body["quietHours"] == {"enabled": True, "start": "23:00", "end": "06:30"}
    assert body["effective"]["mention"]["email"] == "digest"
    assert body["effective"]["approval"]["email"] == "urgent"
    rows = await audit_actions(factory, "notification.preferences_changed")
    assert len(rows) == 1
    d = rows[0].detail
    assert d["scopeKind"] == "global" and d["before"]["email"] is None
    assert d["after"]["email"] == "urgent" and d["after"]["timezone"] == "Asia/Karachi"
    # repeating the same PUT changes nothing: same version, no second audit row
    again = await put_prefs(client, eng, {"email": "urgent", "timezone": "Asia/Karachi"})
    assert again["version"] == 1
    assert len(await audit_actions(factory, "notification.preferences_changed")) == 1
    # optimistic concurrency
    r = await client.put(f"{N}/preferences", json={"email": "off", "expectedVersion": 7},
                         headers=H(eng))
    assert r.status_code == 409 and err(r) == "VERSION_CONFLICT"
    ok = await put_prefs(client, eng, {"email": "off", "expectedVersion": 1})
    assert ok["version"] == 2
    # per-event entries merge, null removes
    await put_prefs(client, eng, {"events": {"mention": None, "failure": {"inapp": "off"}}})
    got = (await client.get(f"{N}/preferences", headers=H(eng))).json()
    assert got["events"] == {"failure": {"inapp": "off"}}


@pytest.mark.parametrize("body,code", [
    ({"timezone": "Mars/Olympus"}, "INVALID_TIMEZONE"),
    ({"quietHours": {"enabled": True, "start": "22:00", "end": "22:00"}}, "INVALID_QUIET_HOURS"),
    ({"quietHours": {"start": "25:00"}}, "INVALID_QUIET_HOURS"),
    ({"email": "sometimes"}, "VALIDATION_ERROR"),
    ({"inapp": "loud"}, "VALIDATION_ERROR"),
    ({"events": {"gossip": {"email": "off"}}}, "VALIDATION_ERROR"),
    ({"events": {"mention": {"sound": "on"}}}, "VALIDATION_ERROR"),
    ({"digest": {"period": "hourly"}}, "VALIDATION_ERROR"),
])
async def test_bad_preference_values_are_stable_422(env, body, code):
    client, _, _, team, _ = env
    r = await client.put(f"{N}/preferences", json=body, headers=H(team["eng@a.io"]))
    assert r.status_code == 422 and err(r) == code


async def test_unknown_fields_are_refused_not_ignored(env):
    client, _, _, team, _ = env
    r = await client.put(f"{N}/preferences", json={"emailAddress": "x@y.z"},
                         headers=H(team["eng@a.io"]))
    assert r.status_code == 422


async def test_preferences_are_per_user(env):
    client, factory, _, team, other = env
    await put_prefs(client, team["eng@a.io"], {"email": "immediate"})
    mine = (await client.get(f"{N}/preferences", headers=H(team["eng2@a.io"]))).json()
    assert mine["email"] is None and mine["version"] == 0
    eve = (await client.get(f"{N}/preferences", headers=H(other["eve@b.io"]))).json()
    assert eve["email"] is None


# ------------------------------------------------------------------ precedence (003)


async def test_precedence_session_over_workspace_over_global_event_over_default(env):
    client, factory, _, team, _ = env
    eng = team["eng@a.io"]
    ws = await create_ws(client, eng, "Alpha")
    sess = await create_session(client, eng, workspaceId=ws["id"])
    plain = await create_session(client, eng, title="Other session")
    # global: default digest, approval -> immediate
    await put_prefs(client, eng, {"email": "digest",
                                  "events": {"approval": {"email": "immediate"}}})

    async def effective(path):
        return (await client.get(f"{N}{SCOPE}{path}", headers=H(eng))).json()["effective"]

    g = (await client.get(f"{N}/preferences", headers=H(eng))).json()["effective"]
    assert g["approval"]["email"] == "immediate" and g["mention"]["email"] == "digest"
    # workspace default OFF beats the global per-event entry (more specific scope wins)
    await put_prefs(client, eng, {"email": "off"}, f"{SCOPE}/workspace/{ws['id']}")
    w = await effective(f"/workspace/{ws['id']}")
    assert w["approval"]["email"] == "off" and w["mention"]["email"] == "off"
    # session per-event entry beats the workspace default; other kinds still inherit workspace
    await put_prefs(client, eng, {"events": {"approval": {"email": "urgent", "inapp": "off"}}},
                    f"{SCOPE}/session/{sess['id']}")
    s = await effective(f"/session/{sess['id']}")
    assert s["approval"] == {"inapp": "off", "email": "urgent"}
    assert s["mention"] == {"inapp": "on", "email": "off"}
    # a session outside that workspace only sees the global settings
    p = await effective(f"/session/{plain['id']}")
    assert p["approval"]["email"] == "immediate" and p["mention"]["email"] == "digest"
    # session default beats its own... nothing above it: set a session default, event still wins
    await put_prefs(client, eng, {"email": "immediate"}, f"{SCOPE}/session/{sess['id']}")
    s = await effective(f"/session/{sess['id']}")
    assert s["approval"]["email"] == "urgent" and s["failure"]["email"] == "immediate"


async def test_scope_preferences_need_a_visible_scope_and_a_known_kind(env):
    client, factory, _, team, other = env
    eng, eng2, eve = team["eng@a.io"], team["eng2@a.io"], other["eve@b.io"]
    sess = await create_session(client, eng)
    ws = await create_ws(client, eng, "Private", visibility="private")
    for who in (eng2, eve):
        for path in (f"/session/{sess['id']}", f"/workspace/{ws['id']}", "/session/nope"):
            assert (await client.get(f"{N}{SCOPE}{path}", headers=H(who))).status_code == 404
            r = await client.put(f"{N}{SCOPE}{path}", json={"email": "off"}, headers=H(who))
            assert r.status_code == 404 and err(r) == "NOT_FOUND"
    assert (await client.get(f"{N}{SCOPE}/team/x", headers=H(eng))).status_code == 422
    # once added to the session the scope becomes visible
    await add_member(client, eng, sess["id"], eng2)
    assert (await client.get(f"{N}{SCOPE}/session/{sess['id']}",
                             headers=H(eng2))).status_code == 200
    # scope bodies cannot carry global-only fields
    r = await client.put(f"{N}{SCOPE}/session/{sess['id']}", json={"timezone": "UTC"},
                         headers=H(eng))
    assert r.status_code == 422
    rows = await audit_actions(factory, "notification.preferences_changed")
    assert rows == []  # refused writes leave no trace


# ------------------------------------------------------------------ mute (009)


async def test_mute_is_idempotent_ends_instead_of_deleting_and_is_audited(env):
    client, factory, clock, team, other = env
    eng, eng2, eve = team["eng@a.io"], team["eng2@a.io"], other["eve@b.io"]
    sess = await create_session(client, eng)
    body = {"scopeKind": "session", "scopeId": sess["id"]}
    r = await client.post(f"{N}/mute", json=body, headers=H(eng))
    assert r.status_code == 201 and r.json()["until"] is None and r.json()["endedAt"] is None
    mid = r.json()["id"]
    again = await client.post(f"{N}/mute", json=body, headers=H(eng))
    assert again.status_code == 200 and again.json()["id"] == mid
    live = (await client.get(f"{N}/preferences", headers=H(eng))).json()["mutes"]
    assert [m["id"] for m in live] == [mid]
    # someone else cannot end it, and gets the same 404 as for a missing id
    for who in (eng2, eve):
        assert (await client.delete(f"{N}/mute/{mid}", headers=H(who))).status_code == 404
    assert (await client.delete(f"{N}/mute/{mid}", headers=H(eng))).status_code == 204
    assert (await client.delete(f"{N}/mute/{mid}", headers=H(eng))).status_code == 204  # no-op
    assert (await client.get(f"{N}/preferences", headers=H(eng))).json()["mutes"] == []
    # history is kept: the row is still there, ended
    rows = await fetch(factory, "SELECT * FROM notification_mutes")
    assert len(rows) == 1 and rows[0]["ended_at"] is not None
    assert len(await audit_actions(factory, "notification.muted")) == 1
    assert len(await audit_actions(factory, "notification.unmuted")) == 1
    # a new mute after the old one ended is a fresh row
    r = await client.post(f"{N}/mute", json=body, headers=H(eng))
    assert r.status_code == 201 and r.json()["id"] != mid
    assert len(await fetch(factory, "SELECT * FROM notification_mutes")) == 2


async def test_timed_mute_expires_with_the_clock_and_can_be_replaced(env):
    client, factory, clock, team, _ = env
    eng = team["eng@a.io"]
    body = {"scopeKind": "kind", "scopeId": "mention", "minutes": 60}
    first = (await client.post(f"{N}/mute", json=body, headers=H(eng))).json()
    assert first["until"] == (T0 + timedelta(hours=1)).isoformat()
    assert len((await client.get(f"{N}/preferences", headers=H(eng))).json()["mutes"]) == 1
    clock.advance(minutes=61)
    assert (await client.get(f"{N}/preferences", headers=H(eng))).json()["mutes"] == []
    second = await client.post(f"{N}/mute", json=body, headers=H(eng))
    assert second.status_code == 201 and second.json()["id"] != first["id"]
    old = (await fetch(factory, "SELECT ended_at FROM notification_mutes WHERE id=:i",
                       i=first["id"]))[0]
    assert old["ended_at"] is not None  # closed, not deleted


@pytest.mark.parametrize("body", [
    {"scopeKind": "kind", "scopeId": "gossip"},
    {"scopeKind": "planet", "scopeId": "x"},
    {"scopeKind": "kind", "scopeId": "mention", "minutes": 5, "until": "2031-01-01T00:00:00+00:00"},
    {"scopeKind": "kind", "scopeId": "mention", "until": "2020-01-01T00:00:00+00:00"},
    {"scopeKind": "kind", "scopeId": "mention", "colour": "red"},
])
async def test_bad_mute_requests_are_422(env, body):
    client, _, _, team, _ = env
    r = await client.post(f"{N}/mute", json=body, headers=H(team["eng@a.io"]))
    assert r.status_code == 422


async def test_cannot_mute_a_scope_you_cannot_see(env):
    client, factory, _, team, other = env
    eng, eng2, eve = team["eng@a.io"], team["eng2@a.io"], other["eve@b.io"]
    sess = await create_session(client, eng)
    project = await seed_project(factory, team["teamId"])
    run = await seed_run(factory, project)
    for who in (eng2, eve):
        for kind, sid in (("session", sess["id"]), ("workspace", "nope"), ("run", run)):
            r = await client.post(f"{N}/mute", json={"scopeKind": kind, "scopeId": sid},
                                  headers=H(who))
            ok = who is eng2 and kind == "run"  # a team member can see a run of their team
            assert r.status_code == (201 if ok else 404), (kind, r.text)


# ------------------------------------------------------------------ unsubscribe (009)


async def make_token(factory, actor, expires=timedelta(days=30), used=False) -> str:
    raw = "tok-" + hash_token(str(id(actor)) + str(expires))[:40]
    await sql(factory,
              "INSERT INTO notification_unsub_tokens (token_hash, team_id, user_id, created_at,"
              " expires_at, used_at) VALUES (:h,:t,:u,:c,:e,:used)",
              h=hash_token(raw), t=actor["teamId"], u=actor["userId"], c=T0,
              e=T0 + expires, used=T0 if used else None)
    return raw


async def test_unsubscribe_token_switches_email_off_once_and_is_idempotent(env):
    client, factory, _, team, _ = env
    eng = team["eng@a.io"]
    ws = await create_ws(client, eng, "Alpha")
    await put_prefs(client, eng, {"email": "immediate", "events": {"mention": {"email": "digest"}}})
    await put_prefs(client, eng, {"email": "immediate"}, f"{SCOPE}/workspace/{ws['id']}")
    pending = await seed_item(factory, eng, email_state="pending", email_due_at=T0)
    digest = await seed_item(factory, eng, email_state="digest")
    token = await make_token(factory, eng)
    # no login needed: the token is the credential
    r = await client.post(f"{N}/unsubscribe", json={"token": token})
    assert r.status_code == 200 and r.json() == {"unsubscribed": True, "alreadyApplied": False}
    got = (await client.get(f"{N}/preferences", headers=H(eng))).json()
    assert got["email"] == "off" and got["events"] == {}  # own email overrides are cleared too
    ws_row = (await client.get(f"{N}{SCOPE}/workspace/{ws['id']}", headers=H(eng))).json()
    assert ws_row["email"] is None
    assert {v["email"] for v in got["effective"].values()} == {"off"}
    for nid in (pending, digest):
        rows = await fetch(factory, "SELECT email_state, delivery FROM notification_inbox"
                                    " WHERE id=:i", i=nid)
        assert rows[0]["email_state"] == "skipped"
        assert json.loads(rows[0]["delivery"])["email"]["reason"] == "unsubscribed"
    audits = await audit_actions(factory, "notification.unsubscribed")
    assert len(audits) == 1 and audits[0].detail["channel"] == "email"
    # the person opts back in later; an old mail's link must NOT switch it off again
    await put_prefs(client, eng, {"email": "immediate"})
    again = await client.post(f"{N}/unsubscribe", json={"token": token})
    assert again.status_code == 200 and again.json()["alreadyApplied"] is True
    assert (await client.get(f"{N}/preferences", headers=H(eng))).json()["email"] == "immediate"
    assert len(await audit_actions(factory, "notification.unsubscribed")) == 1


async def test_unsubscribe_unknown_or_expired_token_is_404(env):
    client, factory, clock, team, _ = env
    eng = team["eng@a.io"]
    expired = await make_token(factory, eng, expires=timedelta(days=1))
    clock.advance(days=2)
    for tok in ("x" * 32, expired):
        r = await client.post(f"{N}/unsubscribe", json={"token": tok})
        assert r.status_code == 404 and err(r) == "NOT_FOUND"
    assert (await client.post(f"{N}/unsubscribe", json={"token": "short"})).status_code == 422
    assert (await client.get(f"{N}/preferences", headers=H(eng))).json()["email"] is None
    assert await audit_actions(factory, "notification.unsubscribed") == []
