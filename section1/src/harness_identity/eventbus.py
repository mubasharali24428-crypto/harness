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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
        if engine is None:
            return
        try:
            backend = engine.url.get_backend_name()
            dsn = engine.url.render_as_string(hide_password=False).replace(
                "postgresql+asyncpg://", "postgresql://", 1
            )
        except Exception:  # noqa: BLE001 — test doubles may not be real engines
            return
        if backend != "postgresql":
            return  # sqlite tests: direct fan-out covers same-instance delivery
        self._listener_task = asyncio.get_running_loop().create_task(self._listen(dsn))

    async def _listen(self, dsn: str) -> None:
        import asyncpg

        while True:
            try:
                conn = await asyncpg.connect(dsn)

                def _on_notify(_conn, _pid, _channel, raw: str, _self=self):
                    try:
                        data = json.loads(raw)
                        envelope = EventEnvelope(
                            type=data["type"],
                            payload=data.get("payload") or {},
                            ts=str(data.get("ts") or ""),
                            seq=int(data["seq"]),
                            projectId=data["projectId"],
                            runId=data.get("runId"),
                        )
                    except Exception:  # noqa: BLE001 — malformed notify, skip
                        return
                    _self._fanout_local(envelope)

                maybe_coro = self._attach_listener(conn, _on_notify)
                if maybe_coro is not None:
                    await maybe_coro
                    await asyncio.Event().wait()  # park forever; callbacks drive us
                else:
                    await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — DB restarts etc.; reconnect
                logger.exception("eventbus listener reconnecting")
                await asyncio.sleep(1)

    def _attach_listener(self, conn, callback):
        """asyncpg >=0.30 made add_listener a coroutine; older versions are sync."""
        result = conn.add_listener("agent_events", callback)
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
