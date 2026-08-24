"""M12-W2.2 lane G2 tests (Squad BETA) — section3 rate-limit hook, H-021.

POST /api/v1/models/call spends real provider budget (S3 spend per request),
so install_router_rate_limit puts the DB-backed fixed-window limiter in front
of the model-router:

1. shipped budget contract: models/call at 60/min
2. burst past the limit -> 429 RATE_LIMITED + Retry-After (limiter answers,
   the provider is never reached)
3. other routes unaffected by the spent call bucket
4. window reset restores the budget in the SAME bucket row
5. store outage fails OPEN with a warning (availability parity)

The counter store runs on aiosqlite — the upsert SQL in ratelimit.py is
portable, so these exercise the exact statement Postgres runs against
rate_buckets (migration 0011). Provider responses are the service's built-in
simulated replies; no network anywhere.
"""

import logging
import sys
from pathlib import Path

# G3-style seam: the shared limiter lives in section1 and this venv does not
# carry harness_identity as an installed distribution, so append its src tree
# BEFORE importing it.
_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

import harness_identity.ratelimit as rl  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from harness_contracts.roles import ModelRole  # noqa: E402
from harness_identity.models import Base  # noqa: E402
from harness_identity.ratelimit import MODEL_CALL_LIMIT, MODEL_CALL_TEMPLATE  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_router.api import router  # noqa: E402
from harness_router.ratelimit_hooks import (  # noqa: E402
    ROUTER_ROUTE_LIMITS,
    install_router_rate_limit,
)
from harness_router.service import ModelRouterService  # noqa: E402

RATE_BUCKETS_DDL = """
CREATE TABLE rate_buckets (
    bucket_key VARCHAR(200) PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
)
"""

CALL_BODY = {
    "agentId": "agent-g2",
    "role": ModelRole.PRIMARY.value,
    "messages": [{"role": "user", "content": "g2 probe"}],
}


class _Clock:
    """Replaceable time source: lets tests roll windows forward instantly."""

    def __init__(self, start: float) -> None:
        self.now_value = start

    def time(self) -> float:
        return self.now_value


@pytest.fixture
async def store_engine():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(RATE_BUCKETS_DDL))
    yield engine
    await engine.dispose()


def _factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def _client_app(session_factory, **kw):
    """Model-router app + G2 hook wired exactly as deploy assembly would."""
    app = FastAPI()
    app.include_router(router)
    install_router_rate_limit(app, session_factory=session_factory, **kw)
    return app


async def _post_call(ip: str, app):
    transport = ASGITransport(app=app, client=(ip, 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post("/api/v1/models/call", json=CALL_BODY)


# ---------------------------------------------------------------------------
# 1+2: budget contract + burst -> 429 + Retry-After
# ---------------------------------------------------------------------------


def test_hook_carries_shipped_call_budget():
    """Contract pin: the hook enforces the G2 model-call template at 60/min."""
    assert ROUTER_ROUTE_LIMITS == {MODEL_CALL_TEMPLATE: MODEL_CALL_LIMIT}
    assert MODEL_CALL_TEMPLATE == "/api/v1/models/call"
    assert MODEL_CALL_LIMIT == 60


async def test_call_burst_over_limit_429_with_retry_after(store_engine):
    """Requests 1..60 pass through to the provider path; #61 hits the wall."""
    app = _client_app(_factory(store_engine))
    ip = "10.51.0.1"
    for _ in range(MODEL_CALL_LIMIT):
        res = await _post_call(ip, app)
        assert res.status_code == 200, res.text

    blocked = await _post_call(ip, app)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60


# ---------------------------------------------------------------------------
# 3: other routes unaffected
# ---------------------------------------------------------------------------


async def test_other_routes_unaffected_by_spent_call_bucket(store_engine):
    """Exhausting /call never throttles /catalog or GET /models from the same
    IP — buckets key on ROUTE TEMPLATE and only /call is configured here."""
    app = _client_app(
        _factory(store_engine),
        routes=(MODEL_CALL_TEMPLATE,),
        route_limits={MODEL_CALL_TEMPLATE: 2},
    )
    ip = "10.52.0.1"
    for _ in range(3):  # past the tiny budget
        res = await _post_call(ip, app)
    assert res.status_code == 429  # call saturated...

    transport = ASGITransport(app=app, client=(ip, 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        catalog = await c.get("/api/v1/models/catalog")
        spend = await c.get("/api/v1/models/spend")
    assert catalog.status_code != 429
    assert spend.status_code != 429


# ---------------------------------------------------------------------------
# 4: window reset
# ---------------------------------------------------------------------------


async def test_window_reset_restores_call_budget(store_engine, monkeypatch):
    """Crossing the fixed-window boundary restarts the SAME bucket at 1."""
    clock = _Clock(4_000_000.0)
    monkeypatch.setattr(rl, "time", clock)
    app = _client_app(
        _factory(store_engine),
        routes=(MODEL_CALL_TEMPLATE,),
        route_limits={MODEL_CALL_TEMPLATE: 2},
    )
    ip = "10.53.0.1"
    for _ in range(2):
        assert (await _post_call(ip, app)).status_code == 200
    assert (await _post_call(ip, app)).status_code == 429

    clock.now_value += 61  # cross the boundary
    assert (await _post_call(ip, app)).status_code == 200

    async with _factory(store_engine)() as db:
        rows = (await db.execute(text("SELECT count FROM rate_buckets"))).all()
    assert len(rows) == 1  # one bucket reused...
    assert rows[0][0] == 1  # ...counter restarted at 1: reset proven.


# ---------------------------------------------------------------------------
# 5: fail-open parity on the expensive op
# ---------------------------------------------------------------------------


class _ExplodingStore:
    """Session factory standing in for an unreachable rate-limit store."""

    def __call__(self):
        raise RuntimeError("simulated store outage")


async def test_store_outage_fails_open_on_call(store_engine, caplog):
    """With the counter STORE down, every call keeps reaching the provider
    path (200) and a warning is logged. Availability parity: NEVER closed."""
    broken = FastAPI()
    broken.include_router(router)
    install_router_rate_limit(broken, session_factory=_ExplodingStore())

    ratelimit_logger = logging.getLogger("harness_identity.ratelimit")
    ratelimit_logger.propagate = True
    with caplog.at_level(logging.WARNING, logger="harness_identity.ratelimit"):
        statuses = [
            (await _post_call("10.54.0.1", broken)).status_code
            for _ in range(MODEL_CALL_LIMIT + 5)  # far past any budget
        ]
    assert statuses == [200] * (MODEL_CALL_LIMIT + 5)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("failing OPEN" in r.getMessage() for r in warnings)
