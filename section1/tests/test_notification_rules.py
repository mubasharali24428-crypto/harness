"""M12-W2.2 lane G7 (raw-13 #14) — run-terminal notification rules.

Offline tests for notification_rules + the G7 extensions in notifier:
trigger mapping, dedup on replay, admin fan-out with per-user opt-out,
deep-link content, secret hygiene, and never-raise guarantees. conftest.py
is frozen — fixtures are defined locally (same pattern as test_notifications).
"""

import asyncio
from types import SimpleNamespace

from harness_contracts.events import EventEnvelope, EventType
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.models import Base, Role, Team, TeamMember, User, UserSetting
from harness_identity.notification_rules import (
    RUN_TERMINAL_EVENTS,
    enqueue_run_transition,
    is_run_terminal_event,
    make_run_event_rule,
    recipients_for_envelope,
)
from harness_identity.notifier import (
    NOTIFY_EMAIL_DEFAULT,
    NotificationDedup,
    NotificationOutbox,
    _dedup_key,
    get_notify_email_pref,
    render_template,
    run_deep_link,
)

DEV_SETTINGS = SimpleNamespace(smtp_host="", smtp_port=587, smtp_from="harness@test.local")


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _outbox_rows(factory):
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(NotificationOutbox).order_by(NotificationOutbox.created_at)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            session.expunge(row)
        return rows


def _envelope(event_type: EventType, project="proj-1", run="run-42", seq=101, payload=None):
    return EventEnvelope(
        type=event_type,
        payload=dict(payload or {"teamId": "team-1"}),
        ts="t",
        seq=seq,
        projectId=project,
        runId=run,
    )


# ------------------------------------------------------------ trigger rules


def test_three_terminal_events_are_the_rule_triggers():
    assert is_run_terminal_event(EventType.ENGINE_COMPLETED)
    assert is_run_terminal_event(EventType.ENGINE_FAILED)
    assert is_run_terminal_event(EventType.BLOCKED_AWAITING_CLARIFICATION)
    assert is_run_terminal_event("engine_completed")
    assert not is_run_terminal_event(EventType.PLAN_PROPOSED)
    assert not is_run_terminal_event(EventType.PAUSED)
    assert not is_run_terminal_event(EventType.PR_OPENED)
    assert RUN_TERMINAL_EVENTS == frozenset(
        {"engine_completed", "engine_failed", "blocked_awaiting_clarification"}
    )


def test_new_templates_render_with_deep_link_and_honest_status():
    subject, html, plain = render_template(
        "run_failed",
        {
            "run_id": "run-9",
            "project_id": "p",
            "reason": "engine exploded",
            "run_link": run_deep_link("p", "run-9"),
        },
    )
    assert "FAILED" in subject and "run-9" in subject
    assert "/projects/p/runs/run-9" in plain
    assert "/projects/p/runs/run-9" in html
    assert "engine exploded" in plain

    b_subject, _b_html, b_plain = render_template(
        "run_blocked",
        {
            "run_id": "run-10",
            "project_id": "p",
            "detail": "Question: which port?",
            "run_link": run_deep_link("p", "run-10"),
        },
    )
    assert "blocked" in b_subject.lower()
    assert "which port?" in b_plain
    assert "/projects/p/runs/run-10" in b_plain


async def test_enqueue_persists_row_for_each_terminal_event():
    factory = await _make_factory()
    for event_type, template in (
        (EventType.ENGINE_COMPLETED, "run_finished"),
        (EventType.ENGINE_FAILED, "run_failed"),
        (EventType.BLOCKED_AWAITING_CLARIFICATION, "run_blocked"),
    ):
        rows_before = len(await _outbox_rows(factory))
        delivered = await enqueue_run_transition(
            factory,
            _envelope(event_type, payload={"notifyEmail": "ops@x.dev"}),
        )
        assert delivered == ["ops@x.dev"], event_type
        rows = await _outbox_rows(factory)
        assert len(rows) == rows_before + 1
        assert rows[-1].template == template
        assert rows[-1].status == "queued"


async def test_non_terminal_event_enqueues_nothing():
    factory = await _make_factory()
    delivered = await enqueue_run_transition(factory, _envelope(EventType.PAUSED))
    assert delivered == []
    assert await _outbox_rows(factory) == []


# ------------------------------------------------------------------- dedup


def test_dedup_key_uses_event_id_then_seq():
    env = _envelope(EventType.ENGINE_FAILED, seq=7, payload={"teamId": "t", "eventId": "evt-1"})
    assert _dedup_key(env).endswith("|evt-1")
    env_no_id = _envelope(EventType.ENGINE_FAILED, seq=7)
    key = _dedup_key(env_no_id)
    assert key.endswith("|7")
    # Same transition replayed = same envelope identity = same key.
    assert _dedup_key(_envelope(EventType.ENGINE_FAILED, seq=7)) == key
    # A different transition of the same run gets its own key.
    other = _dedup_key(_envelope(EventType.ENGINE_COMPLETED, seq=8))
    assert other != key


def test_notification_dedup_is_bounded_lru():
    guard = NotificationDedup(maxsize=2)
    assert guard.seen("a") is False
    assert guard.seen("a") is True  # replay suppressed
    guard.seen("b")
    guard.seen("c")  # evicts "a"
    assert guard.seen("a") is False
    assert guard.seen("c") is True


async def test_replay_of_same_transition_enqueues_only_once():
    factory = await _make_factory()
    guard = NotificationDedup()
    seen = guard.seen
    env = _envelope(EventType.ENGINE_FAILED, payload={"notifyEmail": "ops@x.dev"})
    first = await enqueue_run_transition(factory, env, dedup_seen=seen)
    replay = await enqueue_run_transition(factory, env, dedup_seen=seen)
    assert first == ["ops@x.dev"]
    assert replay == []  # one email per transition per run
    rows = await _outbox_rows(factory)
    assert len(rows) == 1


async def test_distinct_transitions_of_one_run_each_notify():
    factory = await _make_factory()
    guard = NotificationDedup()
    for seq, kind in ((1, EventType.ENGINE_FAILED), (2, EventType.BLOCKED_AWAITING_CLARIFICATION)):
        delivered = await enqueue_run_transition(
            factory,
            _envelope(kind, seq=seq, payload={"notifyEmail": "ops@x.dev"}),
            dedup_seen=guard.seen,
        )
        assert delivered == ["ops@x.dev"]
    templates = {row.template for row in await _outbox_rows(factory)}
    assert templates == {"run_failed", "run_blocked"}


# ------------------------------------------------- recipient resolution


async def _seed_team(factory, *, admins=1, engineers=0, inactive_admin=False, agent_member=True):
    async with factory() as session:
        team = Team(id="team-1", name="Alpha")
        session.add(team)
        for i in range(admins):
            user = User(id=f"user-admin-{i}", email=f"admin{i}@x.dev", name=f"A{i}")
            session.add(user)
            session.add(
                TeamMember(
                    id=f"tm-admin-{i}",
                    team_id=team.id,
                    user_id=user.id,
                    display_name=user.name,
                    member_type="human",
                    role=Role.ADMIN.value,
                )
            )
        if inactive_admin:
            user = User(id="user-off", email="off@x.dev", name="Off", is_active=False)
            session.add(user)
            session.add(TeamMember(id="tm-off", team_id=team.id, user_id=user.id,
                                   display_name="Off", member_type="human",
                                   role=Role.ADMIN.value))
        for i in range(engineers):
            user = User(id=f"user-eng-{i}", email=f"eng{i}@x.dev", name=f"E{i}")
            session.add(user)
            session.add(
                TeamMember(
                    id=f"tm-eng-{i}",
                    team_id=team.id,
                    user_id=user.id,
                    display_name=user.name,
                    member_type="human",
                    role=Role.ENGINEER.value,
                )
            )
        if agent_member:
            session.add(
                TeamMember(
                    id="tm-agent",
                    team_id=team.id,
                    user_id=None,
                    display_name="bot",
                    member_type="agent",
                    role=Role.ADMIN.value,
                )
            )
        await session.commit()


async def test_recipients_are_active_human_admins_only():
    factory = await _make_factory()
    await _seed_team(factory, admins=2, engineers=1, inactive_admin=True)
    emails = await recipients_for_envelope(factory, _envelope(EventType.ENGINE_FAILED))
    # Engineers excluded, inactive excluded, agent-member (NULL user) excluded.
    assert emails == ["admin0@x.dev", "admin1@x.dev"]


async def test_payload_override_still_wins_over_team_admins():
    factory = await _make_factory()
    await _seed_team(factory)
    emails = await recipients_for_envelope(
        factory,
        _envelope(EventType.ENGINE_COMPLETED, payload={"notifyEmail": "solo@x.dev"}),
    )
    assert emails == ["solo@x.dev"]


async def test_missing_team_means_no_recipients():
    factory = await _make_factory()
    assert await recipients_for_envelope(factory, _envelope(EventType.ENGINE_FAILED)) == []


async def test_corrupt_roster_degrades_to_empty_not_raise():
    class _BoomFactory:
        def __call__(self):
            raise RuntimeError("db gone")

    emails = await recipients_for_envelope(
        _BoomFactory(), _envelope(EventType.ENGINE_FAILED)
    )
    assert emails == []


# --------------------------------------------------- per-user preference


async def test_notify_email_defaults_true_when_row_absent():
    factory = await _make_factory()
    assert NOTIFY_EMAIL_DEFAULT is True
    assert await get_notify_email_pref(factory, "ghost") is True


async def test_user_setting_roundtrip_false_and_true():
    factory = await _make_factory()
    async with factory() as session:
        session.add(UserSetting(user_id="u1", key="notify_email", value="false"))
        await session.commit()
    assert await get_notify_email_pref(factory, "u1") is False

    async with factory() as session:
        row = await session.get(UserSetting, ("u1", "notify_email"))
        row.value = "true"
        await session.commit()
    assert await get_notify_email_pref(factory, "u1") is True


async def test_opted_out_admin_is_filtered_from_fanout():
    factory = await _make_factory()
    await _seed_team(factory, admins=2)
    async with factory() as session:
        session.add(UserSetting(user_id="user-admin-0", key="notify_email", value="false"))
        await session.commit()
    emails = await recipients_for_envelope(
        factory, _envelope(EventType.ENGINE_FAILED)
    )
    assert emails == ["admin1@x.dev"]


# ------------------------------------------------------- content safety


async def test_email_content_has_no_secrets_and_correct_link():
    factory = await _make_factory()
    # The "secrets" below are planted OUTSIDE the template-context fields:
    # they ride in payload keys the rule never reads (simulating an engine
    # that stuffs credentials into odd corners of the bus payload).
    planted = {"apiKey": "sk-secret", "smtpPassword": "password123"}
    payloads = [
        {"teamId": "t", "summary": "done"},
        {"teamId": "t", "reason": "boom"},
        {"teamId": "t", "question": "hmm"},
        {"teamId": "t"},  # sparse
    ]
    for i, payload in enumerate(payloads):
        kind = (
            EventType.ENGINE_COMPLETED
            if i == 0
            else EventType.ENGINE_FAILED
            if i == 1
            else EventType.BLOCKED_AWAITING_CLARIFICATION
        )
        await enqueue_run_transition(
            factory,
            _envelope(kind, seq=200 + i, payload=payload | {"notifyEmail": "o@x.dev"} | planted),
        )
    rows = await _outbox_rows(factory)
    assert len(rows) == len(payloads)
    for row in rows:
        blob = repr(row.payload)
        for secret in planted.values():
            assert secret not in blob, f"{row.template} leaked {secret}"
        link = run_deep_link(row.payload["project_id"], row.payload["run_id"])
        assert link.startswith("/projects/")
        assert "/runs/" in link


async def test_hook_factory_never_raises_on_garbage_without_loop():
    factory = await _make_factory()
    rule = make_run_event_rule(factory)

    class _Junk:
        type = None
        payload = None
        projectId = None
        runId = None
        seq = None

    try:
        rule(_Junk())  # no running loop => skip quietly
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"rule hook raised on garbage input: {exc}") from exc


async def test_bus_wired_rule_end_to_end_via_publish(monkeypatch):
    """Integration: a real EventBus publish lands exactly one outbox row."""
    monkeypatch.setenv("HARNESS_NOTIFY_EMAIL", "")
    from harness_identity.eventbus import EventBus

    factory = await _make_factory()
    bus = EventBus(factory)
    bus.add_event_listener(make_run_event_rule(factory))

    async with factory() as session:
        session.add(Team(id="team-bus", name="B"))
        user = User(id="user-bus", email="bus-admin@x.dev", name="B")
        session.add(user)
        session.add(
            TeamMember(
                id="tm-bus", team_id="team-bus", user_id=user.id,
                display_name="B", member_type="human", role=Role.ADMIN.value,
            )
        )
        await session.commit()

    await bus.publish(
        EventType.ENGINE_FAILED,
        "proj-bus",
        {"teamId": "team-bus", "reason": "kaput"},
        run_id="run-bus",
    )
    for _ in range(50):  # let the fire-and-forget rule task settle
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not any("_enqueue_row" in repr(t) for t in tasks):
            break
        await asyncio.sleep(0.02)
    rows = await _outbox_rows(factory)
    assert len(rows) == 1
    assert rows[0].to_email == "bus-admin@x.dev"
    assert rows[0].template == "run_failed"
    assert rows[0].payload["run_link"] == "/projects/proj-bus/runs/run-bus"


# ------------------------------------- make_event_notifier G7 route split


async def _settle():
    for _ in range(50):
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not any("_enqueue_row" in repr(t) for t in tasks):
            return
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)


async def test_notifier_hook_routes_engine_failed_to_team_admins():
    from harness_identity.notifier import make_event_notifier

    factory = await _make_factory()
    await _seed_team(factory, admins=2)
    hook = make_event_notifier(factory)
    hook(_envelope(EventType.ENGINE_FAILED))
    await _settle()
    rows = await _outbox_rows(factory)
    assert {row.to_email for row in rows} == {"admin0@x.dev", "admin1@x.dev"}
    assert {row.template for row in rows} == {"run_failed"}


async def test_notifier_hook_suppresses_duplicate_envelopes():
    from harness_identity.notifier import make_event_notifier

    factory = await _make_factory()
    await _seed_team(factory)
    hook = make_event_notifier(factory)
    # Unique event identity (real bus seqs never repeat within a process;
    # the module-level guard is intentionally shared, like production's).
    env = _envelope(EventType.ENGINE_FAILED, project="proj-dup", run="run-dup", seq=555)
    hook(env)
    hook(env)  # replay of the SAME committed envelope (same seq)
    await _settle()
    rows = await _outbox_rows(factory)
    assert len(rows) == 1
    assert rows[0].to_email == "admin0@x.dev"


async def test_notifier_hook_keeps_wave9_path_for_review_failed(monkeypatch):
    from harness_identity.notifier import make_event_notifier

    monkeypatch.setenv("HARNESS_NOTIFY_EMAIL", "ops@x.dev")
    factory = await _make_factory()
    hook = make_event_notifier(factory)
    hook(
        EventEnvelope(
            type=EventType.REVIEW_FAILED,
            payload={"findings": {"tests": "2 failing"}},
            ts="t",
            seq=9,
            projectId="proj-1",
            runId="run-77",
        )
    )
    await _settle()
    rows = await _outbox_rows(factory)
    assert len(rows) == 1
    assert rows[0].to_email == "ops@x.dev"
    assert rows[0].template == "review_failed"
    # G7 deep link rides along in the free-text findings field.
    assert "/projects/proj-1/runs/run-77" in str(rows[0].payload["findings"])
