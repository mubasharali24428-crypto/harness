"""M19-N2 (C8) — the bus carries signals, not payloads (pbc1 F3 + F9).

Three laws, pinned at the section-1 delivery seam:

1. Publish enforces a hard payload cap (default 8192 bytes, the NOTIFY
   ceiling) BEFORE any DB write: oversized bodies are refused with
   PayloadTooLargeError and never reach agent_events.
2. The cross-instance wake-up signal is a minimal (seq, projectId, type)
   triple — migration 0029's trigger shape — and the listener path fetches
   committed envelopes TABLE-SIDE on wake instead of trusting a body that
   rode the wire. Proven against a fake notify connection end-to-end:
   signal in -> PK fetch -> full envelope fanned out locally.
3. The dedupe between direct delivery and the notify wake-up stays honest
   and bounded: direct deliveries are remembered, wake-ups for seqs this
   instance already sent are skipped, unknown seqs still fall back to the
   table (which is the source of truth).
"""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from harness_contracts import EventType

from harness_identity.eventbus import (
    BUS_PAYLOAD_MAX_BYTES_DEFAULT,
    EventBus,
    PayloadTooLargeError,
    payload_wire_size,
)


async def make_factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from harness_identity.models import Base

    eng = create_async_engine("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(eng)


# ---------------------------------------------------------------------------
# Law 1 — publish-time payload cap
# ---------------------------------------------------------------------------


async def test_publish_refuses_payload_over_8kb_before_persistence():
    """A >8KB body dies at publish with PayloadTooLargeError and leaves NO
    row behind: the frozen log never holds an undeliverable event."""
    factory = await make_factory()
    bus = EventBus(factory)

    big = {"diffText": "x" * 10_000}  # 10KB body > default cap
    assert payload_wire_size(big) > BUS_PAYLOAD_MAX_BYTES_DEFAULT

    with pytest.raises(PayloadTooLargeError):
        await bus.publish(EventType.DONE_SIGNAL_RECEIVED, "proj-cap", big)

    async with factory() as session:
        from sqlalchemy import func, select

        from harness_identity.models import AgentEvent

        count = (await session.execute(select(func.count()).select_from(AgentEvent))).scalar()
    assert count == 0, "refused payload must not be persisted"


async def test_publish_accepts_payload_at_the_cap(monkeypatch):
    """Boundary honesty: a body just UNDER the cap sails through."""
    monkeypatch.setenv("BUS_PAYLOAD_MAX_BYTES", "1024")
    factory = await make_factory()
    bus = EventBus(factory)

    env = await bus.publish(EventType.DONE_SIGNAL_RECEIVED, "proj-cap", {"d": "y" * 900})
    assert env.seq >= 1
    assert env.payload["d"].startswith("yyy")


async def test_bus_payload_cap_env_knob_is_honored(monkeypatch):
    monkeypatch.setenv("BUS_PAYLOAD_MAX_BYTES", "100")
    factory = await make_factory()
    bus = EventBus(factory)

    with pytest.raises(PayloadTooLargeError):
        await bus.publish(EventType.PAUSED, "proj-knob", {"blob": "z" * 200})

    monkeypatch.setattr("harness_identity.eventbus.BUS_PAYLOAD_MAX_BYTES_DEFAULT", 1)
    # invalid / zero knob falls back to the default law, never disables it
    monkeypatch.setenv("BUS_PAYLOAD_MAX_BYTES", "not-a-number")
    with pytest.raises(PayloadTooLargeError):
        await bus.publish(EventType.PAUSED, "proj-knob", {"blob": "z" * 200})


# ---------------------------------------------------------------------------
# Law 2 — minimal signal + table-side fetch on wake
# ---------------------------------------------------------------------------


class _FakeNotifyConn:
    """Stands in for the asyncpg listener socket: captures the callback and
    replays raw notify payloads into it."""

    def __init__(self) -> None:
        self.callback = None
        self.other_callbacks: dict = {}

    def feed(self, raw: str) -> None:
        self.callback(None, 0, "agent_events", raw)

    def add_listener(self, channel, cb):
        if channel == "agent_events":  # the bus also listens on collab_events (push)
            self.callback = cb
        else:
            self.other_callbacks[channel] = cb
        return None  # old asyncpg sync shape


def test_signal_body_carries_identity_only():
    """The wire format is exactly the three identity keys — no payload, no
    ts, no runId. This is the shape migration 0029's trigger emits."""
    from harness_identity.eventbus import _signal_body
    from harness_identity.eventbus import event_envelope as make_envelope

    envelope = make_envelope(EventType.REVIEW_PASSED, "proj-sig", 41, {"findings": {"big": "body"}})
    body = json.loads(_signal_body(envelope))
    assert set(body) == {"seq", "projectId", "type"}
    assert body == {"seq": 41, "projectId": "proj-sig", "type": "review_passed"}


async def test_listener_wakes_and_fetches_envelope_table_side(monkeypatch):
    """End-to-end round trip through the notify path WITHOUT a real PG:

    a slim signal arrives -> listener coalesces -> fetches the committed row
    from the table -> fans the FULL envelope out to local subscribers.
    """
    factory = await make_factory()
    bus = EventBus(factory)

    # Commit a row directly (simulating another instance's publish).
    async with factory() as session:
        from harness_identity.models import AgentEvent

        row = AgentEvent(
            project_id="proj-wake",
            run_id=None,
            type=EventType.ENGINE_COMPLETED.value,
            payload={"summary": "table-side truth"},
            ts=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        seq = int(row.seq)  # read BEFORE commit: default expire_on_commit expires it
        await session.commit()

    conn = _FakeNotifyConn()
    # Let the listener start on the sqlite fixture: only the DSN gate is
    # faked — the wake→fetch→fan-out path below runs for real against the
    # table this factory owns.
    monkeypatch.setattr(
        "harness_identity.eventbus._pg_dsn", lambda engine: "postgresql://fake-dsn"
    )
    monkeypatch.setattr(
        "asyncpg.connect", lambda *a, **k: _finished_conn(conn), raising=True
    )

    q = await bus.subscribe("proj-wake")  # starts the listener task
    for _ in range(5):  # let the listener task attach its callback
        await asyncio.sleep(0)
        if conn.callback is not None:
            break
    assert conn.callback is not None, "listener must attach its callback"

    # The slim signal — what migration 0029's trigger now sends.
    conn.feed(json.dumps({"seq": seq, "projectId": "proj-wake", "type": "engine_completed"}))

    envelope = await asyncio.wait_for(q.get(), timeout=5)
    assert envelope.seq == seq
    assert envelope.projectId == "proj-wake"
    assert envelope.type == EventType.ENGINE_COMPLETED
    assert envelope.payload == {"summary": "table-side truth"}, (
        "body must come from the table fetch, not the wire"
    )
    await bus.unsubscribe("proj-wake", q)
    if bus._listener_task is not None:
        bus._listener_task.cancel()


class _NeverConnect:
    def __call__(self, *a, **k):
        raise AssertionError("real asyncpg.connect must not be used in this test")


def _finished_conn(fake_conn):
    async def _connect():
        return fake_conn

    return _connect()


async def test_malformed_signals_are_dropped_without_breaking_the_stream(monkeypatch):
    factory = await make_factory()
    bus = EventBus(factory)
    conn = _FakeNotifyConn()
    monkeypatch.setattr(
        "harness_identity.eventbus._pg_dsn", lambda engine: "postgresql://fake-dsn"
    )
    monkeypatch.setattr("asyncpg.connect", lambda *a, **k: _finished_conn(conn), raising=True)

    q = await bus.subscribe("proj-bad")
    for _ in range(5):
        await asyncio.sleep(0)
        if conn.callback is not None:
            break
    assert conn.callback is not None
    for garbage in ("not json", "{}", '{"seq": "x", "projectId": 1}'):
        conn.feed(garbage)
    try:
        await asyncio.wait_for(q.get(), timeout=0.2)
        raised = False
    except TimeoutError:
        raised = True
    assert raised, "garbage signals must never produce envelopes"
    await bus.unsubscribe("proj-bad", q)
    if bus._listener_task is not None:
        bus._listener_task.cancel()


# ---------------------------------------------------------------------------
# Law 3 — dedupe stays honest and bounded
# ---------------------------------------------------------------------------


async def test_direct_delivery_is_remembered_and_notify_wakeup_skipped(monkeypatch):
    """A process that both publishes and subscribes must not double-handle
    its own events: the direct fan-out records the seq, and the wake-up for
    that seq is skipped before any fetch."""
    factory = await make_factory()
    bus = EventBus(factory)
    fetched: list[int] = []

    async def _spy_fetch(seqs):
        fetched.extend(seqs)
        return {}

    monkeypatch.setattr(bus, "_fetch_envelopes", _spy_fetch)

    q = await bus.subscribe("proj-dedup")
    env = await bus.publish(EventType.PAUSED, "proj-dedup", {"i": 1})
    got = await asyncio.wait_for(q.get(), timeout=2)  # direct delivery works
    assert got.seq == env.seq
    assert int(env.seq) in bus._delivered_seqs

    await asyncio.sleep(0.05)  # let any stray wake-up task run
    bus._schedule_fetch_fanout([(int(env.seq), "proj-dedup", "paused")])
    await asyncio.sleep(0.05)
    assert fetched == [], "wake-up for a directly-delivered seq must skip the fetch"
    await bus.unsubscribe("proj-dedup", q)


async def test_unknown_seq_still_fetches_falls_back_to_table(monkeypatch):
    """A seq NOT seen via direct publish is fetched; if the row vanished
    (retention), the wake-up degrades to a logged miss — never a crash."""
    factory = await make_factory()
    bus = EventBus(factory)
    missing_seq = await _next_seq(factory)

    q = await bus.subscribe("proj-gone")
    bus._schedule_fetch_fanout([(missing_seq + 999_999, "proj-gone", "paused")])
    await asyncio.sleep(0.05)  # no exception, subscriber simply gets nothing
    assert q.empty()
    await bus.unsubscribe("proj-gone", q)


async def _next_seq(factory) -> int:
    async with factory() as session:
        from harness_identity.models import AgentEvent

        row = AgentEvent(
            project_id="proj-gone",
            type="paused",
            payload={},
            ts=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        seq = int(row.seq)
        await session.commit()
        return seq
