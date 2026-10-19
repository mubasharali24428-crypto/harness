import asyncio
import inspect
import json
import logging
import os
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import NamedTuple  # M16-X2 unblock: ReplayPage below needs this

from harness_contracts import EventEnvelope, EventType, event_envelope
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import domain_metrics
from .models import AgentEvent, AgentRun

logger = logging.getLogger(__name__)

REPLAY_DEFAULT_LIMIT = 500
REPLAY_MAX_LIMIT = 2000

# M16-X3: hard cap on events sent in ONE replay batch. A reconnecting client
# whose gap exceeds this gets the NEWEST N events plus an explicit truncation
# marker (see ReplayPage) instead of an unbounded dump — or worse, the old
# silent behavior where a fixed ASC window was re-sent forever and the client
# never learned it was missing history.
REPLAY_MAX_EVENTS_DEFAULT = 5000


def _replay_max_events() -> int:
    raw = os.environ.get("REPLAY_MAX_EVENTS", "")
    try:
        value = int(raw)
    except ValueError:
        return REPLAY_MAX_EVENTS_DEFAULT
    return value if value >= 1 else REPLAY_MAX_EVENTS_DEFAULT


# ---------------------------------------------------------------------------
# M19-N2 (C8): the bus carries signals, not payloads (pbc1 F3 + F9).
#
# Two walls fell:
# 1. The 0003 trigger embedded the FULL envelope (payload JSONB included) in
#    every pg_notify — payloads near Postgres's 8000-byte NOTIFY cap failed
#    the insert outright, and every listening process parsed the whole body
#    of every event. Migration 0029 slims the signal to (seq, projectId,
#    type); listeners wake, then FETCH the committed row table-side (one
#    indexed PK read on the listener socket) and fan out the full envelope
#    locally.
# 2. Nothing stopped a producer from parking a huge body (a done-signal diff
#    up to DONE_DIFF_TEXT_MAX) in agent_events.payload. Publish now enforces
#    a hard cap (default 8192 bytes, BUS_PAYLOAD_MAX_BYTES overrides) BEFORE
#    any DB write: oversized producers must ship a reference (path/id) and
#    serve the body via REST/table instead. DONE_DIFF_TEXT_MAX stays what it
#    always was — an HTTP request-validation cap in harness_contracts.limits
#    (frozen, parity-pinned); the bus cap is a separate, section-1-owned
#    delivery law.
# ---------------------------------------------------------------------------

BUS_PAYLOAD_MAX_BYTES_DEFAULT = 8192


class PayloadTooLargeError(ValueError):
    """Publish refused: payload JSON exceeds the bus cap.

    Raised BEFORE persistence so the log never holds a row that cannot be
    delivered whole. Ship a reference and let consumers fetch the body.
    """


def _bus_payload_max_bytes() -> int:
    raw = os.environ.get("BUS_PAYLOAD_MAX_BYTES", "")
    try:
        value = int(raw)
    except ValueError:
        return BUS_PAYLOAD_MAX_BYTES_DEFAULT
    return value if value >= 1 else BUS_PAYLOAD_MAX_BYTES_DEFAULT


def payload_wire_size(payload: dict | None) -> int:
    """UTF-8 byte length of the payload as it is stored and served."""
    try:
        blob = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        blob = json.dumps(payload or {}, separators=(",", ":"), default=repr)
    return len(blob.encode("utf-8"))


def _signal_body(envelope: EventEnvelope) -> str:
    """The compact cross-instance wake-up signal: identity only, no body."""
    return json.dumps(
        {
            "seq": int(envelope.seq),
            "projectId": envelope.projectId,
            "type": str(envelope.type),
        },
        separators=(",", ":"),
    )


def _pg_dsn(engine) -> str | None:
    """libpq DSN for a SQLAlchemy asyncpg engine; None for non-PG engines."""
    try:
        if engine.url.get_backend_name() != "postgresql":
            return None
        return engine.url.render_as_string(hide_password=False).replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )
    except Exception:  # noqa: BLE001 — test doubles may not be real engines
        return None


class ReplayPage(NamedTuple):
    """One bounded slice of replay history, newest-N under the gap cap.

    - envelopes: ascending by seq, never more than replay_max_events of them.
    - truncated + oldest_seq_sent: honest marker for clients whose gap exceeded
      the cap — they now KNOW they missed history older than oldest_seq_sent
      and can refetch from the REST cursor endpoint instead of guessing.
    - has_more: pure pagination flag (gap larger than the caller's limit), no
      data loss implied.
    """

    envelopes: list[EventEnvelope]
    truncated: bool = False
    oldest_seq_sent: int | None = None
    has_more: bool = False


class CollabPage(NamedTuple):
    """One bounded slice of a run's collaboration history (ascending by seq).

    ``truncated`` + ``oldest_seq_sent`` mean the gap exceeded the replay cap and the
    NEWEST events were sent, same honesty rule as :class:`ReplayPage`.
    """

    frames: list[dict]
    truncated: bool = False
    oldest_seq_sent: int | None = None


COLLAB_CHANNEL = "collab_events"
COLLAB_NOISE_KINDS = ("guidance.delivered",)  # engine cursor markers, not product events


def _collab_frame(row, project_id: str | None) -> dict:
    """run_collab_events row -> wire frame. Payload is bounded; bodies over the bus
    cap are replaced by an explicit marker (consumers refetch via collab-history)."""
    payload = row["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    if payload_wire_size(payload) > _bus_payload_max_bytes():
        payload = {"omitted": True, "reason": "payload exceeds the event bound"}
    ts = row["ts"]
    return {
        "channel": "collab",
        "seq": int(row["seq"]),
        "kind": row["kind"],
        "actor": row["actor_user_id"],
        "payload": payload or {},
        "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "runId": row["run_id"],
        "projectId": project_id,
        "teamId": row["team_id"],
        "schemaVersion": int(row["schema_version"] or 1),
    }


def _envelope_from_row(row) -> EventEnvelope:
    """DB row -> wire envelope. Accepts an ORM AgentEvent (attribute access)
    or a keyed mapping row from a streamed column select (same key names)."""
    if isinstance(row, Mapping):
        return EventEnvelope(
            type=row["type"],
            payload=row["payload"] or {},
            ts=row["ts"].isoformat(),
            seq=int(row["seq"]),
            projectId=row["project_id"],
            runId=row["run_id"],
        )
    return EventEnvelope(
        type=row.type,
        payload=row.payload or {},
        ts=row.ts.isoformat(),
        seq=int(row.seq),
        projectId=row.project_id,
        runId=row.run_id,
    )


class EventBus:
    """Section 1 owns DELIVERY: publish -> persist -> fan out to live subscribers.

    Replay is served from agent_events so reconnecting clients never miss events.
    Section 4 owns semantics (which types exist / when they fire).

    Delivery guarantees:
    - Persistence always succeeds or publish raises; the DB row IS the log.
    - Live fan-out is best-effort per subscriber: a slow subscriber's overflow is
      dropped locally and its socket is expected to reconnect via seq-gap
      detection (see app.project_ws). One bad client can never break a publisher.
    - run_id rows are ensured before event insert so FK constraints hold even
      when the orchestrator has not persisted the run itself.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        subscriber_queue_maxsize: int = 1000,
    ) -> None:
        self._sessions = session_factory
        self._queue_maxsize = subscriber_queue_maxsize
        self._subscribers: dict[str, set[asyncio.Queue[EventEnvelope]]] = {}
        self._event_listeners: list = []  # wave9D: sync taps on committed envelopes
        self._lock = asyncio.Lock()
        self._listener_task: asyncio.Task | None = None
        # M19-N2: seqs this instance already delivered via the direct publish
        # path (bounded FIFO), so the notify wake-up path skips re-fetch +
        # re-fan-out of events it sent itself. Clients dedup by seq anyway;
        # this just avoids redundant work when a process both publishes and
        # subscribes to the same project.
        self._delivered_seqs: deque[int] = deque(maxlen=4096)
        self._bg_tasks: set[asyncio.Task] = set()
        # Collaboration push: run_id -> wake events set by the NOTIFY listener (or by an
        # in-process producer via notify_collab). Rows are always re-read from the table,
        # so a missed wake only delays delivery to the next poll tick; it never loses events.
        self._collab_wakers: dict[str, set[asyncio.Event]] = {}

    # ------------------------------------------------------------ pg listener
    # G-LIVE: each service process owns an EventBus instance, but the log is one
    # shared Postgres table. Direct fan-out (below) only reaches subscribers of
    # the publishing instance; the NOTIFY listener delivers cross-instance so
    # every process behaves like a projection of the single log (dsh-style).
    # Duplicates between direct + notify paths are harmless: clients dedup by seq.

    def _maybe_start_listener(self) -> None:
        if self._listener_task is not None and not self._listener_task.done():
            return
        engine = getattr(self._sessions, "kw", {}).get("bind")
        dsn = _pg_dsn(engine)
        if dsn is None:
            return  # sqlite tests: direct fan-out covers same-instance delivery
        self._listener_task = asyncio.get_running_loop().create_task(self._listen(dsn))

    async def _fetch_envelopes(self, seqs: list[int]) -> dict[int, EventEnvelope]:
        """Table-side body fetch for wake-up signals (M19-N2).

        The NOTIFY carries only (seq, project_id, type); the committed row is
        the single source of truth, so listeners read the envelope back from
        agent_events — one indexed PK batch per signal burst.
        """
        if not seqs:
            return {}
        found: dict[int, EventEnvelope] = {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(
                    AgentEvent.seq,
                    AgentEvent.project_id,
                    AgentEvent.run_id,
                    AgentEvent.type,
                    AgentEvent.payload,
                    AgentEvent.ts,
                ).where(AgentEvent.seq.in_(seqs))
            )
            for row in rows.mappings():
                try:
                    found[int(row["seq"])] = _envelope_from_row(row)
                except Exception:  # noqa: BLE001 — one bad row never stalls the rest
                    logger.exception("eventbus: unreadable row for seq %s", row.get("seq"))
        return found

    def _schedule_fetch_fanout(self, signals: list[tuple[int, str, str]]) -> None:
        """Wake → fetch → local fan-out, deduped against direct delivery.

        ``signals`` are raw (seq, projectId, type) triples from pg_notify.
        Envelopes already handed to this instance's subscribers by the direct
        publish path are skipped (clients dedup by seq; this just avoids the
        redundant fetch + queue copy).
        """
        fresh = [s for s in signals if int(s[0]) not in self._delivered_seqs]
        if len(fresh) < len(signals):
            domain_metrics.inc("duplicate", len(signals) - len(fresh))
        if not fresh:
            return

        async def _run() -> None:
            seqs_all = [int(seq) for seq, _pid, _type in fresh]
            fetched: dict[int, EventEnvelope] = await self._fetch_envelopes(seqs_all)
            for seq, _project_id, _type in fresh:
                envelope = fetched.get(int(seq))
                if envelope is None:
                    logger.warning(
                        "eventbus(notify): seq %s vanished before fetch", seq
                    )
                    continue
                self._fanout_local(envelope)

        task = asyncio.get_running_loop().create_task(_run())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _listen(self, dsn: str) -> None:
        import asyncpg

        # Per-connection coalescing state. The closures below are re-created
        # on every reconnect and only ever touch THIS iteration's cells, so
        # the B023 loop-variable warning is a false positive here.
        pending: list[tuple[int, str, str]] = []
        timer: asyncio.TimerHandle | None = None
        loop = asyncio.get_running_loop()

        while True:
            try:
                conn = await asyncpg.connect(dsn)  # noqa: B023 — see note above

                def _flush() -> None:  # noqa: B023 — see note above
                    nonlocal pending, timer
                    if timer is not None:
                        timer.cancel()
                        timer = None
                    if not pending:
                        return
                    batch, pending = pending[:], []
                    self._schedule_fetch_fanout(batch)

                def _on_notify(
                    _conn, _pid, _channel, raw: str, _self=self
                ):  # noqa: B023 — see note above
                    # M19-N2: the wire carries a minimal signal —
                    # {"seq": N, "projectId": "...", "type": "..."} — no body.
                    try:
                        data = json.loads(raw)
                        signal = (
                            int(data["seq"]),
                            str(data["projectId"]),
                            str(data.get("type") or ""),
                        )
                    except Exception:  # noqa: BLE001 — malformed notify, skip
                        return
                    nonlocal pending, timer
                    pending.append(signal)
                    if timer is None:
                        # ~5ms coalescing window: bursts of publishes cost ONE
                        # PK fetch instead of one per event. Delivery latency
                        # stays far below any human-observable threshold.
                        timer = loop.call_later(0.005, _flush)

                def _on_collab_notify(_conn, _pid, _channel, raw: str, _self=self):
                    try:
                        _self.notify_collab(str(json.loads(raw)["runId"]))
                    except Exception:  # noqa: BLE001 — malformed notify, skip
                        return

                maybe_coro = self._attach_listener(conn, _on_notify)
                if maybe_coro is not None:
                    await maybe_coro
                collab_coro = self._attach_listener(conn, _on_collab_notify, COLLAB_CHANNEL)
                if collab_coro is not None:
                    await collab_coro
                await asyncio.Event().wait()  # park forever; callbacks drive us
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — DB restarts etc.; reconnect
                logger.exception("eventbus listener reconnecting")
                await asyncio.sleep(1)

    def _attach_listener(self, conn, callback, channel: str = "agent_events"):
        """asyncpg >=0.30 made add_listener a coroutine; older versions are sync."""
        result = conn.add_listener(channel, callback)
        if inspect.iscoroutine(result):
            return result
        return None

    def _fanout_local(self, envelope: EventEnvelope) -> None:
        dropped = 0
        for q in list(self._subscribers.get(envelope.projectId, ())):
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                dropped += 1
                try:
                    q.overflowed = True
                except AttributeError:
                    pass
        if dropped:
            domain_metrics.inc("dropped", dropped)
            logger.warning(
                "eventbus(notify): dropped %d live deliveries on %s",
                dropped,
                envelope.projectId,
            )

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        """Idempotently create the agent_runs parent row for FK-safe publishing."""
        if not run_id:
            return
        async with self._sessions() as session:
            exists = await session.get(AgentRun, run_id)
            if exists is not None:
                return
            session.add(AgentRun(id=run_id, project_id=project_id))
            try:
                await session.commit()
            except Exception:
                # Lost an insert race with another worker that created it. Fine.
                await session.rollback()

    async def publish(
        self,
        event_type: EventType,
        project_id: str,
        payload: dict | None = None,
        run_id: str | None = None,
    ) -> EventEnvelope:
        # M19-N2 (F9): enforce the bus cap BEFORE any DB write. The frozen
        # log must never hold a row whose body cannot be delivered whole —
        # oversized producers ship a reference (path/id) and serve the body
        # via REST/table instead.
        wire_size = payload_wire_size(payload)
        max_bytes = _bus_payload_max_bytes()
        if wire_size > max_bytes:
            raise PayloadTooLargeError(
                f"event payload is {wire_size} bytes; bus cap is {max_bytes}. "
                "Ship a reference (path/id) and let consumers fetch the body."
            )

        if run_id:
            await self.ensure_run(project_id, run_id)

        async with self._sessions() as session:
            row = AgentEvent(
                project_id=project_id,
                run_id=run_id,
                type=event_type.value,
                payload=payload or {},
                ts=datetime.now(UTC),
            )
            session.add(row)
            # Flush FIRST so seq is loaded while the object is live; a plain
            # commit would expire it (expire_on_commit=True callers) and the
            # later row.seq access would lazy-load outside greenlet context.
            await session.flush()
            envelope = event_envelope(event_type, project_id, int(row.seq), payload, run_id)
            await session.commit()

        async with self._lock:
            queues = list(self._subscribers.get(project_id, ()))
        if queues:
            self._delivered_seqs.append(int(envelope.seq))
        for listener in self._event_listeners:
            try:
                # Wave 9D: side-channel taps (notification outbox). Must never
                # delay or break the publisher's fan-out path.
                listener(envelope)
            except Exception:  # noqa: BLE001 — a broken tap must never break the bus
                logger.exception("eventbus event-listener failed on %s", envelope.type)
        dropped = 0
        for q in queues:
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                dropped += 1
                try:
                    q.overflowed = True
                except AttributeError:
                    pass
        if dropped:
            domain_metrics.inc("dropped", dropped)
            logger.warning(
                "eventbus: dropped %d/%d live deliveries on project %s (slow subscribers)",
                dropped,
                len(queues),
                project_id,
            )
        return envelope

    async def subscribe(self, project_id: str) -> asyncio.Queue[EventEnvelope]:
        """Returns a queue with an ``overflowed`` bool attribute: True once any
        delivery to it was dropped. Consumers (project_ws) use this instead of
        seq arithmetic — seq is table-GLOBAL, so per-project jumps are normal."""
        self._maybe_start_listener()
        q: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=self._queue_maxsize)
        q.overflowed = False
        async with self._lock:
            self._subscribers.setdefault(project_id, set()).add(q)
        return q

    async def unsubscribe(self, project_id: str, q: asyncio.Queue[EventEnvelope]) -> None:
        async with self._lock:
            self._subscribers.get(project_id, set()).discard(q)

    # ------------------------------------------------ collaboration events (push)

    def collab_waker(self, run_id: str) -> asyncio.Event:
        """Register a wake-up event for one run; call ``release_collab_waker`` when done."""
        self._maybe_start_listener()
        ev = asyncio.Event()
        self._collab_wakers.setdefault(run_id, set()).add(ev)
        return ev

    def release_collab_waker(self, run_id: str, ev: asyncio.Event) -> None:
        wakers = self._collab_wakers.get(run_id)
        if wakers is not None:
            wakers.discard(ev)
            if not wakers:
                del self._collab_wakers[run_id]

    def notify_collab(self, run_id: str) -> None:
        """Wake every subscriber of this run (NOTIFY listener or an in-process producer)."""
        for ev in list(self._collab_wakers.get(run_id, ())):
            ev.set()

    _COLLAB_COLS = "seq, run_id, team_id, actor_user_id, kind, payload, ts, schema_version"

    async def _project_of(self, run_id: str) -> str | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text("SELECT project_id FROM agent_runs WHERE id = :r"), {"r": run_id}
                )
            ).first()
        return row.project_id if row else None

    async def collab_replay_page(
        self, run_id: str, team_id: str, after_seq: int = 0
    ) -> CollabPage:
        """Catch-up slice for a (re)connecting client: events with seq > after_seq,
        capped at REPLAY_MAX_EVENTS. A larger gap sends the NEWEST cap events plus an
        honest truncation flag. Team-scoped in SQL, so a foreign team's rows can never
        be returned even for a guessed run id."""
        cap = _replay_max_events()
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"SELECT {self._COLLAB_COLS} FROM run_collab_events"
                            " WHERE run_id = :r AND team_id = :t AND seq > :a"
                            " AND kind NOT IN :noise ORDER BY seq DESC LIMIT :n"
                        ).bindparams(
                            bindparam("noise", expanding=True)
                        ),
                        {
                            "r": run_id, "t": team_id, "a": max(0, int(after_seq)),
                            "noise": list(COLLAB_NOISE_KINDS), "n": cap + 1,
                        },
                    )
                )
                .mappings()
                .all()
            )
        truncated = len(rows) > cap
        rows = list(reversed(rows[:cap]))
        project_id = await self._project_of(run_id)
        frames = [_collab_frame(r, project_id) for r in rows]
        return CollabPage(frames, truncated, frames[0]["seq"] if frames else None)

    async def collab_since(
        self, run_id: str, team_id: str, after_seq: int, limit: int = 200
    ) -> list[dict]:
        """Ascending live slice: events with seq > after_seq (cursor never moves back)."""
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"SELECT {self._COLLAB_COLS} FROM run_collab_events"
                            " WHERE run_id = :r AND team_id = :t AND seq > :a"
                            " AND kind NOT IN :noise ORDER BY seq ASC LIMIT :n"
                        ).bindparams(
                            bindparam("noise", expanding=True)
                        ),
                        {
                            "r": run_id, "t": team_id, "a": int(after_seq),
                            "noise": list(COLLAB_NOISE_KINDS), "n": max(1, min(limit, 500)),
                        },
                    )
                )
                .mappings()
                .all()
            )
        if not rows:
            return []
        project_id = await self._project_of(run_id)
        return [_collab_frame(r, project_id) for r in rows]

    def add_event_listener(self, callback) -> None:
        """Wave 9D: process-wide tap on every published envelope (all event
        types, all projects) for side-channel consumers like the notification
        outbox. Callbacks are plain sync functions invoked AFTER the row is
        committed; they must never raise or block (see notifier.make_event_notifier).
        Live-subscriber fan-out is unaffected."""
        self._event_listeners.append(callback)

    async def replay_page(
        self,
        project_id: str,
        after_seq: int = 0,
        limit: int | None = None,
        *,
        replay_max_events: int | None = None,
    ) -> ReplayPage:
        """M16-X3: bounded, capped, streamed replay slice.

        Contract (raw-16 #7):
        - ``limit=None`` (the WS reconnect path): one batch carries up to the
          FULL cap (env REPLAY_MAX_EVENTS, default 5000) envelopes.
        - Explicit ``limit`` (REST cursor callers): batch is bounded by
          ``min(limit, cap)`` — pure pagination, unchanged legacy sizing.
        - When the GAP exceeds the cap, the NEWEST ``cap`` are sent and
          ``truncated=True`` + ``oldest_seq_sent`` tell the client exactly
          which history was skipped — no silent loss. Truncation signals data
          loss only; small-limit paging keeps ``truncated=False``.
        - Memory bound: rows stream out of the DB cursor in fixed chunks via
          yield_per; the gap is never fully materialized. Only the retained
          newest-N deque is held in RAM.
        """
        if replay_max_events is None:
            replay_max_events = _replay_max_events()
        cap = max(1, int(replay_max_events))
        if limit is None:
            effective = cap
        else:
            limit = max(1, min(int(limit), REPLAY_MAX_LIMIT))
            effective = min(cap, limit)
        chunk_size = max(1, min(effective, 500))

        newest: deque[EventEnvelope] = deque()
        stats = {"seen": 0}

        def _stream(sync_session) -> None:
            # Runs inside session.run_sync on the greenlet worker: the sync
            # cursor streams rows chunk-by-chunk (yield_per), so a 100k-event
            # gap costs O(chunk + cap) RAM, never O(gap). Parameterized ORM
            # query — dialect-safe on asyncpg AND sqlite. Retention/indexes
            # come from existing migrations; no new DDL here.
            stream_stmt = (
                select(
                    AgentEvent.seq,
                    AgentEvent.project_id,
                    AgentEvent.run_id,
                    AgentEvent.type,
                    AgentEvent.payload,
                    AgentEvent.ts,
                )
                .where(AgentEvent.project_id == project_id, AgentEvent.seq > int(after_seq))
                .order_by(AgentEvent.seq.asc())
                .execution_options(yield_per=chunk_size)
            )
            for row in sync_session.execute(stream_stmt).mappings():
                stats["seen"] += 1
                newest.append(_envelope_from_row(row))
                if len(newest) > effective:
                    newest.popleft()

        async with self._sessions() as session:
            await session.run_sync(_stream)

        has_more = stats["seen"] > len(newest)
        # Truncation means DATA LOSS (gap > cap), never mere pagination: a small
        # caller limit cycling through the log honestly is NOT truncation.
        return ReplayPage(
            envelopes=list(newest),
            truncated=stats["seen"] > cap,
            oldest_seq_sent=newest[0].seq if newest else None,
            has_more=has_more,
        )

    async def replay(
        self, project_id: str, after_seq: int = 0, limit: int = REPLAY_DEFAULT_LIMIT
    ) -> list[EventEnvelope]:
        limit = max(1, min(int(limit), REPLAY_MAX_LIMIT))
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentEvent)
                        .where(AgentEvent.project_id == project_id, AgentEvent.seq > after_seq)
                        .order_by(AgentEvent.seq)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [_envelope_from_row(row) for row in rows]
