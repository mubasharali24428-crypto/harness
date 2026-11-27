"""Part 47 email channel: opt-in only, quiet hours in the user's zone with a fake clock, urgent
bypass, retries and visible state, digests, no content in mail, worker hook (Session 5 lane N)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from harness_contracts import hash_token
from sqlalchemy import text

from harness_identity import notifications
from harness_identity.notifications import deliver_due, quiet_until, run_digests
from tests.test_notifications_support import (
    T0,
    FakeSender,
    N,
    audit_actions,
    bearer,
    make_env,
    put_prefs,
    row,
    seed_item,
    seed_team,
)

H = bearer
KARACHI = "Asia/Karachi"  # UTC+5 all year


@pytest.fixture
async def env(monkeypatch):
    async with make_env(monkeypatch) as (client, factory, clock):
        team = await seed_team(factory, ("admin@a.io", "admin"), ("eng@a.io", "engineer"),
                               ("eng2@a.io", "engineer"))
        yield client, factory, clock, team


def at(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


async def delivery_of(factory, nid: str) -> dict:
    return json.loads((await row(factory, nid))["delivery"])["email"]


# ------------------------------------------------------------------ the quiet-hours rule itself


@pytest.mark.parametrize("now,tz,start,end,expected", [
    # 23:00 in Karachi (18:00 UTC) is inside 22:00-07:00 and ends at 07:00 local = 02:00 UTC
    (at(2030, 1, 1, 18, 0), KARACHI, "22:00", "07:00", at(2030, 1, 2, 2, 0)),
    # 03:00 local is also inside, and the window ends the SAME local day
    (at(2030, 1, 1, 22, 0), KARACHI, "22:00", "07:00", at(2030, 1, 2, 2, 0)),
    # 12:00 UTC is 17:00 in Karachi: outside
    (at(2030, 1, 1, 12, 0), KARACHI, "22:00", "07:00", None),
    # the same instant is inside for a user in UTC whose window is 11:00-13:00
    (at(2030, 1, 1, 12, 0), "UTC", "11:00", "13:00", at(2030, 1, 1, 13, 0)),
    # end is exclusive, start inclusive
    (at(2030, 1, 1, 7, 0), "UTC", "22:00", "07:00", None),
    (at(2030, 1, 1, 22, 0), "UTC", "22:00", "07:00", at(2030, 1, 2, 7, 0)),
    # New York in winter is UTC-5: 03:00 UTC is 22:00 local
    (at(2030, 1, 15, 3, 0), "America/New_York", "22:00", "07:00", at(2030, 1, 15, 12, 0)),
    # and in summer UTC-4: the same UTC instant is 23:00 local, ends 11:00 UTC
    (at(2030, 7, 15, 3, 0), "America/New_York", "22:00", "07:00", at(2030, 7, 15, 11, 0)),
    # degenerate or broken settings never silence anyone
    (at(2030, 1, 1, 12, 0), "UTC", "10:00", "10:00", None),
    (at(2030, 1, 1, 12, 0), "UTC", "bad", "10:00", None),
])
def test_quiet_until_uses_the_users_wall_clock(now, tz, start, end, expected):
    assert quiet_until(now, tz, start, end) == expected


# ------------------------------------------------------------------ opt-in, quiet hours, urgent


async def test_only_rows_the_producer_queued_are_mailed_and_opt_out_wins_at_send_time(env):
    client, factory, _, team = env
    eng = team["eng@a.io"]
    sender = FakeSender()
    never = await seed_item(factory, eng)  # email_state none: the user never opted in
    queued = await seed_item(factory, eng, email_state="pending", email_due_at=T0)
    out = await deliver_due(factory, T0, delivery=sender)
    # no preferences row at all means email is off: the queued row is dropped, nothing is sent
    assert sender.sent == [] and out["skipped"] == 1 and out["sent"] == 0
    assert (await row(factory, never))["email_state"] == "none"
    assert (await delivery_of(factory, queued))["reason"] == "opted_out"
    # opting in sends the next queued one
    await put_prefs(client, eng, {"email": "immediate"})
    q2 = await seed_item(factory, eng, email_state="pending", email_due_at=T0)
    out = await deliver_due(factory, T0, delivery=sender)
    assert out["sent"] == 1 and sender.sent[0]["to"] == "eng@a.io"
    assert (await row(factory, q2))["email_state"] == "sent"
    # opting out between queue time and send time is honoured
    await put_prefs(client, eng, {"email": "off"})
    q3 = await seed_item(factory, eng, email_state="pending", email_due_at=T0)
    await deliver_due(factory, T0, delivery=sender)
    assert len(sender.sent) == 1 and (await row(factory, q3))["email_state"] == "skipped"
    # 'urgent only' drops a non-urgent row but mails an urgent one
    await put_prefs(client, eng, {"email": "urgent"})
    plain = await seed_item(factory, eng, email_state="pending", email_due_at=T0)
    hot = await seed_item(factory, eng, urgent=True, email_state="pending", email_due_at=T0)
    await deliver_due(factory, T0, delivery=sender)
    assert (await row(factory, plain))["email_state"] == "skipped"
    assert (await row(factory, hot))["email_state"] == "sent" and len(sender.sent) == 2


async def test_quiet_hours_hold_mail_until_they_end_but_urgent_bypasses(env):
    client, factory, _, team = env
    eng = team["eng@a.io"]
    await put_prefs(client, eng, {
        "email": "immediate", "timezone": KARACHI,
        "quietHours": {"enabled": True, "start": "22:00", "end": "07:00"}})
    sender = FakeSender()
    night = at(2030, 1, 1, 18, 0)  # 23:00 in Karachi
    calm = await seed_item(factory, eng, email_state="pending", email_due_at=night)
    urgent = await seed_item(factory, eng, urgent=True, email_state="pending", email_due_at=night)
    out = await deliver_due(factory, night, delivery=sender)
    assert out["sent"] == 1 and out["held"] == 1
    assert [m["subject"] for m in sender.sent] == ["Harness: An approval needs your decision"]
    assert (await row(factory, urgent))["email_state"] == "sent"
    held = await row(factory, calm)
    assert held["email_state"] == "pending"
    assert (await delivery_of(factory, calm))["reason"] == "quiet_hours"
    assert str(held["email_due_at"]).startswith("2030-01-02 02:00:00")  # 07:00 Karachi
    # nothing happens one minute before the window ends...
    assert (await deliver_due(factory, at(2030, 1, 2, 1, 59), delivery=sender))["scanned"] == 0
    assert len(sender.sent) == 1
    # ...and the held mail goes out when it ends
    out = await deliver_due(factory, at(2030, 1, 2, 2, 0), delivery=sender)
    assert out["sent"] == 1 and (await row(factory, calm))["email_state"] == "sent"


async def test_quiet_hours_also_apply_when_the_window_was_switched_on_after_queueing(env):
    client, factory, _, team = env
    eng = team["eng@a.io"]
    await put_prefs(client, eng, {"email": "immediate"})
    sender = FakeSender()
    nid = await seed_item(factory, eng, email_state="pending", email_due_at=T0)
    quiet = {"enabled": True, "start": "11:00", "end": "13:00"}
    await put_prefs(client, eng, {"timezone": "UTC", "quietHours": quiet})
    out = await deliver_due(factory, T0, delivery=sender)  # 12:00 UTC is inside
    assert out["held"] == 1 and sender.sent == []
    assert (await row(factory, nid))["email_state"] == "pending"


async def test_read_dismissed_and_snoozed_rows_are_not_mailed(env):
    client, factory, _, team = env
    eng = team["eng@a.io"]
    await put_prefs(client, eng, {"email": "immediate"})
    sender = FakeSender()
    read = await seed_item(factory, eng, state="read", email_state="pending", email_due_at=T0)
    gone = await seed_item(factory, eng, state="dismissed", email_state="pending", email_due_at=T0)
    napping = await seed_item(factory, eng, state="snoozed", snoozed_until=T0 + timedelta(hours=1),
                              email_state="pending", email_due_at=T0)
    out = await deliver_due(factory, T0, delivery=sender)
    assert sender.sent == [] and out["skipped"] == 2 and out["held"] == 1
    assert (await delivery_of(factory, read))["reason"] == "handled_in_app"
    assert (await row(factory, gone))["email_state"] == "skipped"
    assert (await row(factory, napping))["email_state"] == "pending"
    out = await deliver_due(factory, T0 + timedelta(hours=1, minutes=1), delivery=sender)
    assert out["sent"] == 1 and (await row(factory, napping))["email_state"] == "sent"


async def test_digest_setting_switched_after_queueing_moves_the_row_to_the_digest(env):
    client, factory, _, team = env
    eng = team["eng@a.io"]
    await put_prefs(client, eng, {"email": "digest"})
    sender = FakeSender()
    nid = await seed_item(factory, eng, email_state="pending", email_due_at=T0)
    await deliver_due(factory, T0, delivery=sender)
    assert sender.sent == [] and (await row(factory, nid))["email_state"] == "digest"


# ------------------------------------------------------------------ retries and visible state


async def test_failed_sends_back_off_then_fail_audited_and_state_is_visible(env):
    client, factory, _, team = env
    eng = team["eng@a.io"]
    await put_prefs(client, eng, {"email": "immediate"})
    sender = FakeSender(ok=False)
    nid = await seed_item(factory, eng, email_state="pending", email_due_at=T0)
    out = await deliver_due(factory, T0, delivery=sender)
    assert out["retried"] == 1
    d = await delivery_of(factory, nid)
    assert d["state"] == "pending" and d["reason"] == "retrying" and d["attempts"] == 1
    assert (await deliver_due(factory, T0 + timedelta(seconds=30), delivery=sender))["scanned"] == 0
    out = await deliver_due(factory, T0 + timedelta(seconds=61), delivery=sender)
    assert out["retried"] == 1 and (await delivery_of(factory, nid))["attempts"] == 2
    out = await deliver_due(factory, T0 + timedelta(minutes=10), delivery=sender)
    assert out["failed"] == 1 and len(sender.sent) == 3
    assert (await row(factory, nid))["email_state"] == "failed"
    audits = await audit_actions(factory, "notification.delivery_failed")
    assert len(audits) == 1 and audits[0].detail["notificationIds"] == [nid]
    # the user can see the state on the item itself
    item = (await client.get(N, headers=H(eng))).json()["items"][0]
    assert item["delivery"]["inapp"] == "delivered"
    assert item["delivery"]["email"]["state"] == "failed"
    assert item["delivery"]["email"]["attempts"] == 3
    # a failed row is never retried again
    assert (await deliver_due(factory, T0 + timedelta(days=1), delivery=sender))["scanned"] == 0


async def test_a_raising_sender_does_not_stop_the_batch(env):
    client, factory, _, team = env
    eng, eng2 = team["eng@a.io"], team["eng2@a.io"]
    await put_prefs(client, eng, {"email": "immediate"})
    await put_prefs(client, eng2, {"email": "immediate"})

    class Flaky(FakeSender):
        async def deliver(self, to, subject, html, plain):
            if to == "eng@a.io":
                raise RuntimeError("boom")
            return await super().deliver(to, subject, html, plain)

    sender = Flaky()
    a = await seed_item(factory, eng, email_state="pending", email_due_at=T0)
    b = await seed_item(factory, eng2, email_state="pending", email_due_at=T0)
    out = await deliver_due(factory, T0, delivery=sender)
    assert out["retried"] == 1 and out["sent"] == 1
    assert (await row(factory, b))["email_state"] == "sent"
    assert (await delivery_of(factory, a))["reason"] == "retrying"


# ------------------------------------------------------------------ mail content (006)


async def test_mail_holds_no_content_and_an_unsubscribe_link_whose_token_is_stored_hashed(env):
    client, factory, _, team = env
    eng = team["eng@a.io"]
    secret = "Quarterly layoffs rehearsal"
    # G-QUAL-1: seed the sensitive title in THIS test's database instead of going
    # through the sessions router, so no session-module state can bleed into the
    # mail-content assertion when the full suite runs in a different order.
    sess = {"id": "s-mail-secret"}
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO collab_sessions (id, team_id, title, owner_user_id,"
            " created_at, updated_at) VALUES (:i, :t, :title, :u, :ts, :ts)"),
            {"i": sess["id"], "t": eng["teamId"], "title": secret,
             "u": eng["userId"], "ts": T0})
        await db.commit()
    await put_prefs(client, eng, {"email": "immediate"})
    nid = await seed_item(factory, eng, kind="mention", source_kind="comment",
                          source_id="c-1", session_id=sess["id"], count=2,
                          email_state="pending", email_due_at=T0)
    sender = FakeSender()
    await deliver_due(factory, T0, delivery=sender)
    mail = sender.sent[0]
    blob = mail["subject"] + mail["plain"] + mail["html"]
    assert secret not in blob and "c-1" not in blob
    assert mail["subject"] == "Harness: You were mentioned 2 times"
    assert f"/notifications?open={nid}" in mail["plain"]
    token = mail["plain"].split("/notifications/unsubscribe?token=")[1].split()[0]
    async with factory() as db:
        rows = (await db.execute(text(
            "SELECT token_hash FROM notification_unsub_tokens WHERE team_id = :t AND user_id = :u"),
            {"t": eng["teamId"], "u": eng["userId"]})).all()
    assert [r[0] for r in rows] == [hash_token(token)] and token not in [r[0] for r in rows]
    # the link in the mail really works, once
    r = await client.post(f"{N}/unsubscribe", json={"token": token})
    assert r.json() == {"unsubscribed": True, "alreadyApplied": False}
    assert (await client.get(f"{N}/preferences", headers=H(eng))).json()["email"] == "off"


# ------------------------------------------------------------------ digests (004)


async def test_daily_digest_waits_for_the_users_hour_sends_once_and_skips_handled_items(env):
    client, factory, clock, team = env
    eng = team["eng@a.io"]
    await put_prefs(client, eng, {"email": "digest", "timezone": KARACHI,
                                  "digest": {"period": "daily", "hour": 9}})
    sender = FakeSender()
    ids = [await seed_item(factory, eng, kind="mention", source_kind="comment",
                           email_state="digest") for _ in range(2)]
    handled = await seed_item(factory, eng, state="read", email_state="digest")
    ids.append(await seed_item(factory, eng, kind="failure", source_kind="run",
                               email_state="digest"))
    # 03:00 UTC is 08:00 in Karachi: before the user's hour
    assert (await run_digests(factory, at(2030, 1, 1, 3, 0), delivery=sender))["sent"] == 0
    out = await run_digests(factory, at(2030, 1, 1, 4, 0), delivery=sender)  # 09:00 local
    assert out == {"users": 1, "sent": 1, "failed": 0, "retried": 0, "skipped": 1}
    assert len(sender.sent) == 1
    mail = sender.sent[0]
    assert mail["subject"] == "Harness digest: 3 items"
    assert mail["plain"].count("You were mentioned") == 2 and "A run failed" in mail["plain"]
    for nid in ids:
        assert (await row(factory, nid))["email_state"] == "sent"
    assert (await row(factory, handled))["email_state"] == "skipped"
    audits = await audit_actions(factory, "notification.digest_sent")
    assert len(audits) == 1 and audits[0].detail["count"] == 3
    assert audits[0].detail["kinds"] == ["failure", "mention"]
    pref = (await client.get(f"{N}/preferences", headers=H(eng))).json()
    assert pref["digest"]["lastSentAt"] == "2030-01-01T04:00:00+00:00"
    # the same local day: nothing more, even with a new item
    late = await seed_item(factory, eng, email_state="digest")
    assert (await run_digests(factory, at(2030, 1, 1, 10, 0), delivery=sender))["sent"] == 0
    # the next local day it goes out
    assert (await run_digests(factory, at(2030, 1, 2, 4, 0), delivery=sender))["sent"] == 1
    assert (await row(factory, late))["email_state"] == "sent"
    # and with nothing pending there is no empty digest
    assert (await run_digests(factory, at(2030, 1, 3, 4, 0), delivery=sender))["users"] == 0


async def test_weekly_digest_and_quiet_hours_defer_the_digest(env):
    client, factory, _, team = env
    eng = team["eng@a.io"]
    quiet = {"enabled": True, "start": "08:00", "end": "09:00"}
    await put_prefs(client, eng, {"email": "digest", "timezone": "UTC",
                                  "digest": {"period": "weekly", "hour": 8}, "quietHours": quiet})
    sender = FakeSender()
    await seed_item(factory, eng, email_state="digest")
    # 08:30 is inside quiet hours: wait
    assert (await run_digests(factory, at(2030, 1, 1, 8, 30), delivery=sender))["users"] == 0
    assert (await run_digests(factory, at(2030, 1, 1, 9, 0), delivery=sender))["sent"] == 1
    await seed_item(factory, eng, email_state="digest")
    assert (await run_digests(factory, at(2030, 1, 5, 9, 0), delivery=sender))["users"] == 0
    assert (await run_digests(factory, at(2030, 1, 8, 9, 0), delivery=sender))["sent"] == 1


async def test_digest_failures_back_off_and_end_in_an_audited_failure(env):
    client, factory, _, team = env
    eng = team["eng@a.io"]
    await put_prefs(client, eng, {"email": "digest", "timezone": "UTC", "digest": {"hour": 0}})
    sender = FakeSender(ok=False)
    nid = await seed_item(factory, eng, email_state="digest")
    out = await run_digests(factory, T0, delivery=sender)
    assert out["retried"] == 1 and (await row(factory, nid))["email_state"] == "digest"
    assert (await run_digests(factory, T0 + timedelta(seconds=10), delivery=sender))["sent"] == 0
    assert len(sender.sent) == 1  # backing off, not hammering the relay
    await run_digests(factory, T0 + timedelta(minutes=30), delivery=sender)
    out = await run_digests(factory, T0 + timedelta(hours=2), delivery=sender)
    assert out["failed"] == 1 and (await row(factory, nid))["email_state"] == "failed"
    assert len(await audit_actions(factory, "notification.delivery_failed")) == 1
    assert await audit_actions(factory, "notification.digest_sent") == []


# ------------------------------------------------------------------ worker hook


async def test_the_existing_worker_loop_drives_both_jobs(env, monkeypatch):
    _, factory, _, _ = env
    from harness_identity.notifier import NotificationWorker

    calls: list[str] = []

    async def fake_due(f, now, **kw):
        calls.append("due")
        return {}

    async def fake_digests(f, now, **kw):
        calls.append("digests")
        return {}

    monkeypatch.setattr(notifications, "deliver_due", fake_due)
    monkeypatch.setattr(notifications, "run_digests", fake_digests)
    worker = NotificationWorker(factory, poll_interval=0.01)
    worker.start()
    for _ in range(200):
        if {"due", "digests"} <= set(calls):
            break
        await asyncio.sleep(0.01)
    worker.stop()
    await asyncio.gather(worker._task, return_exceptions=True)  # before engine.dispose()
    assert {"due", "digests"} <= set(calls)
