"""M16-X3 (raw-16 #7) — WS replay cap + seq-window resume proof.

What is proven here:

1. CAP ENFORCEMENT — replay_page sends at most N events per batch
   (env REPLAY_MAX_EVENTS, default 5000), newest-N when the gap exceeds it.
2. TRUNCATION MARKER — when the gap exceeds the cap the WS replay carries an
   honest trailing frame {"replayTruncated": true, "oldestSeqSent": S} so the
   client KNOWS it missed history older than S (and the REST endpoint exposes
   the same pair additively).
3. SEQ-WINDOW RESUME UNDER CONCURRENT PUBLISH — while rows are being inserted,
   repeated replay_page(after_seq=cursor) calls never return a seq <= cursor;
   the concatenated pages equal EXACTLY the DB ground truth (no dup, no gap).
4. MEMORY BOUND — replay streams via yield_per in fixed chunks; only the
   retained newest-N deque is held in RAM (structural guarantee; see
   replay_page docstring — this module pins the observable behavior: batch size
   stays capped regardless of gap size).
5. EMPTY-GAP FAST PATH — afterSeq beyond the log returns zero envelopes, no
   marker, and the socket proceeds straight to live delivery.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from harness_contracts import EventType
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.auth import hash_token
from harness_identity.config import Settings
from harness_identity.eventbus import ReplayPage, _replay_max_events
from harness_identity.models import AgentEvent, AuthSession, Base, Project, Team, TeamMember, User


class WsRejected(AssertionError):
    def __init__(self, code):
        super().__init__(f"websocket rejected with close code {code}")
        self.code = code


class MiniWs:
    """Single-loop raw ASGI websocket driver (same pattern as test_ws_auth.py:
    starlette's threaded portal wedges aiosqlite engines bound to the fixture
    loop; this runs app + DB on ONE loop, deterministically)."""

    def __init__(self, app, target: str):
        parts = urlsplit(target)
        self.scope = {
            "type": "websocket",
            "path": parts.path,
            "query_string": parts.query.encode(),
            "headers": [],
            "subprotocols": [],
            "server": ("testserver", 80),
            "client": ("testclient", 123),
        }
        self.app = app
        self._in: asyncio.Queue = asyncio.Queue()
        self._out: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.accepted = False
        self.close_code: int | None = None

    async def __aenter__(self) -> "MiniWs":
        self._task = asyncio.ensure_future(self.app(self.scope, self._in.get, self._out.put))
        await self._in.put({"type": "websocket.connect"})
        first = await asyncio.wait_for(self._out.get(), timeout=5)
        if first["type"] == "websocket.accept":
            self.accepted = True
        elif first["type"] == "websocket.close":
            self.close_code = first.get("code", 1000)
        return self

    async def recv_json(self, timeout: float = 5):
        while True:
            msg = await asyncio.wait_for(self._out.get(), timeout=timeout)
            if msg["type"] == "websocket.send":
                return json.loads(msg["text"])
            if msg["type"] == "websocket.close":
                self.close_code = msg.get("code", 1000)
                raise WsRejected(self.close_code)

    async def __aexit__(self, *exc) -> None:
        if self.accepted and self.close_code is None:
            await self._in.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def _marker(frame: dict) -> bool:
    return isinstance(frame, dict) and frame.get("replayTruncated") is True


async def _seed(db):
    """users/teams/projects/team_members/auth_sessions for token 'goodtok'."""
    async with db() as s:
        s.add(User(id="u1", email="x@y.z", name="X"))
        s.add(Team(id="t1", name="T"))
        s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="X", role="admin"))
        s.add(Project(id="p1", team_id="t1", name="demo"))
        s.add(Project(id="p-empty", team_id="t1", name="empty"))
        s.add(
            AuthSession(
                token_hash=hash_token("goodtok"),
                user_id="u1",
                team_id="t1",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def app(db):
    await _seed(db)
    return make_app(db, Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[]))


DB_DIR = Path("/tmp/opencode")


@pytest.fixture
async def fdb():
    """File-backed SQLite: REAL per-session connections.

    The in-memory fixture shares one connection across every session, so an
    interleaved session's rollback discards another session's committed rows —
    a fixture artifact that would fake "lost events" under any concurrency
    (verified by matrix probe: even a bare SELECT-count loop 'loses' rows
    there, while publisher-alone loses none). Production runs Postgres with a
    connection pool; the file DB reproduces those semantics for the isolation
    proof below.
    """
    DB_DIR.mkdir(parents=True, exist_ok=True)
    db_file = DB_DIR / "ws_replay_cap_test.db"
    if db_file.exists():
        db_file.unlink()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 15}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()
    if db_file.exists():
        db_file.unlink()


@pytest.fixture
async def fapp(fdb):
    await _seed(fdb)
    return make_app(fdb, Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[]))


async def _publish_n(bus, project_id: str, n: int, tag: str = "") -> list[int]:
    seqs = []
    for i in range(n):
        env = await bus.publish(EventType.TERMINAL_OUTPUT, project_id, {"i": i, "tag": tag})
        seqs.append(env.seq)
    return seqs


def _is_envelope(frame: dict) -> bool:
    return isinstance(frame, dict) and "seq" in frame and "type" in frame


def _auth() -> dict:
    return {"Authorization": "Bearer goodtok"}


async def _rest_get(app, query: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(f"/api/v1/projects/p1/events/replay{query}", headers=_auth())


# ----------------------------------------------------------------- cap rules


async def test_replay_page_caps_batch_to_newest_n(app, monkeypatch):
    bus = app.state.event_bus
    seqs = await _publish_n(bus, "p1", 120)
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "50")

    page = await bus.replay_page("p1", 0)

    assert isinstance(page, ReplayPage)
    assert len(page.envelopes) == 50  # hard cap, not the whole 120-row gap
    got = [e.seq for e in page.envelopes]
    assert got == sorted(got), "batch must stay ascending by seq"
    assert got == seqs[-50:], "capped replay must carry the NEWEST N events"
    assert page.truncated is True
    assert page.oldest_seq_sent == seqs[-50]
    assert page.has_more is True


async def test_replay_page_under_cap_is_not_truncated(app):
    bus = app.state.event_bus
    seqs = await _publish_n(bus, "p1", 7)

    page = await bus.replay_page("p1", 0)

    assert [e.seq for e in page.envelopes] == seqs
    assert page.truncated is False
    assert page.oldest_seq_sent == seqs[0]
    assert page.has_more is False


async def test_limit_clamps_below_cap(app, monkeypatch):
    """limit < cap: batch is limit-sized; has_more pagination, NOT truncation."""
    bus = app.state.event_bus
    await _publish_n(bus, "p1", 30)

    page = await bus.replay_page("p1", 0, limit=10)

    assert len(page.envelopes) == 10
    assert page.truncated is False  # cap untouched — pure pagination
    assert page.has_more is True


async def test_env_parsing_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "bogus")
    assert _replay_max_events() == 5000
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "0")
    assert _replay_max_events() == 5000  # must never disable the cap
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "-3")
    assert _replay_max_events() == 5000
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "12")
    assert _replay_max_events() == 12


# ------------------------------------------------------------ WS marker frame


async def test_ws_sends_truncation_marker_when_gap_exceeds_cap(app, monkeypatch):
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "20")
    bus = app.state.event_bus
    seqs = await _publish_n(bus, "p1", 35)  # gap 35 > cap 20

    async with MiniWs(app, "/api/v1/ws/p1?token=goodtok&afterSeq=0") as ws:
        assert ws.accepted
        frames = [await ws.recv_json() for _ in range(21)]  # newest-20 envelopes + marker

    envelopes = [f for f in frames if _is_envelope(f)]
    markers = [f for f in frames if _marker(f)]
    assert len(envelopes) == 20, "capped replay sends the NEWEST N, not the whole gap"
    assert [e["seq"] for e in envelopes] == list(range(seqs[-20], seqs[-1] + 1))
    assert len(markers) == 1, "exactly one honest truncation marker must follow the batch"
    marker = markers[0]
    assert marker["oldestSeqSent"] == seqs[-20]
    assert marker["afterSeq"] == 0
    # Marker comes AFTER the envelopes, never before/between:
    assert frames.index(marker) == 20


async def test_ws_no_marker_when_gap_within_cap(app):
    bus = app.state.event_bus
    seqs = await _publish_n(bus, "p1", 5)

    async with MiniWs(app, "/api/v1/ws/p1?token=goodtok&afterSeq=0") as ws:
        # Wait until the handler actually subscribed, then push a live event so
        # we can deterministically read one frame PAST the replay window.
        for _ in range(200):
            if app.state.event_bus._subscribers.get("p1"):
                break
            await asyncio.sleep(0.01)
        live = await bus.publish(EventType.PLAN_APPROVED, "p1", {"live": True})

        frames = [await ws.recv_json() for _ in range(6)]

    replayed = frames[:5]
    assert [_is_envelope(f) for f in replayed] == [True] * 5
    assert [f["seq"] for f in replayed] == seqs
    assert not any(_marker(f) for f in frames), "no marker may appear under the cap"
    assert frames[5]["seq"] == live.seq and frames[5]["type"] == "plan_approved"


# --------------------------------------------------- REST endpoint surface


async def test_rest_replay_shape_unchanged_without_truncation(app):
    bus = app.state.event_bus
    await _publish_n(bus, "p1", 6)
    r = await _rest_get(app, "?afterSeq=0&limit=4")
    assert r.status_code == 200
    body = r.json()
    assert body["nextCursor"] == body["items"][-1]["seq"]
    assert len(body["items"]) == 4
    assert not body.get("replayTruncated"), "marker must be falsy/absent when cap did not bite"


async def test_rest_replay_reports_truncation_pair(app, monkeypatch):
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "15")
    bus = app.state.event_bus
    seqs = await _publish_n(bus, "p1", 40)

    r = await _rest_get(app, "?afterSeq=0&limit=200")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 15
    assert [i["seq"] for i in body["items"]] == list(range(seqs[-15], seqs[-1] + 1))
    assert body["replayTruncated"] is True
    assert body["oldestSeqSent"] == seqs[-15]


async def test_rest_before_seq_backfills_truncated_gap_oldest_first(app, monkeypatch):
    """W22: paging (after_seq, before_seq) by nextCursor recovers the whole skipped range."""
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "15")
    bus = app.state.event_bus
    seqs = await _publish_n(bus, "p1", 40)
    stop = seqs[-15]  # the oldestSeqSent a truncated resume reports

    got, cursor = [], 0
    for _ in range(10):
        body = (await _rest_get(app, f"?after_seq={cursor}&before_seq={stop}&limit=10")).json()
        if not body["items"]:
            break
        got += [i["seq"] for i in body["items"]]
        cursor = body["nextCursor"]
    assert got == seqs[:-15]
    assert not body.get("replayTruncated")


# --------------------------------------- seq-window resume under concurrency


async def test_a_capped_resume_page_names_a_hole_a_client_can_fill(fapp, monkeypatch):
    """The deterministic twin of the concurrent test below.

    Publish everything up front so the cap is forced to bite: the resume page
    carries only the newest N, `oldestSeqSent` says exactly where it cut, and
    the skipped head is recoverable oldest-first via before_seq. A plain
    afterSeq walk is NOT a recovery path — it returns the same newest-N window,
    which is why the marker exists at all.
    """
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "17")
    app = fapp
    bus = app.state.event_bus
    seqs = await _publish_n(bus, "p1", 50)

    page = await bus.replay_page("p1", 0)
    delivered = [e.seq for e in page.envelopes]
    assert page.truncated is True
    assert delivered == seqs[-17:], "newest-N under the cap"
    stop = page.oldest_seq_sent
    assert stop == seqs[-17], "the marker names the first row that WAS sent"

    naive = await _rest_get(app, "?afterSeq=0&limit=500")
    assert [item["seq"] for item in naive.json()["items"]] == seqs[-17:], (
        "a plain afterSeq walk never reaches the hole"
    )

    recovered, cursor = [], 0
    while True:
        body = (
            await _rest_get(app, f"?after_seq={cursor}&before_seq={stop}&limit=10")
        ).json()
        items = body["items"]
        if not items:
            break
        recovered += [item["seq"] for item in items]
        cursor = items[-1]["seq"]
    assert recovered == seqs[:-17], "the skipped head, recovered oldest-first"
    assert sorted(delivered + recovered) == seqs


async def test_resume_never_resends_at_or_below_afterseq_during_concurrent_publish(
    fapp, monkeypatch
):
    """THE isolation proof (file-backed DB — real per-session connections):
    while another task keeps committing new rows, every replay_page(after_seq)
    returns ONLY rows with seq > cursor. Concatenated pages plus the REST
    backfill walk equal the DB ground truth exactly — no dup, no gap, nothing
    at-or-below any resume watermark ever re-sent."""
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "17")  # force multi-page streaming
    app = fapp
    bus = app.state.event_bus
    pre = await _publish_n(bus, "p1", 10)
    after_seq = 0  # reconnecting client that has seen NOTHING yet

    published_count = 40
    done = asyncio.Event()

    async def concurrent_publisher():
        for i in range(published_count):
            await bus.publish(EventType.DIFF_PROGRESS, "p1", {"n": i})
            await asyncio.sleep(0)  # hand control back between commits
        done.set()

    publisher = asyncio.ensure_future(concurrent_publisher())

    batches: list[list[int]] = []
    holes: list[tuple[int, int]] = []  # (cursor, oldestSeqSent) the cap skipped
    while True:
        page = await bus.replay_page("p1", after_seq)
        seqs = [e.seq for e in page.envelopes]
        assert all(s > after_seq for s in seqs), (
            f"re-send violation: {[s for s in seqs if s <= after_seq]} <= cursor {after_seq}"
        )
        if (
            page.truncated
            and page.oldest_seq_sent is not None
            and page.oldest_seq_sent > after_seq + 1
        ):
            # Newest-N: this page had to skip (cursor, oldestSeqSent). The client
            # fills that hole with before_seq; it never re-reads at or below its
            # cursor, which is the only thing that would duplicate.
            holes.append((after_seq, page.oldest_seq_sent))
        if seqs:
            batches.append(seqs)
            after_seq = seqs[-1]
        elif done.is_set():
            break  # publisher finished AND no new rows past cursor: fully drained
        # else: publisher still running — yield and re-poll (no busy skip of a
        # commit landing between done.set() and this page read).
        await asyncio.sleep(0)
    await publisher

    delivered_ws = [s for batch in batches for s in batch]
    assert delivered_ws == sorted(delivered_ws), "cross-batch order violated"
    assert len(delivered_ws) == len(set(delivered_ws)), "duplicate delivery across resumes"
    assert all(s > 0 for s in delivered_ws)

    # Ground truth from the DB itself. Read AFTER the publisher task is fully
    # awaited: its last commits may still be in-flight when the loop above
    # sees an empty page, and the CI runner's slower interleaving exposed
    # exactly that window (truth read before the final row committed).
    async with bus._sessions() as session:  # noqa: SLF001 — test introspection
        truth = [
            int(s)
            for s in (
                (
                    await session.execute(
                        select(AgentEvent.seq)
                        .where(AgentEvent.project_id == "p1")
                        .order_by(AgentEvent.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
        ]
    # Newest-N semantics: when a mid-stream gap outgrew the cap, the WS page
    # skipped the OLDEST rows. A compliant client backfills that hole through
    # the before_seq cursor — and NOT with a plain afterSeq walk, which is
    # newest-N too and would silently skip the very rows being accounted for.
    # (This test used to use that plain walk: on a loaded runner the gap
    # outgrew the cap, the walk could not see the skipped head, and a range
    # that was never lost was reported as lost.)
    backfilled = list(delivered_ws)
    for start, stop in holes:
        cursor = start
        while True:
            body = (
                await _rest_get(app, f"?after_seq={cursor}&before_seq={stop}&limit=10")
            ).json()
            items = body["items"]
            if not items:
                break
            assert all(start < item["seq"] < stop for item in items), (
                "the hole walk must stay strictly inside the hole"
            )
            backfilled += [item["seq"] for item in items]
            cursor = items[-1]["seq"]

    assert sorted(backfilled) == truth
    assert len(backfilled) == len(set(backfilled)), "no dup across WS + hole recovery"
    assert len(truth) == 10 + published_count
    assert pre[0] in backfilled and truth[-1] in backfilled


async def test_mid_stream_resume_from_high_watermark(fapp, monkeypatch):
    """Client that already saw half the log reconnects mid-publish with a gap
    LARGER than the cap: everything re-delivered must be strictly above ITS
    watermark AND within the newest-N window — never rows at/below its own
    watermark, never fabricated rows. The skipped middle is exactly what the
    truncation marker's oldestSeqSent advertises."""
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "8")
    app = fapp
    bus = app.state.event_bus
    first = await _publish_n(bus, "p1", 20)
    watermark = first[9]  # client processed through the 10th event

    page = await bus.replay_page("p1", watermark)

    seqs = [e.seq for e in page.envelopes]
    assert all(s > watermark for s in seqs), "never re-send at-or-below afterSeq"
    assert seqs == first[-8:], "newest-N window under the cap"
    assert page.truncated is True
    assert page.oldest_seq_sent == first[12], "marker names exactly where history was cut"
    # The skipped rows are strictly between the watermark and oldestSeqSent,
    # and are reachable via REST pagination (no permanent loss):
    async with bus._sessions() as session:  # noqa: SLF001 — test introspection
        missing = [
            int(s)
            for s in (
                (
                    await session.execute(
                        select(AgentEvent.seq).where(
                            AgentEvent.project_id == "p1",
                            AgentEvent.seq > watermark,
                            AgentEvent.seq < page.oldest_seq_sent,
                        )
                    )
                )
                .scalars()
                .all()
            )
        ]
    assert missing == first[10:12]


# -------------------------------------------------------- memory bound probes


async def test_batch_size_stays_capped_regardless_of_gap_size(app, monkeypatch):
    """Structural memory-bound pin: even a gap far larger than the cap yields a
    batch of exactly cap envelopes (streaming + bounded deque, never O(gap))."""
    monkeypatch.setenv("REPLAY_MAX_EVENTS", "25")
    bus = app.state.event_bus
    await _publish_n(bus, "p1", 300)

    page = await bus.replay_page("p1", 0)
    assert len(page.envelopes) == 25
    assert page.truncated is True
    # ...and resuming from the watermark keeps every subsequent batch bounded:
    resumed = await bus.replay_page("p1", page.oldest_seq_sent)
    assert len(resumed.envelopes) <= 25
    # H-2 (M17): real window assertion (was `... or True`, a tautology).
    # Resuming from the truncation watermark must return only events strictly
    # ABOVE that watermark (afterSeq filtering is exclusive).
    assert all(e.seq > page.oldest_seq_sent for e in resumed.envelopes)
    # Walking forward with the true cursor (each batch's last-seen seq) must
    # tile the served window exactly: a contiguous suffix of the log — no
    # duplicates, no fabricated holes — with every batch still bounded.
    seen: list[int] = []
    cursor = 0
    while True:
        batch = await bus.replay_page("p1", cursor)
        assert len(batch.envelopes) <= 25
        if not batch.envelopes:
            break
        seen.extend(e.seq for e in batch.envelopes)
        cursor = batch.envelopes[-1].seq
    assert seen == sorted(set(seen)), "duplicate delivery along the resume walk"
    assert seen == list(range(min(seen), max(seen) + 1)), "hole in served window"
    assert min(seen) == page.oldest_seq_sent


# --------------------------------------------------------- empty-gap fast path


async def test_empty_gap_fast_path_bus_level(app):
    bus = app.state.event_bus
    page = await bus.replay_page("p-empty", 0)
    assert page.envelopes == []
    assert page.truncated is False
    assert page.oldest_seq_sent is None
    assert page.has_more is False


async def test_empty_gap_fast_path_ws_goes_straight_live(app):
    bus = app.state.event_bus
    seqs = await _publish_n(bus, "p1", 3)

    async with MiniWs(app, f"/api/v1/ws/p1?token=goodtok&afterSeq={seqs[-1]}") as ws:
        for _ in range(200):
            if bus._subscribers.get("p1"):  # noqa: SLF001 — test introspection
                break
            await asyncio.sleep(0.01)
        live = await bus.publish(EventType.REVIEW_PASSED, "p1", {})

        frame = await ws.recv_json()

    assert frame["seq"] == live.seq, "empty gap must produce zero replay frames"
    assert not _marker(frame)


async def test_after_seq_filtering_is_strict(app):
    """afterSeq=N must exclude seq==N itself (strictly-greater window)."""
    bus = app.state.event_bus
    seqs = await _publish_n(bus, "p1", 4)
    page = await bus.replay_page("p1", seqs[1])
    assert [e.seq for e in page.envelopes] == seqs[2:]
