"""Mission 10 node B5 tests: Postgres fixed-window login/OTP rate limiting.

Node B5 owns ONLY this file (conftest.py is frozen) — every fixture lives
here. Coverage, mapped to the brief:

1. burst within limit passes            -> test_burst_within_limit_passes
2. limit+1 -> 429 + Retry-After         -> test_limit_plus_one_gets_429_with_retry_after
3. new window resets the counter        -> test_new_window_resets
4. different client IPs independent     -> test_different_ips_independent
5. DB down fails OPEN with a warning    -> test_db_down_fails_open
6. only configured routes are limited   -> test_only_configured_routes_limited
7. buckets keyed per IP+ROUTE template  -> test_routes_bucketed_separately

Runs against the real identity app (make_app) over aiosqlite; the fixed-window
upsert SQL in ratelimit.py is portable, so the suite exercises the exact
statement Postgres runs (see migration 0011 for the production table).
"""

import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import harness_identity.ratelimit as rl
from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import Base
from harness_identity.ratelimit import install_rate_limit

MASTER = b"test-master-key-0123456789abcdef"
LIMITED_LOGIN = "/api/v1/auth/login"

# Mirrors migrations/0011_rate_buckets.sql (SQLite accepts the same DDL).
RATE_BUCKETS_DDL = """
CREATE TABLE rate_buckets (
    bucket_key VARCHAR(200) PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
)
"""


class _Clock:
    """Replaceable time source: lets tests roll windows forward instantly."""

    def __init__(self, start: float) -> None:
        self.now_value = start

    def time(self) -> float:
        return self.now_value


@pytest.fixture
async def rl_engine():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(RATE_BUCKETS_DDL))
    yield engine
    await engine.dispose()


pytestmark = pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)


def _limited_app(session_factory, **kw):
    """Real identity app + rate limiting installed at factory time."""
    app = make_app(
        session_factory, Settings(master_key=MASTER, cors_origins=[])
    )
    install_rate_limit(app, **kw)
    return app


async def _client_for(app, ip: str):
    transport = ASGITransport(app=app, client=(ip, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


def _factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _login(ip: str, app, email: str | None = None):
    email = email or f"{ip.replace('.', '-')}@example.com"
    async with await _client_for(app, ip) as c:
        return await c.post(LIMITED_LOGIN, json={"email": email})


async def test_default_routes_are_the_auth_edge():
    """Pin the contract: the shipped defaults guard exactly these two routes."""
    assert rl.DEFAULT_ROUTES == ("/api/v1/auth/login", "/api/v1/auth/otp/request")


async def test_burst_within_limit_passes(rl_engine):
    app = _limited_app(_factory(rl_engine), limit=3, window_seconds=60)
    statuses = [await _login("10.1.1.1", app) for _ in range(3)]
    statuses = [r.status_code for r in statuses]
    assert statuses == [200, 200, 200]


async def test_limit_plus_one_gets_429_with_retry_after(rl_engine):
    app = _limited_app(_factory(rl_engine), limit=3, window_seconds=60)
    for i in range(3):
        resp = await _login("10.2.2.2", app, email=f"u{i}@example.com")
        assert resp.status_code == 200
    blocked = await _login("10.2.2.2", app, email="overflow@example.com")
    assert blocked.status_code == 429
    # Contract error_body() envelope + machine-readable Retry-After.
    assert blocked.json() == {
        "error": {"code": "RATE_LIMITED", "message": "too many attempts; slow down"}
    }
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60


async def test_new_window_resets(rl_engine, monkeypatch):
    clock = _Clock(1_000_000.0)
    monkeypatch.setattr(rl, "time", clock)
    app = _limited_app(_factory(rl_engine), limit=2, window_seconds=60)

    assert (await _login("10.3.3.3", app)).status_code == 200
    assert (await _login("10.3.3.3", app)).status_code == 200
    assert (await _login("10.3.3.3", app)).status_code == 429

    clock.now_value += 61  # cross the fixed-window boundary
    assert (await _login("10.3.3.3", app)).status_code == 200

    async with _factory(rl_engine)() as db:
        rows = (
            await db.execute(
                text("SELECT window_start, count FROM rate_buckets")
            )
        ).all()
    assert len(rows) == 1  # same bucket reused, counter restarted…
    assert rows[0][1] == 1  # …at 1, proving the CASE branch reset it.


async def test_different_ips_independent(rl_engine):
    app = _limited_app(_factory(rl_engine), limit=2, window_seconds=60)
    for i in range(2):
        assert (await _login("10.4.4.4", app, email=f"a{i}@example.com")).status_code == 200
    assert (await _login("10.4.4.4", app)).status_code == 429
    # A fresh IP starts from a clean bucket even mid-window of the first IP.
    assert (await _login("10.4.4.5", app)).status_code == 200


class _ExplodingStore:
    """Session factory standing in for an unreachable rate-limit store."""

    def __call__(self):
        raise RuntimeError("simulated store outage")


async def test_db_down_fails_open(rl_engine, caplog, monkeypatch):
    # Sanity twin on a healthy store proves the block comes from the limiter,
    # not the app: with a working counter the 4th attempt is 429...
    healthy = _limited_app(_factory(rl_engine), limit=3, window_seconds=60)
    for i in range(3):
        assert (await _login("10.5.5.5", healthy, email=f"h{i}@example.com")).status_code == 200
    assert (await _login("10.5.5.5", healthy)).status_code == 429

    # ...but when the COUNTER STORE is down, availability wins and requests
    # pass through to the app (whose own healthy DB answers 200).
    ratelimit_logger = logging.getLogger("harness_identity.ratelimit")
    monkeypatch.setattr(ratelimit_logger, "propagate", True)
    with caplog.at_level(logging.WARNING, logger="harness_identity.ratelimit"):
        broken = make_app(_factory(rl_engine), Settings(master_key=MASTER, cors_origins=[]))
        install_rate_limit(
            broken, limit=1, window_seconds=60, session_factory=_ExplodingStore()
        )
        statuses = []
        for i in range(5):  # well past limit=1 — every one must fail OPEN
            statuses.append(
                (await _login("10.6.6.6", broken, email=f"f{i}@example.com")).status_code
            )
    assert statuses == [200] * 5
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("failing OPEN" in r.getMessage() for r in warnings)


async def test_store_exception_fails_open_directly():
    """Unit probe: ANY exception from the counter store -> allowed + warning,
    independent of the wrapped app."""
    seen: list[tuple[bool, int]] = []

    class _Boom:
        def __call__(self):
            raise OSError("connection refused")

    mw = rl.RateLimitMiddleware(
        lambda scope, receive, send: None, _Boom(), limit=1, window_seconds=60
    )
    seen.append(await mw._check("/api/v1/auth/login", ("203.0.113.9", 4444)))
    assert seen[-1][0] is True  # fail OPEN
    assert seen[-1][1] == 0  # no Retry-After needed when we never blocked


async def test_only_configured_routes_limited(rl_engine):
    app = _limited_app(
        _factory(rl_engine),
        limit=2,
        window_seconds=60,
        routes=(LIMITED_LOGIN,),
    )
    # Hammer a NON-configured route far past the limit: never throttled.
    async with await _client_for(app, "10.7.7.7") as c:
        for _ in range(6):
            resp = await c.get("/api/v1/me")
            assert resp.status_code != 429
    # And the configured route still enforces its own budget afterwards.
    for i in range(2):
        assert (await _login("10.7.7.7", app, email=f"o{i}@example.com")).status_code == 200
    assert (await _login("10.7.7.7", app)).status_code == 429


async def test_routes_bucketed_separately(rl_engine):
    """Keying is IP + route TEMPLATE: exhausting one route never bleeds into
    another configured route for the same client."""
    otp_request = "/api/v1/auth/otp/request"  # arbitrary second template here
    app = _limited_app(
        _factory(rl_engine),
        limit=1,
        window_seconds=60,
        routes=(LIMITED_LOGIN, otp_request),
    )
    assert (await _login("10.8.8.8", app)).status_code == 200
    assert (await _login("10.8.8.8", app)).status_code == 429  # login bucket spent

    async with await _client_for(app, "10.8.8.8") as c:
        # The otp/request route has its own untouched bucket (404 from routing
        # is FINE — the point is the limiter did not answer 429).
        resp = await c.post(otp_request, json={"email": "x@example.com"})
    assert resp.status_code != 429
