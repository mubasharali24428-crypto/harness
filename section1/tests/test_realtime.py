"""Part 46 (lane R): real-time delivery of collaboration events over the socket.

Chain (``prevSeq``), critical vs ephemeral kinds, schema compatibility, the opt-in ``ready`` frame,
backpressure (send timeout, rate cap), connection and message limits, revoked access, metrics.
SQLite, one event loop, same driver as test_audit_export_and_collab_push.py (whose ``world``
fixture and ``collab_row`` helper are reused).
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
import test_audit_export_and_collab_push as base
from sqlalchemy import text
from ws_driver import MiniWs, WsRejected

from harness_identity import domain_metrics, realtime_metrics
from harness_identity.eventbus import (
    CLOSE_ACCESS_REVOKED,
    CLOSE_RATE_LIMITED,
    CLOSE_SLOW_CONSUMER,
    ConnectionGuard,
    SocketPolicyClose,
)
from harness_identity.models import AgentRun

world = base.world  # the shared fixture: SQLite, two teams, three sessions
URL, collab_row = base.URL, base.collab_row

CONN = "harness_realtime_connections_total"
CLOSED = "harness_realtime_close_total"
RECOVERED = "harness_realtime_recovered_total"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("COLLAB_WS_POLL_SECONDS", "0.05")
    domain_metrics.reset()
    realtime_metrics.reset()


async def raw_row(factory, run, team, kind, *, schema=1, ts=None, **payload):
    async with factory() as s:
        res = await s.execute(
            text(
                "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind, payload,"
                " ts, schema_version) VALUES (:r, :t, 'u1', :k, :p, :ts, :v) RETURNING seq"
            ),
            {"r": run, "t": team, "k": kind, "p": json.dumps(payload), "v": schema,
             "ts": ts or datetime.now(UTC)},
        )
        seq = res.scalar()
        await s.commit()
    return seq


async def hang_up(ws):
    """What a real peer does: the server sees a disconnect and ends the handler itself
    (MiniWs would cancel the handler task on exit, which is a server-side abort)."""
    await ws._in.put({"type": "websocket.disconnect", "code": 1000})
    ws.close_code = 1000
    await asyncio.sleep(0.15)


async def closes_with(ws, timeout=5):
    """Read until the server closes; returns (frames seen, close code)."""
    frames = []
    try:
        while True:
            frames.append(await ws.recv_json(timeout=timeout))
    except WsRejected as stop:
        return frames, stop.code


# --------------------------------------------------------------- chain (002, 003)


async def test_every_frame_names_the_previous_visible_event_of_its_own_run(world):
    f, app = world["factory"], world["app"]
    async with f() as s:
        s.add(AgentRun(id="r3", project_id="p1"))
        await s.commit()
    e1 = await collab_row(f, "r1", "t1", "participant.joined")
    await collab_row(f, "r3", "t1", "participant.joined")  # another run: seq gap, not a gap
    await collab_row(f, "r1", "t1", "guidance.delivered", upTo=e1)  # marker: not in the chain
    await collab_row(f, "r1", "t2", "redirect.guidance", content="forged team")  # not ours
    e2 = await collab_row(f, "r1", "t1", "handoff.requested")
    await collab_row(f, "r3", "t1", "handoff.requested")
    e3 = await collab_row(f, "r1", "t1", "approval.requested")
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        got = await ws.recv_all()
        assert [fr["seq"] for fr in got] == [e1, e2, e3]
        assert [fr["prevSeq"] for fr in got] == [None, e1, e2]
        assert e3 - e1 > 2  # table-global seq: its gaps say nothing, the chain does
        e4 = await collab_row(f, "r1", "t1", "approval.approved")
        live = await ws.recv_json()
        assert (live["seq"], live["prevSeq"]) == (e4, e3)


async def test_a_resuming_client_gets_a_chain_that_starts_at_its_cursor(world):
    f, app = world["factory"], world["app"]
    seqs = [await collab_row(f, "r1", "t1", "participant.joined", n=i) for i in range(5)]
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq={seqs[1]}") as ws:
        got = await ws.recv_all()
    assert [fr["seq"] for fr in got] == seqs[2:]
    assert got[0]["prevSeq"] == seqs[1]  # exactly the cursor: nothing between


async def test_truncated_replay_chain_proves_what_was_skipped(world, monkeypatch):
    f, app = world["factory"], world["app"]
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "3")
    seqs = [await collab_row(f, "r1", "t1", "participant.joined", n=i) for i in range(8)]
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        got = await ws.recv_all()
    frames = [fr for fr in got if "seq" in fr]
    assert [fr["seq"] for fr in frames] == seqs[-3:]
    assert frames[0]["prevSeq"] == seqs[-4]  # an event the client never received: a gap, provably
    assert any(fr.get("replayTruncated") for fr in got)
    assert realtime_metrics.value(RECOVERED, "truncated_replay") == 1


# ----------------------------------------------- critical vs ephemeral (007)


async def test_ephemeral_kinds_are_never_part_of_the_durable_stream(world):
    f, app = world["factory"], world["app"]
    a = await raw_row(f, "r1", "t1", "participant.joined")
    for kind in ("presence.heartbeat", "typing.started", "cursor.moved"):
        await raw_row(f, "r1", "t1", kind)
    b = await raw_row(f, "r1", "t1", "handoff.requested")
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0&control=1") as ws:
        got = await ws.recv_all()
        await raw_row(f, "r1", "t1", "presence.heartbeat")  # live too
        assert await ws.recv_all(0.3) == []
    frames = [fr for fr in got if "seq" in fr]
    assert [(fr["seq"], fr["prevSeq"]) for fr in frames] == [(a, None), (b, a)]
    assert got[-1]["headSeq"] == b  # the head ignores them as well


# --------------------------------------------------- schema compatibility (008)


async def test_a_newer_schema_row_keeps_its_place_but_not_its_body_for_old_clients(world):
    f, app = world["factory"], world["app"]
    a = await raw_row(f, "r1", "t1", "participant.joined", who="x")
    b = await raw_row(f, "r1", "t1", "future.kind", schema=2, secret_new_field=1)
    c = await raw_row(f, "r1", "t1", "handoff.requested", to="u3")
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:  # declares nothing: v1
        old = await ws.recv_all()
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0&schemaVersion=2") as ws:
        new = await ws.recv_all()
    assert [fr["seq"] for fr in old] == [a, b, c] == [fr["seq"] for fr in new]
    assert old[1]["payload"] == {"omitted": True, "reason": "schema_unsupported"}
    assert old[1]["schemaVersion"] == 2 and old[1]["prevSeq"] == a and old[2]["prevSeq"] == b
    assert new[1]["payload"] == {"secret_new_field": 1}
    assert old[0]["payload"] == {"who": "x"} and old[2]["payload"] == {"to": "u3"}


# ------------------------------------------------------ ready control frame


async def test_ready_frame_is_opt_in_and_reports_head_and_a_cursor_from_the_future(world):
    f, app = world["factory"], world["app"]
    seqs = [await collab_row(f, "r1", "t1", "participant.joined", n=i) for i in range(2)]
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        assert all("control" not in fr for fr in await ws.recv_all())  # old clients see none
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0&control=1") as ws:
        got = await ws.recv_all()
    ready = got[-1]  # sent only after the whole catch-up
    assert ready["control"] == "ready" and ready["headSeq"] == seqs[-1]
    assert ready["afterSeq"] == 0 and ready["cursorAhead"] is False and ready["schemaVersion"] == 1
    assert [fr["seq"] for fr in got[:-1]] == seqs
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq={seqs[-1] + 50}&control=1") as ws:
        ahead = await ws.recv_all()
    assert len(ahead) == 1 and ahead[0]["cursorAhead"] is True  # purged or restored log


# ------------------------------------------------------- backpressure (006)


class StuckWs(MiniWs):
    """A peer whose TCP window is full: writing a frame never completes."""

    async def __aenter__(self):
        async def send(msg):
            if msg["type"] == "websocket.send":
                await asyncio.Event().wait()
            await self._out.put(msg)

        self._task = asyncio.ensure_future(self.app(self.scope, self._in.get, send))
        await self._in.put({"type": "websocket.connect"})
        first = await asyncio.wait_for(self._out.get(), timeout=5)
        self.accepted = first["type"] == "websocket.accept"
        return self


async def test_a_consumer_that_cannot_take_a_frame_is_closed_and_can_resume(world, monkeypatch):
    monkeypatch.setenv("WS_SEND_TIMEOUT_SECONDS", "0.1")
    f, app = world["factory"], world["app"]
    first = await collab_row(f, "r1", "t1", "participant.joined")
    second = await collab_row(f, "r1", "t1", "handoff.requested")
    async with StuckWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        while True:
            msg = await asyncio.wait_for(ws._out.get(), timeout=5)
            if msg["type"] == "websocket.close":
                break
        ws.close_code = msg["code"]
    assert ws.close_code == CLOSE_SLOW_CONSUMER == 4408
    assert msg.get("reason") == "SLOW_CONSUMER"
    assert realtime_metrics.value(CLOSED, "slow_consumer") == 1
    # nothing was lost: a healthy reconnect from the old cursor receives everything
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws2:
        assert [fr["seq"] for fr in await ws2.recv_all()] == [first, second]
    assert realtime_metrics.value(CONN, "resume") == 0  # afterSeq=0 is a fresh connect


async def test_live_frames_are_paced_by_the_rate_cap_replay_is_not():
    class Ws:
        def __init__(self):
            self.sent = []

        async def send_json(self, frame):
            self.sent.append(frame)

    now = {"t": 100.0}
    naps = []

    async def nap(seconds):
        naps.append(round(seconds, 3))
        now["t"] += seconds

    guard = ConnectionGuard(Ws(), clock=lambda: now["t"], sleep=nap)
    guard._rate, guard._tokens = 10.0, 10.0  # 10 frames per second, a full burst
    for i in range(10):
        await guard.send({"i": i})
    assert naps == []  # the burst is free
    for i in range(10, 15):
        await guard.send({"i": i})
    assert naps and abs(sum(naps) - 0.5) < 0.05  # five more frames cost half a second
    assert [fr["i"] for fr in guard.ws.sent] == list(range(15))  # all delivered, in order
    before = len(naps)
    for i in range(100):
        await guard.send({"i": i}, throttle=False)
    assert len(naps) == before  # replay is a bounded answer: never paced


async def test_guard_closes_a_stuck_peer_with_the_stable_code():
    class Stuck:
        async def send_json(self, frame):
            await asyncio.Event().wait()

    guard = ConnectionGuard(Stuck())
    guard._timeout = 0.05
    with pytest.raises(SocketPolicyClose) as stop:
        await guard.send({"x": 1})
    assert (stop.value.code, stop.value.reason) == (CLOSE_SLOW_CONSUMER, "SLOW_CONSUMER")


# ----------------------------------------------------------- limits (006)


async def test_a_client_that_floods_the_read_only_socket_is_told_RATE_LIMITED(world, monkeypatch):
    monkeypatch.setenv("WS_INBOUND_PER_MINUTE", "3")
    f, app = world["factory"], world["app"]
    await collab_row(f, "r1", "t1", "participant.joined")
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        assert len(await ws.recv_all(0.2)) == 1
        for _ in range(5):
            await ws._in.put({"type": "websocket.receive", "text": "hello"})
        frames, code = await closes_with(ws)
    assert code == CLOSE_RATE_LIMITED == 4429
    final = frames[-1]
    assert final["error"]["code"] == "RATE_LIMITED" and final["retryAfterSeconds"] > 0
    assert realtime_metrics.value(CLOSED, "rate_limited") == 1


async def test_sockets_per_user_are_capped_and_the_slot_is_released_on_close(world, monkeypatch):
    monkeypatch.setenv("WS_MAX_CONNECTIONS_PER_USER", "2")
    app = world["app"]
    async with MiniWs(app, f"{URL}?token=goodtok") as a, MiniWs(app, f"{URL}?token=goodtok") as b:
        assert a.accepted and b.accepted
        async with MiniWs(app, f"{URL}?token=goodtok") as c:
            assert not c.accepted and c.close_code == CLOSE_RATE_LIMITED
        async with MiniWs(app, "/api/v1/ws/p1?token=goodtok") as d:  # the project socket shares it
            assert not d.accepted and d.close_code == CLOSE_RATE_LIMITED
        async with MiniWs(app, f"{URL}?token=engtok") as other:  # another user is not affected
            assert other.accepted
    async with MiniWs(app, f"{URL}?token=goodtok") as again:
        assert again.accepted
    assert realtime_metrics.value(CONN, "refused") == 2


# ------------------------------------------------- scoped, revocable (011)


@pytest.fixture
def quick_recheck(monkeypatch):
    monkeypatch.setenv("WS_REVALIDATE_SECONDS", "0.05")


async def test_removing_the_member_closes_their_collab_socket_not_others(world, quick_recheck):
    f, app = world["factory"], world["app"]
    seq = await collab_row(f, "r1", "t1", "participant.joined")
    async with (
        MiniWs(app, f"{URL}?token=goodtok") as gone,
        MiniWs(app, f"{URL}?token=engtok") as kept,
    ):
        assert [fr["seq"] for fr in await gone.recv_all(0.2)] == [seq]
        await kept.recv_all(0.2)
        async with f() as s:
            await s.execute(text("DELETE FROM team_members WHERE id = 'm1'"))
            await s.commit()
        _frames, code = await closes_with(gone)
        assert code == CLOSE_ACCESS_REVOKED == 4410
        later = await collab_row(f, "r1", "t1", "handoff.requested")
        assert (await kept.recv_json())["seq"] == later  # u3 still reads
    assert realtime_metrics.value(CLOSED, "access_revoked") == 1


async def test_logging_out_everywhere_closes_the_project_socket(world, quick_recheck):
    f, app = world["factory"], world["app"]
    async with MiniWs(app, "/api/v1/ws/p1?token=engtok") as ws:
        assert ws.accepted
        async with f() as s:
            await s.execute(text("DELETE FROM auth_sessions WHERE user_id = 'u3'"))
            await s.commit()
        _frames, code = await closes_with(ws)
    assert code == CLOSE_ACCESS_REVOKED


async def test_expired_sessions_do_not_keep_a_socket_alive(world, quick_recheck):
    f, app = world["factory"], world["app"]
    async with MiniWs(app, f"{URL}?token=engtok") as ws:
        assert ws.accepted
        past = (datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None)
        async with f() as s:
            await s.execute(
                text("UPDATE auth_sessions SET expires_at = :e WHERE user_id = 'u3'"), {"e": past}
            )
            await s.commit()
        _frames, code = await closes_with(ws)
    assert code == CLOSE_ACCESS_REVOKED


async def test_a_run_moved_out_of_the_project_ends_the_run_socket(world, quick_recheck):
    f, app = world["factory"], world["app"]
    async with f() as s:
        s.add(AgentRun(id="r9", project_id="p1"))
        await s.commit()
    async with MiniWs(app, "/api/v1/ws/p1/runs/r9/collab?token=goodtok") as ws:
        assert ws.accepted
        async with f() as s:
            await s.execute(text("UPDATE agent_runs SET project_id = 'p2' WHERE id = 'r9'"))
            await s.commit()
        _frames, code = await closes_with(ws)
    assert code == CLOSE_ACCESS_REVOKED


async def test_an_unreadable_store_keeps_the_socket_open(world, quick_recheck, monkeypatch):
    f, app = world["factory"], world["app"]
    import harness_identity.app as app_module

    async def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(app_module, "principal_still_valid", boom)
    async with MiniWs(app, f"{URL}?token=goodtok") as ws:
        await ws.recv_all(0.3)  # several re-check ticks, all failing
        seq = await collab_row(f, "r1", "t1", "participant.joined")
        assert (await ws.recv_json())["seq"] == seq  # availability over strictness


# ------------------------------------------------------------- metrics (010)


async def test_connects_resumes_recovered_events_lag_and_closes_are_measured(world):
    f, app = world["factory"], world["app"]
    old = datetime.now(UTC) - timedelta(seconds=7)
    seqs = [await raw_row(f, "r1", "t1", "participant.joined", n=i) for i in range(3)]
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq=0") as ws:
        await ws.recv_all(0.2)
        await hang_up(ws)
    async with MiniWs(app, f"{URL}?token=goodtok&afterSeq={seqs[0]}") as ws:  # a reconnect
        assert len(await ws.recv_all(0.2)) == 2
        # no NOTIFY at all: only the poll tick can deliver this one (a lost wake-up)
        await raw_row(f, "r1", "t1", "handoff.requested", ts=old)
        await ws.recv_json()
        await hang_up(ws)
    assert realtime_metrics.value(CONN, "connect") == 1
    assert realtime_metrics.value(CONN, "resume") == 1
    assert realtime_metrics.value(RECOVERED, "resumed_events") == 2
    assert realtime_metrics.value(RECOVERED, "poll_recovered") >= 1
    assert realtime_metrics.value(CLOSED, "client") == 2
    assert realtime_metrics.lag_count() == 1  # live frames only, never replayed ones
    scrape = realtime_metrics.render()
    assert 'harness_event_delivery_lag_seconds_bucket{le="5.0"} 0' in scrape
    assert 'harness_event_delivery_lag_seconds_bucket{le="10.0"} 1' in scrape  # ~7 s late
    assert 'harness_event_delivery_lag_seconds_bucket{le="+Inf"} 1' in scrape
    assert "harness_event_delivery_lag_seconds_count 1" in scrape


def test_metric_labels_are_closed_and_every_series_starts_at_zero():
    realtime_metrics.inc(CONN, "made_up")  # unknown label values are ignored, never new series
    realtime_metrics.inc("harness_unknown_total", "connect")
    realtime_metrics.inc(CONN, "connect", 0)
    scrape = realtime_metrics.render()
    assert "made_up" not in scrape and "harness_unknown_total" not in scrape
    for name, (_help, label, values) in realtime_metrics.COUNTERS.items():
        assert f"# TYPE {name} counter" in scrape
        for v in values:
            assert f'{name}{{{label}="{v}"}} 0' in scrape
    assert "# TYPE harness_event_delivery_lag_seconds histogram" in scrape
    assert realtime_metrics.render().count('le="+Inf"') == 1


# ------------------------------------------------- existing contract still holds


async def test_project_socket_still_replays_and_counts_metrics(world):
    from harness_contracts import EventType

    app = world["app"]
    bus = app.state.event_bus
    await bus.publish(EventType.TERMINAL_OUTPUT, "p1", {"n": 1})
    async with MiniWs(app, "/api/v1/ws/p1?token=goodtok&afterSeq=0") as ws:
        assert len(await ws.recv_all(0.2)) == 1
        await bus.publish(EventType.TERMINAL_OUTPUT, "p1", {"n": 2})
        assert (await ws.recv_json())["payload"] == {"n": 2}
    assert realtime_metrics.value(CONN, "connect") == 1
    assert realtime_metrics.lag_count() == 1
