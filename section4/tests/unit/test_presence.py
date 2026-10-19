"""Part 41 (lane P): presence buckets, privacy, multi-device merge, expiry, team modes."""

import json

from . import test_presence_support as support
from .test_presence_support import P

env = support.env  # the shared fixture (migration 0042 on sqlite, fake clock)


def seat_team(env):
    env.seat("r1", "lead", "leader")
    env.seat("r1", "eng2")
    env.seat("r1", "view", "observer")


def test_foreign_team_gets_the_same_404_as_unknown_run(env):
    seat_team(env)
    c = env.as_("evil")
    unknown = c.get(f"{P}/runs/nope/presence")
    for method, path, body in [
        ("get", "/runs/r1/presence", None),
        ("post", "/runs/r1/presence/heartbeat", {"deviceId": "d1"}),
        ("post", "/runs/r1/presence/leave", None),
    ]:
        r = getattr(c, method)(P + path, **({"json": body} if body else {}))
        assert r.status_code == 404
        assert r.json() == unknown.json()  # no existence oracle


def test_heartbeat_needs_roster_membership(env):
    seat_team(env)
    r = env.beat("eng3")  # same team, never joined
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "NOT_PARTICIPANT"
    assert env.q("SELECT 1 FROM presence_records") == []


def test_multi_device_merge_online_beats_idle_and_survives_one_device_expiring(env):
    seat_team(env)
    assert env.beat("eng2", "laptop", "online").status_code == 200
    env.tick(seconds=60)
    assert env.beat("eng2", "phone", "idle").status_code == 200
    assert env.presence("lead")["eng2"]["presence"] == {"status": "online"}
    env.tick(seconds=45)  # laptop (t0+90) expired, phone (t0+150) live
    assert env.presence("lead")["eng2"]["presence"] == {"status": "idle"}
    assert env.q("SELECT count(*) AS n FROM presence_records")[0].n == 2


def test_stale_expiry_and_coarse_buckets_with_fake_clock(env):
    seat_team(env)
    env.beat("eng2")
    env.tick(seconds=89)
    assert env.presence("lead")["eng2"]["presence"]["status"] == "online"
    env.tick(seconds=2)  # 91 s: expired (client crash, no leave)
    assert env.presence("lead")["eng2"]["presence"] == {
        "status": "disconnected", "lastSeen": "under_5_min"}
    env.tick(minutes=10)
    assert env.presence("lead")["eng2"]["presence"]["lastSeen"] == "under_1_hour"
    env.tick(hours=2)
    assert env.presence("lead")["eng2"]["presence"]["lastSeen"] == "under_1_day"
    env.tick(hours=23)
    assert env.presence("lead")["eng2"]["presence"] == {"status": "unknown"}


def test_payload_never_carries_exact_times_or_devices(env):
    seat_team(env)
    env.beat("eng2", "secret-device-77")
    env.tick(minutes=3)
    body = env.as_("lead").get(f"{P}/runs/r1/presence").text
    assert "secret-device-77" not in body and "2026-" not in body
    data = json.loads(body)
    assert data["approximate"] is True and "availab" in data["notice"]


def test_expired_rows_older_than_24h_are_purged_on_next_heartbeat(env):
    seat_team(env)
    env.beat("eng2", "old")
    env.tick(hours=25)
    env.beat("lead", "new")
    assert [r.device_id for r in env.q("SELECT device_id FROM presence_records")] == ["new"]


def test_device_cap_counts_only_live_devices(env):
    seat_team(env)
    for i in range(10):
        assert env.beat("eng2", f"d{i}").status_code == 200
    r = env.beat("eng2", "d10")
    assert r.status_code == 429 and r.json()["detail"]["error"]["code"] == "TOO_MANY_DEVICES"
    assert env.beat("eng2", "d3").status_code == 200  # known device is never refused
    env.tick(seconds=120)  # all ten expired
    assert env.beat("eng2", "d10").status_code == 200


def test_presence_leave_expires_devices_now(env):
    seat_team(env)
    env.beat("eng2", "a")
    env.beat("eng2", "b")
    r = env.as_("eng2").post(f"{P}/runs/r1/presence/leave", json={"deviceId": "a"})
    assert r.json() == {"expiredDevices": 1}
    assert env.presence("lead")["eng2"]["presence"]["status"] == "online"  # b still live
    assert env.as_("eng2").post(f"{P}/runs/r1/presence/leave").json() == {"expiredDevices": 1}
    assert env.presence("lead")["eng2"]["presence"]["status"] == "disconnected"
    assert env.as_("eng2").post(f"{P}/runs/r1/presence/leave").json() == {"expiredDevices": 0}


# ---------------------------------------------------------------------- privacy


def test_share_nobody_hides_you_from_others_but_not_from_yourself(env):
    seat_team(env)
    env.beat("eng2")
    env.as_("eng2").put(f"{P}/runs/r1/participation/prefs", json={"sharePresence": "nobody"})
    assert env.presence("lead")["eng2"]["presence"] == {"status": "hidden"}
    assert env.presence("eng2")["eng2"]["presence"]["status"] == "online"
    assert "hidden" in env.presence("lead")["eng2"]["announcement"]


def test_share_team_is_roster_only_and_everyone_reaches_non_participants(env):
    seat_team(env)
    env.beat("eng2")
    assert env.presence("lead")["eng2"]["presence"]["status"] == "online"  # roster member
    assert env.presence("eng3")["eng2"]["presence"] == {"status": "hidden"}  # sees run, not on it
    env.as_("eng2").put(f"{P}/runs/r1/participation/prefs", json={"sharePresence": "everyone"})
    assert env.presence("eng3")["eng2"]["presence"]["status"] == "online"


def test_roster_entry_has_role_control_status_and_own_preferences_only(env):
    seat_team(env)
    got = env.presence("eng2")
    assert got["lead"]["isLeader"] is True and got["lead"]["role"] == "leader"
    assert got["view"]["role"] == "observer" and got["eng2"]["self"] is True
    assert "preferences" in got["eng2"] and "preferences" not in got["lead"]
    assert got["lead"]["announcement"]  # accessibility sentence is always present


def test_announcement_sentences_follow_status(env):
    seat_team(env)
    assert env.presence("lead")["eng2"]["announcement"] == "Name eng2's presence is unknown"
    env.beat("eng2", state="idle")
    assert env.presence("lead")["eng2"]["announcement"] == "Name eng2 is idle"
    env.tick(seconds=100)
    assert env.presence("lead")["eng2"]["announcement"] == "Name eng2 disconnected"


# ------------------------------------------------------------------- team settings


def test_defaults_when_no_row(env):
    body = env.as_("eng2").get(f"{P}/presence-settings").json()
    assert (body["mode"], body["staleSeconds"], body["maxParticipants"]) == ("full", 90, 20)
    assert body["isDefault"] is True


def test_only_admins_change_settings_and_change_is_audited_with_before_after(env):
    assert env.as_("eng2").put(f"{P}/presence-settings", json={"mode": "off"}).status_code == 403
    r = env.as_("boss").put(f"{P}/presence-settings", json={"mode": "limited", "staleSeconds": 30})
    assert r.status_code == 200 and r.json()["staleSeconds"] == 30
    (row,) = env.audit("presence.settings_changed")
    d = json.loads(row.detail)
    assert d["before"]["mode"] == "full" and d["after"]["mode"] == "limited"
    assert d["after"]["staleSeconds"] == 30
    env.as_("boss").put(f"{P}/presence-settings", json={"mode": "limited"})  # no-op
    assert len(env.audit("presence.settings_changed")) == 1


def test_settings_validation_and_team_isolation(env):
    c = env.as_("boss")
    for bad in ({"mode": "loud"}, {"staleSeconds": 5}, {"maxParticipants": 0},
                {"maxParticipants": 500}):
        assert c.put(f"{P}/presence-settings", json=bad).status_code == 422
    c.put(f"{P}/presence-settings", json={"mode": "off"})
    assert env.as_("evil").get(f"{P}/presence-settings").json()["mode"] == "full"


def test_limited_mode_collects_and_reports_less(env):
    seat_team(env)
    env.as_("boss").put(f"{P}/presence-settings", json={"mode": "limited"})
    r = env.beat("eng2", state="idle")
    assert r.json()["state"] == "online"  # idle is not collected
    assert env.q("SELECT state FROM presence_records")[0].state == "online"
    assert env.presence("lead")["eng2"]["presence"] == {"status": "online"}
    env.tick(seconds=200)
    assert env.presence("lead")["eng2"]["presence"] == {"status": "unknown"}  # no last-seen


def test_off_mode_refuses_heartbeats_reports_nothing_and_deletes_stored_rows(env):
    seat_team(env)
    env.beat("eng2")
    assert env.q("SELECT count(*) AS n FROM presence_records")[0].n == 1
    env.as_("boss").put(f"{P}/presence-settings", json={"mode": "off"})
    assert env.q("SELECT count(*) AS n FROM presence_records")[0].n == 0
    r = env.beat("eng2")
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "PRESENCE_DISABLED"
    got = env.presence("lead")
    assert all(p["presence"] is None for p in got.values())
    assert got["eng2"]["role"] == "participant"  # roster itself is not presence


def test_stale_window_follows_team_setting(env):
    seat_team(env)
    env.as_("boss").put(f"{P}/presence-settings", json={"staleSeconds": 20})
    assert env.beat("eng2").json()["expiresInSeconds"] == 20
    env.tick(seconds=21)
    assert env.presence("lead")["eng2"]["presence"]["status"] == "disconnected"
