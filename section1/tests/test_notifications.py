"""Wave 9 node D — notification templates, outbox, delivery worker tests.

All offline: dev-mode delivery (no HARNESS_SMTP_HOST) logs visibly and marks
rows sent; SMTP behavior is exercised through a fake smtplib.SMTP double.
No test here touches a real network or edits another node's files (fixtures
are defined locally — conftest.py is frozen this wave).
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_contracts.events import EventEnvelope, EventType
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.eventbus import EventBus
from harness_identity.models import Base
from harness_identity.notifier import (
    OUTBOX_MAX_ATTEMPTS,
    TEMPLATES,
    NotificationOutbox,
    NotificationWorker,
    NotifierDelivery,
    build_notification_context,
    default_recipient,
    drain_outbox,
    enqueue,
    event_to_template,
    make_event_notifier,
    render_template,
    send_template,
)

DEV_SETTINGS = SimpleNamespace(
    database_url="sqlite://",
    smtp_host="",  # empty host => dev mode
    smtp_port=587,
    smtp_username="",
    smtp_password="",
    smtp_from="harness@test.local",
    smtp_starttls=True,
)


async def _outbox_rows(factory):
    async with factory() as session:
        rows = (
            (await session.execute(select(NotificationOutbox).order_by(NotificationOutbox.id)))
            .scalars()
            .all()
        )
        for row in rows:
            session.expunge(row)
        return rows


# ---------------------------------------------------------------- fixtures


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


# ------------------------------------------------------------- templates


def test_registry_has_graph_mandated_templates():
    assert {"run_finished", "review_failed", "pr_opened"} <= set(TEMPLATES)


def test_run_finished_subject_contains_key_fields():
    subject, html, plain = render_template(
        "run_finished",
        {"run_id": "run-777", "project_id": "proj-1", "summary": "All green."},
    )
    assert "run-777" in subject
    assert "proj-1" in plain
    assert "All green." in plain
    assert "<h2>" in html and "</html>" in html
    assert "<" not in plain  # plain body carries no markup


def test_review_failed_subject_contains_findings():
    subject, _html, plain = render_template(
        "review_failed",
        {
            "run_id": "run-9",
            "project_id": "proj-2",
            "findings": "tests failing: 3",
        },
    )
    assert "run-9" in subject.lower() or "run-9" in subject
    assert "tests failing: 3" in plain
    assert "tests failing: 3" in _html


def test_pr_opened_subject_contains_pr_number():
    subject, html, plain = render_template(
        "pr_opened", {"run_id": "run-3", "pr_number": 41, "pr_url": "https://git/pr/41"}
    )
    assert "#41" in subject
    assert "https://git/pr/41" in html
    assert "https://git/pr/41" in plain


def test_html_body_escapes_injected_markup():
    _, html, _plain = render_template(
        "run_finished",
        {
            "run_id": "<script>x</script>",
            "project_id": "p",
            "summary": "fine",
        },
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_unknown_template_raises_value_error():
    try:
        render_template("nope", {})
    except ValueError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("unknown template must raise ValueError")


def test_malformed_context_missing_key_raises_value_error():
    try:
        render_template("run_finished", {"run_id": "only-run-id"})
    except ValueError as exc:
        assert "project_id" in str(exc)
    else:
        raise AssertionError("missing context key must raise ValueError")


# ------------------------------------------------------- event -> template


def test_event_type_mapping_uses_contract_names():
    assert event_to_template(EventType.ENGINE_COMPLETED) == "run_finished"
    assert event_to_template(EventType.REVIEW_FAILED) == "review_failed"
    assert event_to_template(EventType.PR_OPENED) == "pr_opened"
    assert event_to_template("plan_proposed") is None
    assert event_to_template(EventType.PAUSED) is None


def test_build_context_survives_sparse_payloads():
    ctx = build_notification_context(EventType.ENGINE_COMPLETED, "p1", {}, None)
    assert ctx["run_id"] == "unknown" and ctx["project_id"] == "p1"
    assert "completed" in ctx["summary"].lower()


def test_build_context_pr_opened_reads_nested_pr():
    ctx = build_notification_context(
        EventType.PR_OPENED,
        "p1",
        {"runId": "r2", "pr": {"number": 7, "url": "u"}},
        None,
    )
    assert ctx == {
        "run_id": "r2",
        "project_id": "p1",
        "pr_number": "7",
        "pr_url": "u",
    }


# ------------------------------------------------------------- outbox flow


async def test_enqueue_persists_queued_row():
    factory = await _make_factory()
    row = await enqueue(
        factory, "ops@x.dev", "review_failed", {"run_id": "r1", "project_id": "p", "findings": "f"}
    )
    assert row.status == "queued"
    assert row.attempts == 0
    assert row.sent_at is None
    assert row.last_error is None
    rows = await _outbox_rows(factory)
    assert len(rows) == 1 and rows[0].template == "review_failed"


async def test_enqueue_rejects_malformed_context_without_inserting():
    factory = await _make_factory()
    try:
        await enqueue(factory, "ops@x.dev", "run_finished", {"run_id": "r"})  # missing project_id
    except ValueError:
        pass
    else:
        raise AssertionError("enqueue must validate context")
    rows = await _outbox_rows(factory)
    assert rows == []  # nothing poisoned the queue


async def test_enqueue_then_drain_marks_sent_in_dev_mode(capsys):
    factory = await _make_factory()
    await enqueue(
        factory,
        "dev@x.dev",
        "run_finished",
        {"run_id": "run-5", "project_id": "p", "summary": "done"},
    )
    summary = await drain_outbox(factory, settings=DEV_SETTINGS)
    assert summary["sent"] == 1 and summary["failed"] == 0
    (row,) = await _outbox_rows(factory)
    assert row.status == "sent"
    assert row.attempts == 1
    assert row.sent_at is not None
    assert row.last_error is None
    err = capsys.readouterr().err
    assert "HARNESS-NOTIFY" in err  # dev delivery must stay visible
    assert "dev@x.dev" in err and "done" in err


async def test_retry_path_fails_honestly_with_last_error_after_three_attempts():
    factory = await _make_factory()
    await enqueue(
        factory,
        "ops@x.dev",
        "run_finished",
        {"run_id": "r", "project_id": "p", "summary": "s"},
    )

    class _Down(NotifierDelivery):
        dev_mode = False

        def __init__(self):  # noqa: D107 — deliberately skips settings plumbing
            pass

        async def deliver(self, *_a):
            return False, "SMTPConnectError: relay down"

    for attempt in range(1, OUTBOX_MAX_ATTEMPTS):
        summary = await drain_outbox(factory, delivery=_Down())
        assert summary["retried"] == 1 and summary["failed"] == 0
        (row,) = await _outbox_rows(factory)
        assert row.status == "queued"  # still under the cap => retry later
        assert row.attempts == attempt
        assert "relay down" in row.last_error

    summary = await drain_outbox(factory, delivery=_Down())
    assert summary["failed"] == 1
    (row,) = await _outbox_rows(factory)
    assert row.status == "failed"
    assert row.attempts == OUTBOX_MAX_ATTEMPTS
    assert "relay down" in row.last_error
    assert row.sent_at is None


async def test_exhausted_rows_are_not_rescanned():
    factory = await _make_factory()
    async with factory() as session:
        session.add(
            NotificationOutbox(
                to_email="z@x.dev", template="pr_opened", attempts=OUTBOX_MAX_ATTEMPTS
            )
        )
        await session.commit()

    class _Spy:
        dev_mode = False
        calls = 0

        @staticmethod
        async def deliver(*_a):
            _Spy.calls += 1
            return True, None

    summary = await drain_outbox(factory, delivery=_Spy())
    assert summary["scanned"] == 0 and _Spy.calls == 0


async def test_malformed_payload_fails_row_gracefully_not_batch():
    factory = await _make_factory()
    async with factory() as session:
        # Row written directly with a context that cannot render.
        session.add(
            NotificationOutbox(
                to_email="bad@x.dev", template="run_finished", payload={"run_id": "only"}
            )
        )
        await session.commit()
    summary = await drain_outbox(factory, settings=DEV_SETTINGS, max_attempts=1)
    assert summary["sent"] == 0 and summary["failed"] == 1
    (row,) = await _outbox_rows(factory)
    assert row.status == "failed"
    assert row.last_error  # honest reason recorded


async def test_batch_survives_one_bad_row_and_sends_the_rest():
    factory = await _make_factory()
    async with factory() as session:
        session.add(
            NotificationOutbox(to_email="bad@x.dev", template="run_finished", payload={"x": 1})
        )
        await session.commit()
    await enqueue(
        factory, "good@x.dev", "run_finished", {"run_id": "r", "project_id": "p", "summary": "s"}
    )
    summary = await drain_outbox(factory, settings=DEV_SETTINGS, max_attempts=1)
    assert summary["sent"] == 1 and summary["failed"] == 1
    statuses = {r.to_email: r.status for r in await _outbox_rows(factory)}
    assert statuses == {"bad@x.dev": "failed", "good@x.dev": "sent"}


# ------------------------------------------------------------ SMTP honesty


async def test_smtp_delivery_failure_is_returned_not_raised():
    settings = SimpleNamespace(**{**vars(DEV_SETTINGS), "smtp_host": "relay.test"})
    delivery = NotifierDelivery(settings)
    with patch("harness_identity.notifier.smtplib.SMTP", side_effect=OSError("no route")):
        ok, error = await delivery.deliver("a@b.c", "subj", "<p>h</p>", "plain")
    assert ok is False
    assert error and "OSError" in error


async def test_real_smtp_path_sends_multipart_message():
    sent = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def send_message(self, msg):
            sent.append(msg)

    settings = SimpleNamespace(**{**vars(DEV_SETTINGS), "smtp_host": "relay.test"})
    delivery = NotifierDelivery(settings)
    with patch("harness_identity.notifier.smtplib.SMTP", _FakeSMTP):
        ok, error = await delivery.deliver("a@b.c", "subj", "<p>hello</p>", "hello")
    assert ok is True and error is None
    assert len(sent) == 1
    assert sent[0]["To"] == "a@b.c"


async def test_send_template_returns_result_dict_dev_mode():
    result = await send_template(
        "ops@x.dev",
        "pr_opened",
        {"run_id": "r", "pr_number": 9, "pr_url": "u"},
        settings=DEV_SETTINGS,
    )
    assert result["ok"] is True and result["error"] is None
    assert "#9" in result["subject"]


async def test_send_template_unknown_template_raises():
    try:
        await send_template("a@b.c", "mystery", {}, settings=DEV_SETTINGS)
    except ValueError:
        pass
    else:
        raise AssertionError("send_template must surface unrenderable input")


# ------------------------------------------------------------- worker loop


async def test_worker_drains_enqueued_rows_end_to_end():
    factory = await _make_factory()
    worker = NotificationWorker(factory, settings=DEV_SETTINGS, poll_interval=0.05)
    worker.start()
    try:
        await enqueue(
            factory,
            "ops@x.dev",
            "run_finished",
            {"run_id": "run-worker", "project_id": "p", "summary": "s"},
        )
        deadline = asyncio.get_running_loop().time() + 5.0
        while True:
            (row,) = await _outbox_rows(factory)
            if row.status == "sent":
                break
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"worker never drained row (status={row.status})")
            await asyncio.sleep(0.05)
        assert row.attempts == 1 and row.sent_at is not None
    finally:
        worker.stop()
        await asyncio.sleep(0)  # let the cancelled task settle


# --------------------------------------------------- bus wiring (S4 events)


async def _settle_background_tasks():
    """Let fire-and-forget enqueue tasks run to completion before asserting.

    sqlite's aiosqlite pool shares one connection, so a concurrent reader
    session close can roll back an in-flight background writer — production
    Postgres sessions each get their own connection and don't need this.
    """
    for _ in range(50):
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not any("enqueue_row" in repr(t) or "_enqueue_row" in repr(t) for t in tasks):
            return
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)


async def test_bus_event_listener_enqueues_outbox_row(monkeypatch):
    monkeypatch.setenv("HARNESS_NOTIFY_EMAIL", "ops@x.dev")
    factory = await _make_factory()
    bus = EventBus(factory)
    seen = []
    bus.add_event_listener(make_event_notifier(factory, DEV_SETTINGS))
    bus.add_event_listener(seen.append)

    envelope = await bus.publish(
        EventType.ENGINE_COMPLETED,
        "proj-9",
        {"summary": "all steps done"},
        run_id="run-42",
    )
    assert seen[-1] is envelope  # listener tap sees the committed envelope
    await _settle_background_tasks()
    rows = await _outbox_rows(factory)
    (row,) = rows
    assert row.status == "queued"
    assert row.template == "run_finished"
    assert row.to_email == "ops@x.dev"
    assert row.payload["run_id"] == "run-42"


async def test_hook_skips_unmapped_events_and_missing_recipient(monkeypatch):
    factory = await _make_factory()
    monkeypatch.delenv("HARNESS_NOTIFY_EMAIL", raising=False)
    hook = make_event_notifier(factory, DEV_SETTINGS)

    hook(EventEnvelope(type=EventType.PAUSED, payload={}, ts="t", seq=1, projectId="p"))
    hook(
        EventEnvelope(
            type=EventType.ENGINE_COMPLETED, payload={}, ts="t", seq=2, projectId="p"
        )
    )
    await asyncio.sleep(0.05)
    assert await _outbox_rows(factory) == []


async def test_hook_never_raises_on_garbage_input():
    factory = await _make_factory()
    hook = make_event_notifier(factory, DEV_SETTINGS)

    class _Junk:
        type = None
        payload = None
        projectId = None
        runId = None

    hook(_Junk())  # must not raise
    hook(object())


async def test_notify_email_payload_override_wins(monkeypatch):
    monkeypatch.setenv("HARNESS_NOTIFY_EMAIL", "fallback@x.dev")
    assert default_recipient({"notifyEmail": "explicit@x.dev"}) == "explicit@x.dev"
    assert default_recipient({}) == "fallback@x.dev"


async def test_model_matches_migration_columns():
    cols = {c.name for c in NotificationOutbox.__table__.columns}
    assert cols == {
        "id",
        "team_id",
        "to_email",
        "template",
        "payload",
        "status",
        "attempts",
        "last_error",
        "created_at",
        "sent_at",
    }
    sql = Path(__file__).resolve().parents[1].joinpath(
        "migrations", "0009_notification_outbox.sql"
    ).read_text()
    assert "CREATE TABLE notification_outbox" in sql
    assert "CREATE INDEX idx_notification_outbox_status ON notification_outbox (status);" in sql
    assert "'queued', 'sent', 'failed'" in sql
    for token in (
        "VARCHAR(36) PRIMARY KEY",
        "REFERENCES teams (id)",
        "VARCHAR(255) NOT NULL",
        "JSONB NOT NULL",
        "INTEGER DEFAULT 0 NOT NULL",
        "TIMESTAMPTZ DEFAULT now() NOT NULL",
    ):
        assert token in sql, f"migration missing {token!r}"


async def test_no_stray_queued_rows_after_full_suite_flow():
    """Sanity on counting helper itself: fresh factory yields zero rows."""
    async with (await _make_factory())() as session:
        count = (
            await session.execute(select(func.count()).select_from(NotificationOutbox))
        ).scalar_one()
    assert count == 0
