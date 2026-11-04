"""W17: a saturated live subscriber cannot block or lose the durable log."""

import pytest
from harness_contracts import EventType

from harness_identity.eventbus import EventBus


@pytest.mark.parametrize("size", [0, -1])
def test_subscriber_queue_must_be_bounded(size):
    with pytest.raises(ValueError, match="must be positive"):
        EventBus(None, subscriber_queue_maxsize=size)


async def test_saturated_subscriber_isolated_and_replay_recovers(db):
    bus = EventBus(db, subscriber_queue_maxsize=4)
    slow = await bus.subscribe("w17-project")
    fast = await bus.subscribe("w17-project")
    other = await bus.subscribe("w17-other")
    published = []
    received = []

    for i in range(100):
        envelope = await bus.publish(EventType.EXECUTION_STARTED, "w17-project", {"i": i})
        published.append(envelope.seq)
        received.append((await fast.get()).seq)

    assert slow.qsize() == 4
    assert slow.overflowed is True
    assert fast.overflowed is False
    assert other.empty() and other.overflowed is False
    assert received == published
    replayed = await bus.replay("w17-project", after_seq=0)
    assert [event.seq for event in replayed] == published
    # A reconnect from the last event actually received retrieves every gap.
    last_received = (await slow.get()).seq
    recovered = await bus.replay("w17-project", after_seq=last_received)
    assert [event.seq for event in recovered] == published[1:]
    for project, subscriber in (("w17-project", slow), ("w17-project", fast),
                                ("w17-other", other)):
        await bus.unsubscribe(project, subscriber)


async def test_project_socket_overflow_closes_1013_and_resume_has_no_gap_or_duplicate(
    tmp_path, monkeypatch
):
    """Socket level: a paced consumer behind a burst is closed with CLOSE_OVERFLOW, then a
    resume from the last seq it saw recovers every event exactly once, in order."""
    import test_audit_export_and_collab_push as base
    from ws_driver import MiniWs, WsRejected

    from harness_identity import realtime_metrics
    from harness_identity.eventbus import CLOSE_OVERFLOW

    monkeypatch.setenv("WS_MAX_FRAMES_PER_SECOND", "20")  # live frames are paced
    realtime_metrics.reset()
    async for world in base.world.__wrapped__(tmp_path):
        app, bus = world["app"], world["app"].state.event_bus
        bus._queue_maxsize = 8
        url = "/api/v1/ws/p1?token=goodtok&afterSeq={}"
        seen: list[int] = []
        published: list[int] = []
        async with MiniWs(app, url.format(0)) as ws:
            assert ws.accepted
            for i in range(60):  # far faster than 20 frames/s with an 8-deep queue
                envelope = await bus.publish(EventType.EXECUTION_STARTED, "p1", {"i": i})
                published.append(envelope.seq)
            code = None
            try:
                while True:
                    seen.append((await ws.recv_json(timeout=3))["seq"])
            except WsRejected as stop:
                code = stop.code
        assert code == CLOSE_OVERFLOW == 1013
        assert realtime_metrics.value("harness_realtime_close_total", "slow_consumer") == 1
        assert len(seen) < len(published)  # the overflow really cost live frames
        assert seen == sorted(set(seen))
        async with MiniWs(app, url.format(seen[-1] if seen else 0)) as ws2:
            resumed = [fr["seq"] for fr in await ws2.recv_all()]
        assert seen + resumed == published  # no gap, no duplicate, in order
