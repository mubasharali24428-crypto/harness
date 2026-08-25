"""M18-C5 (MAC2-F9) — rate_buckets garbage-collection regression tests.

Finding F9 (m17-findings-mac2.md): nothing ever deleted from ``rate_buckets``
— every unique ``(ip|template)`` pair was a permanent PK row, so IPv6
rotation grew the store unboundedly and, when store pressure made ``_check``
error, limiting failed OPEN entirely (the attack disabled its own guard).

Fix under test:
  1. sweep_expired_buckets() deletes ONLY buckets whose fixed window is fully
     elapsed (they are inert: any future hit resets them via the upsert CASE).
  2. RateLimitMiddleware._check runs the sweep opportunistically, throttled
     to ONE sweep per window per middleware instance.
  3. A failing sweep must never break the limited request (fail-open law).

Self-contained fixtures (conftest.py is frozen this wave); rate_buckets DDL
is inlined exactly like tests/test_ratelimit.py (the table is not in ORM
Base.metadata).
"""

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import harness_identity.ratelimit as rl
from harness_identity.ratelimit import (
    DEFAULT_WINDOW_SECONDS,
    RateLimitMiddleware,
    current_window,
    sweep_expired_buckets,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)

# Mirrors migrations/0011_rate_buckets.sql (SQLite accepts the same DDL).
RATE_BUCKETS_DDL = """
CREATE TABLE rate_buckets (
    bucket_key VARCHAR(200) PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
)
"""


@pytest.fixture
async def gc_engine():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(text(RATE_BUCKETS_DDL))
    yield engine
    await engine.dispose()


@pytest.fixture
def gc_factory(gc_engine):
    return async_sessionmaker(gc_engine, expire_on_commit=False)


async def _insert_bucket(factory, key: str, window_epoch: float, count: int = 3):
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO rate_buckets (bucket_key, window_start, count)"
                " VALUES (:k, :w, :c)"
            ),
            {
                "k": key,
                "w": datetime.fromtimestamp(window_epoch, tz=UTC),
                "c": count,
            },
        )
        await db.commit()


async def _bucket_rows(factory) -> dict[str, int]:
    """Surviving buckets as {key: count} (raw SQL; table is not in ORM)."""
    async with factory() as db:
        rows = (
            await db.execute(text("SELECT bucket_key, count FROM rate_buckets"))
        ).all()
    return {r[0]: int(r[1]) for r in rows}


# ---------------------------------------------------------------
# 1. the pure sweep
# ---------------------------------------------------------------


async def test_sweep_deletes_only_elapsed_windows(gc_factory):
    """Buckets from fully-past windows die; the CURRENT window survives —
    deleting an elapsed bucket can never change a limit decision because the
    upsert resets stale counters to 1 before consulting them."""
    now = 1_000_000.0
    cur_epoch = current_window(DEFAULT_WINDOW_SECONDS, now)[1]
    stale_epoch = cur_epoch - DEFAULT_WINDOW_SECONDS * rl.BUCKET_GC_EVERY_WINDOWS

    await _insert_bucket(gc_factory, "8.8.8.8|/old/route", stale_epoch)
    await _insert_bucket(gc_factory, "9.9.9.9|/api/v1/auth/login", cur_epoch)

    removed = await sweep_expired_buckets(
        gc_factory, window_seconds=DEFAULT_WINDOW_SECONDS, now_epoch=now
    )
    assert removed == 1
    assert await _bucket_rows(gc_factory) == {"9.9.9.9|/api/v1/auth/login": 3}


async def test_second_sweep_is_a_noop(gc_factory):
    """Idempotent: nothing left in elapsed windows -> zero rows removed."""
    now = 2_000_000.0
    cur_epoch = current_window(60, now)[1]
    await _insert_bucket(gc_factory, "a|b", cur_epoch)
    assert await sweep_expired_buckets(gc_factory, window_seconds=60, now_epoch=now) == 0


async def test_older_than_one_cadence_window_also_swept(gc_factory):
    """Rows many windows old (the unbounded-growth shape F9 described) go too."""
    now = 3_000_000.0
    cur_epoch = current_window(60, now)[1]
    await _insert_bucket(gc_factory, "ancient", cur_epoch - 60 * 50)
    await _insert_bucket(gc_factory, "fresh", cur_epoch)
    assert await sweep_expired_buckets(gc_factory, window_seconds=60, now_epoch=now) == 1
    assert await _bucket_rows(gc_factory) == {"fresh": 3}


# ---------------------------------------------------------------
# 2. opportunistic wiring inside _check
# ---------------------------------------------------------------


def _middleware(factory) -> RateLimitMiddleware:
    async def inner(scope, receive, send):  # pragma: no cover - never reached here
        raise AssertionError("inner app must not be called for rejected requests")

    return RateLimitMiddleware(
        inner, factory, routes=("/t/route",), window_seconds=DEFAULT_WINDOW_SECONDS
    )


async def test_first_limited_request_sweeps_stale_buckets(gc_factory):
    """The F9 loop, closed end-to-end: a stale bucket seeded directly into the
    store is gone after the next limited request goes through _check."""
    mw = _middleware(gc_factory)
    idx, cur_epoch = current_window(mw.window_seconds)
    await _insert_bucket(gc_factory, "stale-ip|/t/route", cur_epoch - mw.window_seconds)

    allowed, _retry = await mw._check("/t/route", ("10.1.1.1", 5))
    assert allowed  # fresh client inside budget

    # Stale row swept opportunistically; this request's own bucket exists for
    # the CURRENT window only, freshly reset at count=1.
    assert mw._gc_window_index == idx
    assert await _bucket_rows(gc_factory) == {"10.1.1.1|/t/route": 1}


async def test_gc_throttled_to_once_per_window(monkeypatch, gc_factory):
    """One sweep per window index per middleware instance — the per-request
    piggyback must not add a DELETE to every limited call."""
    mw = _middleware(gc_factory)
    calls = []

    async def _spy(factory, **kw):
        calls.append(kw.get("window_seconds"))
        return 0

    monkeypatch.setattr(rl, "sweep_expired_buckets", _spy)

    await mw._check("/t/route", ("10.2.0.1", 5))
    await mw._check("/t/route", ("10.2.0.2", 5))
    await mw._check("/t/route", ("10.2.0.3", 5))
    assert len(calls) == 1

    # Next window => exactly one more sweep.
    gc_index = mw._gc_window_index
    assert gc_index is not None
    await mw._maybe_gc(int(gc_index) + 1)
    assert len(calls) == 2


async def test_gc_failure_never_breaks_the_request(monkeypatch, gc_factory, caplog):
    """Sweep blowing up is swallowed with a warning; the request's own limit
    decision proceeds untouched (availability beats strictness — module law)."""
    mw = _middleware(gc_factory)

    async def _boom(factory, **kw):
        raise RuntimeError("store on fire")

    monkeypatch.setattr(rl, "sweep_expired_buckets", _boom)
    with caplog.at_level(logging.WARNING, logger="harness_identity.ratelimit"):
        allowed, _retry = await mw._check("/t/route", ("10.3.0.1", 5))
    assert allowed
    assert any("GC" in r.message for r in caplog.records)
    # ...and the request's own counter still landed in the store.
    assert await _bucket_rows(gc_factory) == {"10.3.0.1|/t/route": 1}


# ---------------------------------------------------------------
# 3. full ASGI pass-through (no behavior change for callers)
# ---------------------------------------------------------------


async def test_asgi_request_passes_through_with_gc_armed(gc_factory, monkeypatch):
    """__call__ path: a configured route still reaches the wrapped app while
    the opportunistic sweep runs; an UNCONFIGURED route triggers no sweep."""
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = RateLimitMiddleware(
        inner, gc_factory, routes=("/limited/path",), window_seconds=60
    )
    sweeps = []

    async def _spy(factory, **kw):
        sweeps.append(1)
        return 0

    monkeypatch.setattr(rl, "sweep_expired_buckets", _spy)

    async def _call(path: str):
        messages = []

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "path": path,
            "headers": [],
            "client": ("10.9.9.9", 1234),
        }
        await mw(scope, receive, send)
        return messages

    msgs = await _call("/limited/path")
    assert msgs[0]["status"] == 200
    assert reached == ["/limited/path"]
    assert len(sweeps) == 1

    await _call("/unrelated/path")
    assert len(sweeps) == 1  # unconfigured route: no sweep within the same window
