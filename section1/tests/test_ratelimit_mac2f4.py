"""M18-C7 (MAC2-F4) regression: invite REDEMPTION and WS-ticket MINTING were
the last two auth-adjacent edges missing from the shipped rate-limit set.

Contract under test:
- both literal routes are in DEFAULT_PROTECTED_ROUTES and flow through the
  same fixed-window middleware as the OTP edge (ASGI-level, so even
  unauthenticated bursts are counted);
- redeem rides the DEFAULT_LIMIT credential-edge budget (auth-required 401s
  until the burst trips, then the frozen 429 envelope + Retry-After);
- the ticket mint rides its wider 60/min override keyed by the literal
  route, per-IP independent, and an unrelated route is never limited.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import harness_identity.ratelimit as rl
from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import Base
from harness_identity.ratelimit import (
    REDEEM_ROUTE,
    WS_TICKET_LIMIT,
    WS_TICKET_ROUTE,
    install_rate_limit,
)


class _Clock:
    """Replaceable time source: lets tests keep a burst inside one window."""

    def __init__(self, start: float) -> None:
        self.now_value = start

    def time(self) -> float:
        return self.now_value


@pytest.fixture
def fixed_window(monkeypatch):
    """Freeze the fixed window for burst cap assertions.

    These tests assert that a budget trips, not that a wall-clock minute
    contains the whole burst; a boundary crossing would reset the counter
    mid-run.
    """
    monkeypatch.setattr(rl, "time", _Clock(2_000_000.0))


MASTER = b"test-master-key-0123456789abcdef"

RATE_BUCKETS_DDL = """
CREATE TABLE rate_buckets (
    bucket_key VARCHAR(200) PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
)
"""

pytestmark = pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(RATE_BUCKETS_DDL))
    yield engine
    await engine.dispose()


def _factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def _limited_app(session_factory, **kw):
    app = make_app(
        session_factory,
        Settings(master_key=MASTER, cors_origins=[], require_ownership=False),
    )
    app.state.notifier = lambda email, code: None
    install_rate_limit(app, **kw)
    return app


async def _post(ip: str, app, path: str, json_body):
    transport = ASGITransport(app=app, client=(ip, 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post(path, json=json_body)


REDEEM_BODY = {"token": "t" * 43, "email": "invitee@example.com"}
TICKET_BODY = {"projectId": "proj-a"}


def test_shipped_set_and_budget_shape():
    """Membership + budget contract: redeem on the credential-edge default,
    ticket mint on its named wider override."""
    assert rl.DEFAULT_PROTECTED_ROUTES.count(REDEEM_ROUTE) == 1
    assert rl.DEFAULT_PROTECTED_ROUTES.count(WS_TICKET_ROUTE) == 1
    assert rl.limit_for_route(REDEEM_ROUTE) is None  # DEFAULT_LIMIT class
    assert rl.limit_for_route(WS_TICKET_ROUTE) == WS_TICKET_LIMIT
    assert WS_TICKET_LIMIT > rl.DEFAULT_LIMIT


async def test_redeem_burst_trips_429_with_envelope(engine, fixed_window):
    """Middleware counts before the handler: 3 auth-required 401s, then the
    frozen envelope. Unauthenticated spray cannot skip the budget."""
    app = _limited_app(_factory(engine), limit=3, window_seconds=60)
    for _ in range(3):
        resp = await _post("10.9.9.1", app, REDEEM_ROUTE, REDEEM_BODY)
        assert resp.status_code == 401, "not authed — but still counted"
    blocked = await _post("10.9.9.1", app, REDEEM_ROUTE, REDEEM_BODY)
    assert blocked.status_code == 429
    assert blocked.json() == {
        "error": {"code": "RATE_LIMITED", "message": "too many attempts; slow down"}
    }
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60


async def test_ws_ticket_rides_the_wider_override(engine, fixed_window):
    """With the middleware-wide limit at 10, request #11 still passes the
    guard (401 from auth) because the ticket mint's override is 60/min —
    then #61 trips it."""
    app = _limited_app(_factory(engine), limit=10, window_seconds=60)
    for i in range(60):
        resp = await _post("10.9.9.2", app, WS_TICKET_ROUTE, TICKET_BODY)
        assert resp.status_code == 401, f"request {i + 1} unexpectedly limited"
    blocked = await _post("10.9.9.2", app, WS_TICKET_ROUTE, TICKET_BODY)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


async def test_redeem_buckets_are_per_ip(engine, fixed_window):
    app = _limited_app(_factory(engine), limit=1, window_seconds=60)
    assert (await _post("10.9.9.3", app, REDEEM_ROUTE, REDEEM_BODY)).status_code == 401
    assert (await _post("10.9.9.3", app, REDEEM_ROUTE, REDEEM_BODY)).status_code == 429
    # A different client is untouched by the first client's spent budget.
    assert (await _post("10.9.9.4", app, REDEEM_ROUTE, REDEEM_BODY)).status_code == 401


async def test_bucket_keys_are_the_literal_routes(engine):
    app = _limited_app(_factory(engine))
    assert (await _post("10.9.9.5", app, REDEEM_ROUTE, REDEEM_BODY)).status_code == 401
    assert (await _post("10.9.9.5", app, WS_TICKET_ROUTE, TICKET_BODY)).status_code == 401
    async with _factory(engine)() as db:
        keys = [
            row[0]
            for row in (
                await db.execute(text("SELECT bucket_key FROM rate_buckets"))
            ).all()
        ]
    assert len(keys) == 2, "one bucket per (IP, route) — literal keys"
    assert any(k.endswith("|/api/v1/invites/redeem") for k in keys)
    assert any(k.endswith("|/api/v1/ws-ticket") for k in keys)


async def test_unrelated_route_never_limited(engine):
    """A route outside the shipped set (here: project creation) passes the
    guard untouched even at limit=1 — coverage grew, not blanket throttling."""
    app = _limited_app(_factory(engine), limit=1, window_seconds=60)
    for _ in range(3):
        resp = await _post("10.9.9.6", app, "/api/v1/projects", {})
        assert resp.status_code != 429
