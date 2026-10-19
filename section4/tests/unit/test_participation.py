"""Part 41 (lane P): observe/participate/leave/rejoin, participant cap, mute and prefs."""

import json

from sqlalchemy import text

from harness_orchestrator import participation
from harness_orchestrator.participation import capacity_ok

from . import test_presence_support as support
from .test_presence_support import P

env = support.env  # the shared fixture (migration 0042 on sqlite, fake clock)

PART = f"{P}/runs/r1/participation"


def team(env):
    env.seat("r1", "lead", "leader")
    env.seat("r1", "eng2")
    env.seat("r1", "view", "observer")


def err_code(r):
    return r.json()["detail"]["error"]["code"]


def test_foreign_team_gets_404_on_every_participation_route(env):
    team(env)
    c = env.as_("evil")
    for method, path in [("post", "observe"), ("post", "participate"), ("post", "leave"),
                         ("post", "rejoin"), ("post", "mute"), ("post", "unmute"),
                         ("get", "prefs"), ("put", "prefs")]:
        kw = {"json": {}} if method == "put" else {}
        r = getattr(c, method)(f"{PART}/{path}", **kw)
        assert r.status_code == 404, path
    assert env.roster() == {"lead": "leader", "eng2": "participant", "view": "observer"}


def test_observe_then_participate_round_trip_is_audited_announced_and_idempotent(env):
    team(env)
    r = env.as_("eng2").post(f"{PART}/observe")
    assert r.json()["role"] == "observer"
    assert r.json()["announcement"] == "Name eng2 is now observing"
    assert env.roster()["eng2"] == "observer"
    assert env.as_("eng2").post(f"{PART}/observe").json() == {"changed": False, "role": "observer"}
    assert env.as_("eng2").post(f"{PART}/participate").json()["role"] == "participant"
    assert env.kinds() == ["participant.observing", "participant.participating"]
    assert env.audit() == ["participant.observing", "participant.joined"]
    ev = env.q("SELECT payload FROM run_collab_events ORDER BY seq")
    assert json.loads(ev[0].payload)["announcement"] == "Name eng2 is now observing"
    d = json.loads(env.audit("participant.observing")[0].detail)
    assert d["before"] == {"role": "participant"} and d["after"] == {"role": "observer"}


def test_viewer_cannot_participate_and_non_member_cannot_switch(env):
    team(env)
    r = env.as_("view").post(f"{PART}/participate")
    assert r.status_code == 403 and err_code(r) == "ROLE_DENIED"
    assert env.roster()["view"] == "observer"
    r = env.as_("eng3").post(f"{PART}/observe")
    assert r.status_code == 409 and err_code(r) == "NOT_PARTICIPANT"


def test_leader_cannot_leave_or_observe(env):
    team(env)
    for path in ("leave", "observe"):
        r = env.as_("lead").post(f"{PART}/{path}")
        assert r.status_code == 409 and err_code(r) == "LEADER_MUST_HAND_OFF"
    assert env.roster()["lead"] == "leader" and env.kinds() == []


def test_leave_deletes_row_presence_and_pending_handoff_then_rejoin_restores(env):
    team(env)
    env.beat("eng2")

    async def handoff(db):
        await db.execute(text(
            "INSERT INTO run_handoffs (id, run_id, team_id, from_user_id, to_user_id, state)"
            " VALUES ('h1', 'r1', 'team-a', 'lead', 'eng2', 'pending')"))
        await db.commit()

    env.run(handoff)
    r = env.as_("eng2").post(f"{PART}/leave")
    assert r.json()["left"] is True and r.json()["announcement"] == "Name eng2 left the run"
    assert "eng2" not in env.roster()
    assert env.q("SELECT 1 FROM presence_records WHERE user_id='eng2'") == []
    assert env.q("SELECT state FROM run_handoffs")[0].state == "cancelled"
    assert env.as_("eng2").post(f"{PART}/leave").json() == {"changed": False, "left": True}
    assert env.beat("eng2").status_code == 409  # not on the roster any more
    r = env.as_("eng2").post(f"{PART}/rejoin")
    assert r.json()["role"] == "participant" and env.roster()["eng2"] == "participant"
    assert env.as_("eng2").post(f"{PART}/rejoin").json()["changed"] is False
    assert env.kinds() == ["participant.left", "participant.rejoined"]
    assert env.audit() == ["participant.left", "participant.rejoined"]


def test_rejoining_viewer_comes_back_as_observer(env):
    team(env)
    env.as_("view").post(f"{PART}/leave")
    assert env.as_("view").post(f"{PART}/rejoin").json()["role"] == "observer"


# ---------------------------------------------------------------------- capacity


def set_cap(env, n):
    r = env.as_("boss").put(f"{P}/presence-settings", json={"maxParticipants": n})
    assert r.status_code == 200


def test_full_run_refuses_new_members_with_stable_code_and_audit(env):
    env.seat("r1", "lead", "leader")
    env.seat("r1", "eng2")
    set_cap(env, 2)
    r = env.as_("eng3").post(f"{PART}/rejoin")
    assert r.status_code == 409 and err_code(r) == "SESSION_FULL"
    assert "eng3" not in env.roster() and env.kinds() == []
    (row,) = env.audit("participant.capacity_refused")
    assert json.loads(row.detail)["max"] == 2
    # members are never refused: retries, role switches, and leave-then-rejoin of a freed seat
    assert env.as_("eng2").post(f"{PART}/rejoin").json()["changed"] is False
    assert env.as_("eng2").post(f"{PART}/observe").status_code == 200
    env.as_("eng2").post(f"{PART}/leave")
    assert env.as_("eng3").post(f"{PART}/rejoin").status_code == 200


def test_cap_admits_exactly_the_limit(env):
    env.seat("r1", "lead", "leader")
    set_cap(env, 3)
    users = ("eng2", "eng3", "eng4", "boss")
    codes = [env.as_(u).post(f"{PART}/rejoin").status_code for u in users]
    assert codes == [200, 200, 409, 409] and len(env.roster()) == 3


def test_capacity_ok_contract(env):
    env.seat("r1", "lead", "leader")
    env.seat("r1", "eng2")
    set_cap(env, 2)

    async def go(db):
        return (
            await capacity_ok(db, "r1", "team-a", "eng2"),   # member of a full run
            await capacity_ok(db, "r1", "team-a", "eng3"),   # newcomer, full
            await capacity_ok(db, "r2", "team-a", "eng3"),   # other run is empty
        )

    assert env.run(go) == (True, False, True)


def test_capacity_ok_uses_default_cap_when_settings_table_is_missing(env):
    env.seat("r1", "lead", "leader")

    async def drop_then_check(db):
        await db.execute(text("DROP TABLE team_presence_settings"))
        return await capacity_ok(db, "r1", "team-a", "eng3")

    assert env.run(drop_then_check) is True  # pre-0042 stores keep joining


def test_capacity_scope_is_per_run_and_per_team(env):
    env.seat("r1", "lead", "leader")
    set_cap(env, 1)
    assert env.as_("eng2").post(f"{P}/runs/r2/participation/rejoin").status_code == 200
    # team-b's settings are untouched by team-a's cap
    assert env.as_("evil").post(f"{P}/runs/r3/participation/rejoin").status_code == 200
    assert env.as_("eng2").post(f"{PART}/rejoin").status_code == 409


# -------------------------------------------------------------------------- prefs


def test_prefs_default_then_mute_unmute_are_audited_idempotent_and_private(env):
    team(env)
    base = env.as_("eng2").get(f"{PART}/prefs").json()
    assert base == {"muted": False, "notify": True, "sharePresence": "team",
                    "notificationsEnabled": True}
    r = env.as_("eng2").post(f"{PART}/mute").json()
    assert r["changed"] and r["muted"] and r["notificationsEnabled"] is False
    assert r["announcement"] == "Notifications for this run are muted"
    assert env.as_("eng2").post(f"{PART}/mute").json()["changed"] is False
    env.as_("eng2").post(f"{PART}/unmute")
    assert env.audit() == ["participant.muted", "participant.unmuted"]
    assert env.kinds() == []  # mute is not broadcast
    env.as_("eng2").post(f"{PART}/mute")
    assert env.as_("lead").get(f"{PART}/prefs").json()["muted"] is False  # own prefs only
    assert "muted" not in json.dumps(env.presence("lead")["eng2"])


def test_put_prefs_partial_update_and_validation(env):
    team(env)
    r = env.as_("eng2").put(f"{PART}/prefs", json={"notify": False, "sharePresence": "nobody"})
    assert r.json()["notify"] is False and r.json()["sharePresence"] == "nobody"
    assert r.json()["muted"] is False
    (row,) = env.audit("presence.settings_changed")
    assert json.loads(row.detail)["after"]["sharePresence"] == "nobody"
    assert env.as_("eng2").put(f"{PART}/prefs", json={"sharePresence": "world"}).status_code == 422
    assert env.as_("eng2").put(f"{PART}/prefs", json={}).json()["changed"] is False


def test_prefs_need_roster_membership_and_survive_leave(env):
    team(env)
    r = env.as_("eng3").post(f"{PART}/mute")
    assert r.status_code == 409 and err_code(r) == "NOT_PARTICIPANT"
    env.as_("eng2").post(f"{PART}/mute")
    env.as_("eng2").post(f"{PART}/leave")
    env.as_("eng2").post(f"{PART}/rejoin")
    assert env.as_("eng2").get(f"{PART}/prefs").json()["muted"] is True


def test_rejoin_that_loses_an_insert_race_is_idempotent_not_a_500(env, monkeypatch):
    env.seat("r1", "lead", "leader")
    env.seat("r1", "eng2")
    real = participation._me
    calls = {"n": 0}

    async def stale_read(db, run_id, user_id):
        calls["n"] += 1  # the first read happens "before" the competing insert committed
        return None if calls["n"] == 1 else await real(db, run_id, user_id)

    monkeypatch.setattr(participation, "_me", stale_read)
    r = env.as_("eng2").post(f"{PART}/rejoin")
    assert r.status_code == 200 and r.json()["changed"] is False
    assert env.q("SELECT count(*) AS n FROM run_participants")[0].n == 2
