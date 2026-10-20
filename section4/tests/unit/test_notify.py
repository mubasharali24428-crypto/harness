"""Part 47 producer side: notify.emit (Session 5 lane N).

Real migration 0046 on sqlite, minimal copies of the tables emit reads, and a fake clock patched
into the module. Covers preference precedence, mute, quiet hours in the user's zone, urgent bypass,
dedupe, grouping, no content, same-team isolation and never raising.
"""

import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import notify

MIGRATION = (Path(__file__).resolve().parents[3] / "section1" / "migrations"
             / "0046_notifications.sql")
T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
KARACHI = "Asia/Karachi"  # UTC+5, no DST
DDL = (
    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36), role VARCHAR(16) NOT NULL DEFAULT 'engineer')",
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " workspace_id VARCHAR(36))",
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36))",
    "CREATE TABLE workspaces (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)",
    "CREATE TABLE collab_sessions (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " workspace_id VARCHAR(36))",
    "CREATE TABLE session_runs (run_id VARCHAR(36) PRIMARY KEY, session_id VARCHAR(36) NOT NULL)",
    "CREATE TABLE marker (id INTEGER PRIMARY KEY)",
)


def migration_statements() -> list[str]:
    out = []
    for chunk in MIGRATION.read_text().split(";"):
        body = "\n".join(x for x in chunk.splitlines() if not x.strip().startswith("--")).strip()
        if body:
            out.append(body)
    return out


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.delenv(notify.GROUP_SECONDS_ENV, raising=False)
    clock = Clock()
    monkeypatch.setattr(notify, "db_now", clock)
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for stmt in (*DDL, *migration_statements()):
            await conn.execute(text(stmt))
        for tid, uid in (("t1", "ann"), ("t1", "bob"), ("t2", "eve")):
            await conn.execute(text("INSERT INTO team_members (id, team_id, user_id)"
                                    " VALUES (:i, :t, :u)"), {"i": f"m-{uid}", "t": tid, "u": uid})
        await conn.execute(text("INSERT INTO workspaces (id, team_id) VALUES ('w1','t1'),"
                                " ('w2','t1'), ('wx','t2')"))
        await conn.execute(text("INSERT INTO collab_sessions (id, team_id, workspace_id) VALUES"
                                " ('s1','t1','w1'), ('s2','t1',NULL), ('sx','t2',NULL)"))
        await conn.execute(text("INSERT INTO projects (id, team_id, workspace_id) VALUES"
                                " ('p1','t1','w1'), ('px','t2',NULL)"))
        await conn.execute(text("INSERT INTO agent_runs (id, project_id) VALUES"
                                " ('r1','p1'), ('r2','p1'), ('rx','px')"))
        await conn.execute(text("INSERT INTO session_runs (run_id, session_id) VALUES ('r1','s1')"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory, clock
    await engine.dispose()


async def send(factory, **kw) -> dict:
    """emit the way a call site does: inside a transaction that already wrote something."""
    args = {"team_id": "t1", "user_id": "ann", "kind": "approval", "source_kind": "approval",
            "source_id": "a-1", **kw}
    async with factory() as db:
        await db.execute(text("INSERT INTO marker (id) VALUES (NULL)"))  # caller's own write
        out = await notify.emit(db, **args)
        await db.commit()
    return out


async def rows(factory, where: str = "1=1", **kw) -> list:
    async with factory() as db:
        return (await db.execute(text(f"SELECT * FROM notification_inbox WHERE {where}"
                                      " ORDER BY created_at, id"), kw)).mappings().all()


async def prefs(factory, user="ann", scope=("global", "*"), **cols) -> None:
    cols = {"inapp": None, "email": None, "events": "{}", "timezone": None, "quiet_enabled": False,
            "quiet_start": None, "quiet_end": None, **cols}
    if not isinstance(cols["events"], str):
        cols["events"] = json.dumps(cols["events"])
    async with factory() as db:
        await db.execute(
            text("INSERT INTO notification_preferences (id, team_id, user_id, scope_kind, scope_id,"
                 " inapp, email, events, timezone, quiet_enabled, quiet_start, quiet_end,"
                 " created_at, updated_at) VALUES (:id,'t1',:u,:k,:s,:inapp,:email,:events,"
                 ":timezone,:quiet_enabled,:quiet_start,:quiet_end,:n,:n)"),
            {"id": f"{user}-{scope[0]}-{scope[1]}", "u": user, "k": scope[0], "s": scope[1],
             "n": T0, **cols})
        await db.commit()


async def mute(factory, scope_kind, scope_id, until=None, ended=None, user="ann") -> None:
    async with factory() as db:
        await db.execute(
            text("INSERT INTO notification_mutes (id, team_id, user_id, scope_kind, scope_id,"
                 " muted_until, created_at, ended_at) VALUES (:i,'t1',:u,:k,:s,:m,:n,:e)"),
            {"i": f"{scope_kind}-{scope_id}-{ended}", "u": user, "k": scope_kind, "s": scope_id,
             "m": until, "n": T0, "e": ended})
        await db.commit()


# ------------------------------------------------------------------ contract pins


def test_signature_and_closed_vocabulary_are_the_documented_contract():
    params = list(inspect.signature(notify.emit).parameters)
    assert params == ["db", "team_id", "user_id", "kind", "source_kind", "source_id", "run_id",
                      "session_id", "workspace_id", "urgent", "dedupe_key", "summary_code"]
    # nothing in the signature can carry free text except the closed code and ids
    assert notify.KINDS == ("approval", "mention", "handoff", "failure", "blocked",
                            "control_request", "suggestion", "budget")
    assert {k: list(v) for k, v in notify.SUMMARY_CODES.items()} == {
        "approval": ["approval_requested", "approval_decided"], "mention": ["mentioned"],
        "handoff": ["handoff_offered", "handoff_accepted", "handoff_rejected"],
        "failure": ["run_failed", "review_failed"], "blocked": ["run_blocked"],
        "control_request": ["control_requested", "control_granted", "control_declined"],
        "suggestion": ["suggestion_queued"], "budget": ["budget_warning"]}


# precedence table, mirrored in section1 tests/test_notifications_prefs.py::test_resolve_table
RESOLVE_TABLE = [
    ([], "approval", ("on", "off")),
    ([{"email": "digest"}], "approval", ("on", "digest")),
    ([{"email": "digest", "events": {"approval": {"email": "immediate"}}}], "approval",
     ("on", "immediate")),
    ([{"email": "digest", "events": {"approval": {"email": "immediate"}}}], "mention",
     ("on", "digest")),
    # a more specific scope DEFAULT beats a less specific scope's per-event entry
    ([{"events": {"approval": {"email": "immediate"}}}, {"email": "off"}], "approval",
     ("on", "off")),
    # within one scope the per-event entry beats the scope default
    ([{"email": "off"}, {"email": "off", "events": {"approval": {"email": "urgent"}}}],
     "approval", ("on", "urgent")),
    ([{"inapp": "off"}, {"events": {"mention": {"inapp": "on"}}}], "mention", ("on", "off")),
    ([{"inapp": "off"}, {"events": {"mention": {"inapp": "on"}}}], "failure", ("off", "off")),
    ([{"email": "immediate"}, {"email": None}, {"email": "off"}], "handoff", ("on", "off")),
]


@pytest.mark.parametrize("rows_,kind,expected", RESOLVE_TABLE)
def test_resolve_table(rows_, kind, expected):
    assert notify.resolve(rows_, kind) == expected


# ------------------------------------------------------------------ the row


async def test_created_row_holds_ids_and_a_closed_code_only(env):
    factory, _ = env
    out = await send(factory, run_id="r1", urgent=False, dedupe_key="approval:a-1",
                     summary_code="approval_requested")
    assert out["status"] == "created"
    (r,) = await rows(factory)
    assert r["id"] == out["id"] and r["state"] == "unread" and r["count"] == 1
    assert (r["team_id"], r["user_id"], r["kind"]) == ("t1", "ann", "approval")
    assert (r["source_kind"], r["source_id"], r["summary_code"]) == (
        "approval", "a-1", "approval_requested")
    # the scope a run lives in is filled in so per-session / per-workspace settings can apply
    assert (r["run_id"], r["session_id"], r["workspace_id"]) == ("r1", "s1", "w1")
    assert not r["urgent"] and r["dedupe_key"] == "approval:a-1"
    assert json.loads(r["delivery"]) == {"inapp": "delivered", "email": {
        "state": "none", "reason": "not_opted_in", "dueAt": None}}
    assert r["email_state"] == "none" and r["snoozed_until"] is None
    # every stored value is an id, a flag, a number, a timestamp or a closed word
    texts = {k: v for k, v in dict(r).items() if isinstance(v, str)}
    assert len(json.dumps(texts)) < 600


async def test_summary_code_is_closed_per_kind(env):
    factory, _ = env
    await send(factory, source_id="1", summary_code="free text from a comment")
    await send(factory, source_id="2", kind="mention", source_kind="comment",
               summary_code="approval_requested")  # another kind's code
    await send(factory, source_id="3", kind="handoff", source_kind="handoff",
               summary_code="handoff_rejected")
    got = {r["source_id"]: r["summary_code"] for r in await rows(factory)}
    assert got == {"1": "approval_requested", "2": "mentioned", "3": "handoff_rejected"}


async def test_row_is_atomic_with_the_callers_transaction(env):
    factory, _ = env
    async with factory() as db:
        await db.execute(text("INSERT INTO marker (id) VALUES (NULL)"))
        assert (await notify.emit(db, team_id="t1", user_id="ann", kind="mention",
                                  source_kind="comment", source_id="c1"))["status"] == "created"
        await db.rollback()  # the caller's change is undone, so is the notification
    assert await rows(factory) == []


# ------------------------------------------------------------------ never raises, same team


async def test_bad_input_is_rejected_not_raised(env):
    factory, _ = env
    cases = [
        {"kind": "gossip"}, {"kind": None}, {"source_id": ""}, {"source_id": "x" * 81},
        {"user_id": 7}, {"run_id": "r" * 37}, {"dedupe_key": "k" * 121}, {"dedupe_key": ""},
        {"source_kind": "k" * 33},
    ]
    for kw in cases:
        out = await send(factory, **kw)
        assert out["status"] == "rejected", kw
    assert await rows(factory) == []


async def test_a_broken_store_never_raises_and_leaves_the_callers_work_intact(env):
    factory, _ = env
    async with factory() as db:
        await db.execute(text("DROP TABLE notification_inbox"))
        await db.execute(text("INSERT INTO marker (id) VALUES (42)"))
        out = await notify.emit(db, team_id="t1", user_id="ann", kind="mention",
                                source_kind="comment", source_id="c1")
        assert out == {"status": "error"}
        await db.commit()
    async with factory() as db:
        assert (await db.execute(text("SELECT id FROM marker"))).scalar() == 42


async def test_same_team_only(env):
    factory, _ = env
    assert (await send(factory, user_id="eve"))["reason"] == "not_in_team"  # eve is in t2
    assert (await send(factory, user_id="nobody"))["reason"] == "not_in_team"
    for kw in ({"run_id": "rx"}, {"run_id": "nope"}, {"session_id": "sx"}, {"session_id": "nope"},
               {"workspace_id": "wx"}, {"workspace_id": "nope"}):
        out = await send(factory, **kw)
        assert out == {"status": "rejected", "reason": "foreign_scope"}, kw
    assert await rows(factory) == []
    # the same ids are fine for the team that owns them
    assert (await send(factory, run_id="r2", workspace_id="w2"))["status"] == "created"


# ------------------------------------------------------------------ preferences (003)


async def test_in_app_off_suppresses_per_event_and_per_scope(env):
    factory, _ = env
    await prefs(factory, events={"mention": {"inapp": "off"}})
    assert (await send(factory, kind="mention", source_kind="comment")) == {
        "status": "suppressed", "reason": "inapp_off"}
    assert (await send(factory))["status"] == "created"  # other kinds still arrive
    # a workspace can turn everything off, a session inside it can turn one kind back on
    await prefs(factory, scope=("workspace", "w1"), inapp="off")
    await prefs(factory, scope=("session", "s1"), events={"approval": {"inapp": "on"}})
    assert (await send(factory, source_id="a-2", run_id="r1"))["status"] == "created"
    out = await send(factory, kind="failure", source_kind="run", source_id="r1", run_id="r1")
    assert out["reason"] == "inapp_off"
    # outside that workspace the workspace setting does not apply
    assert (await send(factory, kind="failure", source_kind="run", source_id="r2",
                       workspace_id="w2"))["status"] == "created"


async def test_precedence_end_to_end_decides_the_email_channel(env):
    factory, _ = env
    await prefs(factory, email="digest", events={"approval": {"email": "immediate"}})
    await prefs(factory, scope=("workspace", "w1"), email="off")
    await prefs(factory, scope=("session", "s1"), events={"approval": {"email": "urgent"}})

    async def state(**kw) -> str:
        out = await send(factory, **kw)
        return (await rows(factory, "id=:i", i=out["id"]))[0]["email_state"]

    # no scope: global (approval -> immediate, others -> digest)
    assert await state(source_id="1") == "pending"
    assert await state(source_id="2", kind="mention", source_kind="comment") == "digest"
    # workspace w1 default 'off' beats the global per-event entry
    assert await state(source_id="3", workspace_id="w1") == "none"
    # session s1: per-event 'urgent' wins, a non-urgent approval therefore sends no mail
    assert await state(source_id="4", session_id="s1") == "none"
    assert await state(source_id="5", session_id="s1", urgent=True) == "pending"


async def test_preferences_of_another_person_never_apply(env):
    factory, _ = env
    await prefs(factory, user="bob", inapp="off", email="immediate")
    assert (await send(factory, user_id="ann"))["status"] == "created"
    assert (await send(factory, user_id="bob"))["status"] == "suppressed"


# ------------------------------------------------------------------ mute (009)


async def test_mute_silences_non_urgent_events_only(env):
    factory, clock = env
    await mute(factory, "run", "r1")
    await mute(factory, "kind", "mention")
    await mute(factory, "workspace", "w2")
    assert (await send(factory, run_id="r1"))["reason"] == "muted"
    assert (await send(factory, kind="mention", source_kind="comment"))["reason"] == "muted"
    assert (await send(factory, workspace_id="w2"))["reason"] == "muted"
    assert (await send(factory, run_id="r2", source_id="x"))["status"] == "created"  # other run
    # urgent bypasses a mute (it still only lands in the inbox)
    assert (await send(factory, run_id="r1", urgent=True, source_id="u"))["status"] == "created"
    # a session mute covers the runs inside that session
    await mute(factory, "session", "s2")
    assert (await send(factory, session_id="s2", source_id="y"))["reason"] == "muted"


async def test_timed_and_ended_mutes_do_not_apply(env):
    factory, clock = env
    await mute(factory, "kind", "failure", until=T0 + timedelta(hours=1))
    await mute(factory, "kind", "blocked", ended=T0 - timedelta(hours=1))
    kw = {"kind": "failure", "source_kind": "run", "source_id": "r1"}
    assert (await send(factory, **kw))["reason"] == "muted"
    clock.advance(minutes=61)
    assert (await send(factory, **kw))["status"] == "created"
    assert (await send(factory, kind="blocked", source_kind="run",
                       source_id="r1"))["status"] == "created"  # ended mute is history only


async def test_a_mute_of_another_person_does_not_apply(env):
    factory, _ = env
    await mute(factory, "kind", "approval", user="bob")
    assert (await send(factory, user_id="ann"))["status"] == "created"
    assert (await send(factory, user_id="bob"))["reason"] == "muted"


# ------------------------------------------------------------------ dedupe and grouping (005)


async def test_dedupe_key_makes_a_repeat_a_no_op(env):
    factory, _ = env
    assert (await send(factory, dedupe_key="approval:a-1"))["status"] == "created"
    assert (await send(factory, dedupe_key="approval:a-1")) == {"status": "duplicate"}
    assert len(await rows(factory)) == 1 and (await rows(factory))[0]["count"] == 1
    assert (await send(factory, user_id="bob", dedupe_key="approval:a-1"))["status"] == "created"
    assert (await send(factory, dedupe_key="approval:a-2"))["status"] == "grouped"
    # a retry of an event that was folded into a group must not count twice
    assert (await send(factory, dedupe_key="approval:a-2")) == {"status": "duplicate"}
    got = {r["user_id"]: r["count"] for r in await rows(factory)}
    assert got == {"ann": 2, "bob": 1}


async def test_dedupe_survives_dismissal(env):
    factory, _ = env
    first = await send(factory, dedupe_key="k1")
    async with factory() as db:
        await db.execute(text("UPDATE notification_inbox SET state='dismissed' WHERE id=:i"),
                         {"i": first["id"]})
        await db.commit()
    assert (await send(factory, dedupe_key="k1")) == {"status": "duplicate"}


async def test_a_burst_of_one_kind_in_one_scope_is_one_row_with_a_count(env):
    factory, clock = env
    kw = {"run_id": "r2", "kind": "failure", "source_kind": "run"}
    first = await send(factory, source_id="e1", **kw)
    for i in (2, 3):
        clock.advance(minutes=1)
        out = await send(factory, source_id=f"e{i}", **kw)
        assert out == {"status": "grouped", "id": first["id"]}
    (r,) = await rows(factory)
    assert r["count"] == 3 and r["source_id"] == "e3" and r["state"] == "unread"
    assert str(r["updated_at"]).startswith("2030-01-01 12:02")
    assert r["created_at"] != r["updated_at"]
    # another run, another kind, another person: separate rows
    await send(factory, source_id="o1", run_id="r1", kind="failure", source_kind="run")
    await send(factory, source_id="o2", run_id="r2", kind="blocked", source_kind="run")
    await send(factory, source_id="o3", user_id="bob", **kw)
    assert len(await rows(factory)) == 4


async def test_grouping_window_read_state_and_urgency(env):
    factory, clock = env
    kw = {"run_id": "r2", "kind": "failure", "source_kind": "run"}
    a = await send(factory, source_id="e1", **kw)
    clock.advance(minutes=11)  # past the 10 minute default window
    b = await send(factory, source_id="e2", **kw)
    assert b["status"] == "created" and b["id"] != a["id"]
    async with factory() as db:
        await db.execute(text("UPDATE notification_inbox SET state='read' WHERE id=:i"),
                         {"i": b["id"]})
        await db.commit()
    c = await send(factory, source_id="e3", **kw)  # the user already looked: a fresh row
    assert c["status"] == "created" and c["id"] not in (a["id"], b["id"])
    d = await send(factory, source_id="e4", urgent=True, **kw)  # urgent never hides in a calm group
    assert d["status"] == "created" and d["id"] != c["id"]


async def test_group_window_is_configurable(env, monkeypatch):
    factory, clock = env
    monkeypatch.setenv(notify.GROUP_SECONDS_ENV, "0")
    kw = {"run_id": "r2", "kind": "failure", "source_kind": "run"}
    await send(factory, source_id="e1", **kw)
    clock.advance(seconds=1)
    assert (await send(factory, source_id="e2", **kw))["status"] == "created"
    monkeypatch.setenv(notify.GROUP_SECONDS_ENV, "garbage")
    assert notify.group_window() == timedelta(seconds=notify.DEFAULT_GROUP_SECONDS)


# ------------------------------------------------------------------ email channel (002, 004)


async def email_of(factory, out) -> dict:
    r = (await rows(factory, "id=:i", i=out["id"]))[0]
    return {"state": r["email_state"], "due": r["email_due_at"],
            **json.loads(r["delivery"])["email"]}


async def test_email_is_off_by_default_and_for_every_kind(env):
    factory, _ = env
    for i, kind in enumerate(notify.KINDS):
        out = await send(factory, kind=kind, source_kind="x", source_id=str(i), urgent=bool(i % 2))
        mail = await email_of(factory, out)
        assert mail["state"] == "none" and mail["reason"] == "not_opted_in", kind


@pytest.mark.parametrize("mode,urgent,state,reason", [
    ("off", True, "none", "not_opted_in"),
    ("urgent", False, "none", "urgent_only"),
    ("urgent", True, "pending", "immediate"),
    ("immediate", False, "pending", "immediate"),
    ("immediate", True, "pending", "immediate"),
    ("digest", False, "digest", "digest"),
    ("digest", True, "pending", "immediate"),  # urgent escalates out of the digest
])
async def test_email_modes(env, mode, urgent, state, reason):
    factory, _ = env
    await prefs(factory, email=mode)
    mail = await email_of(factory, await send(factory, urgent=urgent))
    assert (mail["state"], mail["reason"]) == (state, reason)


async def test_quiet_hours_are_judged_in_the_users_zone_and_urgent_bypasses(env):
    factory, clock = env
    await prefs(factory, email="immediate", timezone=KARACHI, quiet_enabled=True,
                quiet_start="22:00", quiet_end="07:00")
    # 12:00 UTC is 17:00 in Karachi: not quiet
    assert (await email_of(factory, await send(factory, source_id="1")))["reason"] == "immediate"
    # 18:00 UTC is 23:00 in Karachi: quiet. The mail is held until 07:00 local = 02:00 UTC
    clock.t = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)
    calm = await email_of(factory, await send(factory, source_id="2"))
    assert (calm["state"], calm["reason"]) == ("pending", "quiet_hours")
    assert calm["dueAt"] == "2030-01-02T02:00:00+00:00"
    # urgent bypasses quiet hours and is due now
    hot = await email_of(factory, await send(factory, source_id="3", urgent=True))
    assert (hot["state"], hot["reason"]) == ("pending", "urgent_bypass")
    assert hot["dueAt"] == "2030-01-01T18:00:00+00:00"
    # 03:00 local the next night: still held, ends the same local morning
    clock.t = datetime(2030, 1, 1, 22, 0, tzinfo=UTC)
    late = await email_of(factory, await send(factory, source_id="4", kind="mention",
                                              source_kind="comment"))
    assert late["dueAt"] == "2030-01-02T02:00:00+00:00"
    # quiet hours never silence the inbox itself: all rows are there and unread
    assert {r["state"] for r in await rows(factory)} == {"unread"} and len(await rows(factory)) == 4


async def test_quiet_hours_without_a_valid_zone_fall_back_to_utc(env):
    factory, clock = env
    await prefs(factory, email="immediate", timezone="Mars/Olympus", quiet_enabled=True,
                quiet_start="11:00", quiet_end="13:00")
    mail = await email_of(factory, await send(factory))  # 12:00 UTC is inside 11:00-13:00 UTC
    assert mail["reason"] == "quiet_hours" and mail["dueAt"] == "2030-01-01T13:00:00+00:00"


@pytest.mark.parametrize("now,tz,start,end,expected", [
    (datetime(2030, 1, 1, 18, 0, tzinfo=UTC), KARACHI, "22:00", "07:00",
     datetime(2030, 1, 2, 2, 0, tzinfo=UTC)),
    (datetime(2030, 1, 1, 22, 0, tzinfo=UTC), KARACHI, "22:00", "07:00",
     datetime(2030, 1, 2, 2, 0, tzinfo=UTC)),
    (datetime(2030, 1, 1, 12, 0, tzinfo=UTC), KARACHI, "22:00", "07:00", None),
    (datetime(2030, 1, 1, 12, 0, tzinfo=UTC), "UTC", "11:00", "13:00",
     datetime(2030, 1, 1, 13, 0, tzinfo=UTC)),
    (datetime(2030, 1, 1, 7, 0, tzinfo=UTC), "UTC", "22:00", "07:00", None),
    (datetime(2030, 1, 1, 22, 0, tzinfo=UTC), "UTC", "22:00", "07:00",
     datetime(2030, 1, 2, 7, 0, tzinfo=UTC)),
    (datetime(2030, 1, 15, 3, 0, tzinfo=UTC), "America/New_York", "22:00", "07:00",
     datetime(2030, 1, 15, 12, 0, tzinfo=UTC)),
    (datetime(2030, 7, 15, 3, 0, tzinfo=UTC), "America/New_York", "22:00", "07:00",
     datetime(2030, 7, 15, 11, 0, tzinfo=UTC)),
    (datetime(2030, 1, 1, 12, 0, tzinfo=UTC), "UTC", "10:00", "10:00", None),
    (datetime(2030, 1, 1, 12, 0, tzinfo=UTC), "UTC", "bad", "10:00", None),
])
def test_quiet_until_table_matches_section1(now, tz, start, end, expected):
    # the same table runs in section1 tests/test_notifications_delivery.py
    assert notify.quiet_until(now, tz, start, end) == expected
