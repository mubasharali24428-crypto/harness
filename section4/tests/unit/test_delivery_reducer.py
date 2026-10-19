"""Part 46 (lane R): the client delivery rules, executed.

``delivery.Reducer`` is the reference form of the rules in docs/realtime.md (cursor, dedupe by
seq, a bounded out-of-order buffer keyed on ``prevSeq``, gap detection, backfill over HTTP). The
browser client is a port of it, so these tests are the specification the port must satisfy.
"""

import json
import random
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.delivery import Reducer, history_page

DDL = (
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " actor_user_id VARCHAR(36) NOT NULL, kind VARCHAR(40) NOT NULL, request_id VARCHAR(64),"
    " payload TEXT NOT NULL DEFAULT '{}', ts TIMESTAMP, schema_version INTEGER DEFAULT 1)"
)


def chain(*seqs):
    """Frames shaped like the socket's: each names the previous visible event."""
    return [{"seq": s, "prevSeq": p} for p, s in zip([None, *seqs], seqs, strict=False)]


def seqs(frames):
    return [f["seq"] for f in frames]


# ------------------------------------------------------------------ the rules


def test_an_ordered_stream_is_delivered_at_once_and_moves_the_cursor():
    r = Reducer()
    out = [r.push(f) for f in chain(4, 9, 10)]
    assert [seqs(o) for o in out] == [[4], [9], [10]] and r.cursor == 10 and not r.gap()


def test_a_repeated_seq_is_dropped_and_counted_even_after_a_reconnect_overlap():
    r = Reducer()
    frames = chain(4, 9, 10)
    for f in frames:
        r.push(f)
    # the socket reconnects with an old cursor, or a poll overlaps a push: all repeats
    assert [r.push(f) for f in frames + frames[1:]] == [[]] * 5
    assert r.duplicates == 5 and r.cursor == 10


def test_a_frame_that_arrives_before_its_predecessor_waits_and_is_released_in_order():
    r = Reducer()
    f4, f9, f10, f12 = chain(4, 9, 10, 12)
    assert seqs(r.push(f4)) == [4]
    assert r.push(f12) == [] and r.push(f10) == []  # both wait for 9
    assert r.pending() == 2 and not r.gap(now=0.5)
    assert seqs(r.push(f9)) == [9, 10, 12]  # one arrival releases the whole run, in order
    assert r.cursor == 12 and r.pending() == 0


def test_a_missing_event_becomes_a_gap_after_the_wait_or_when_the_buffer_is_full():
    slow = Reducer(window=100, max_wait=3.0)
    f1, _f2, f3 = chain(1, 2, 3)
    slow.push(f1, now=0)
    slow.push(f3, now=1)
    assert not slow.gap(now=3.9) and slow.gap(now=4.0)  # waited max_wait: ask the server
    full = Reducer(window=2, max_wait=60)
    full.push(chain(1, 2, 3, 4, 5)[4], now=0)  # 5 needs 4
    full.push({"seq": 6, "prevSeq": 5}, now=0)
    assert full.gap(now=0)  # the buffer hit its window


def test_nothing_visible_between_the_cursor_and_the_frame_means_it_is_not_held():
    r = Reducer(cursor=7)  # a stored cursor that is not itself a visible event
    assert seqs(r.push({"seq": 9, "prevSeq": 5})) == [9]
    assert seqs(Reducer().push({"seq": 3, "prevSeq": None})) == [3]  # first event of the run
    # HTTP pages from before the chain existed carry no prevSeq: ordered by construction
    legacy = Reducer()
    assert seqs(legacy.push({"seq": 5})) == [5] and legacy.push({"seq": 4}) == []


def test_a_held_frame_that_a_backfill_also_delivers_is_applied_once():
    r = Reducer()
    held = {"seq": 10, "prevSeq": 9}
    assert r.push(held, now=0) == []  # waits for 9
    got = []
    for frame in chain(8, 9, 10):  # the backfill pages in the whole stretch, ending with 10 again
        got += r.push(frame, now=1)
    assert seqs(got) == [8, 9, 10] and r.duplicates == 1 and r.pending() == 0


# --------------------------------------------------- against the real polling page


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/r.db")
    async with engine.begin() as conn:
        await conn.execute(text(DDL))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed(factory, n, other_every=3):
    truth = []
    async with factory() as db:
        for i in range(n):
            run = "r1" if i % other_every else "r2"  # r2 events open holes in the global seq
            res = await db.execute(
                text(
                    "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind, payload,"
                    " ts) VALUES (:r, 't1', 'u1', 'participant.joined', :p, :ts) RETURNING seq"
                ),
                {"r": run, "p": json.dumps({"n": i}), "ts": datetime.now(UTC)},
            )
            seq = res.scalar()
            if run == "r1":
                truth.append(seq)
        await db.commit()
    return truth


async def backfill(factory, reducer, now):
    """The client's answer to a gap: page the polling endpoint from its cursor to the head."""
    delivered = []
    while True:
        page = await history_page(factory, "t1", "r1", reducer.cursor, 7)
        for event in page["events"]:
            delivered += reducer.push(event, now)
        if not page["hasMore"]:
            return delivered


async def test_a_truncated_replay_is_repaired_by_paging_from_the_cursor(factory):
    truth = await seed(factory, 40)
    newest = (await history_page(factory, "t1", "r1", 0, 500))["events"][-5:]
    r = Reducer()
    got = []
    for frame in newest:  # what the socket sent: only the newest five, then the marker
        got += r.push(frame, now=0)
    assert got == [] and r.pending() == 5  # the first one points at an event never received
    got += await backfill(factory, r, now=0)  # replayTruncated: page from the cursor
    assert seqs(got) == truth and r.pending() == 0


@pytest.mark.parametrize("seed_value", range(30))
async def test_loss_duplication_and_reordering_always_converge_to_the_log(factory, seed_value):
    rng = random.Random(seed_value)
    truth = await seed(factory, 45)
    sent = (await history_page(factory, "t1", "r1", 0, 500))["events"]
    wire = []
    for frame in sent:
        if rng.random() < 0.15:
            continue  # lost
        wire.append(frame)
        if rng.random() < 0.2:
            wire.append(frame)  # delivered twice
    for i in range(len(wire) - 1):
        if rng.random() < 0.2:
            wire[i], wire[i + 1] = wire[i + 1], wire[i]  # swapped
    r = Reducer(window=8, max_wait=3.0)
    delivered, now = [], 0.0
    for frame in wire:
        now += 1.0
        delivered += r.push(frame, now)
        if r.gap(now):
            delivered += await backfill(factory, r, now)
    delivered += await backfill(factory, r, now)  # the periodic safety poll
    assert seqs(delivered) == truth  # complete, in order
    assert len(set(seqs(delivered))) == len(truth)  # and exactly once
    assert r.cursor == truth[-1] and r.pending() == 0
