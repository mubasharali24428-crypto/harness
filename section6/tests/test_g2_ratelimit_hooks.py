"""M12-W2.2 lane G2 tests (Squad BETA) — section6 rate-limit hook, H-021.

POST /api/v1/meetings/{meetingId}/brief runs LLM generation work, so
install_meeting_rate_limit puts the DB-backed fixed-window limiter in front of
the meeting router:

1. shipped budget contract: brief-generate at 20/min
2. burst past the limit -> 429 RATE_LIMITED + Retry-After (the generator never
   runs for throttled requests)
3. template keying: forged fresh meetingIds share ONE per-IP budget
4. other routes unaffected by the spent brief bucket
5. window reset restores the budget in the SAME bucket row
6. store outage fails OPEN with a warning (availability parity)

The counter store runs on aiosqlite — the upsert SQL in ratelimit.py is
portable, so these exercise the exact statement Postgres runs against
rate_buckets (migration 0011). The meeting service answers in-memory; the
limiter sits in front of routing, so unthrottled requests reach the real
router (404/201 — anything but 429).
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
from harness_contracts.ratelimit import (  # noqa: E402
    BRIEF_GENERATE_LIMIT,
    BRIEF_GENERATE_TEMPLATE,
)
from harness_contracts.roles import Principal, Role  # noqa: E402
from harness_identity.models import Base  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_meeting.api import get_principal, router  # noqa: E402
from harness_meeting.ratelimit_hooks import (  # noqa: E402
    MEETING_ROUTE_LIMITS,
    install_meeting_rate_limit,
)

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
    """Meeting app + G2 hook wired exactly as deploy assembly would."""
    app = FastAPI()
    app.include_router(router)
    install_meeting_rate_limit(app, session_factory=session_factory, **kw)
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId="u-g2", teamId="team-g2", role=Role.ADMIN
    )
    return app


async def _post_brief(ip: str, app, meeting_id: str):
    transport = ASGITransport(app=app, client=(ip, 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post(f"/api/v1/meetings/{meeting_id}/brief")


# ---------------------------------------------------------------------------
# 1+2: budget contract + burst -> 429 + Retry-After
# ---------------------------------------------------------------------------


def test_hook_carries_shipped_brief_budget():
    """Contract pin: the hook enforces the G2 brief template at 20/min."""
    assert MEETING_ROUTE_LIMITS == {BRIEF_GENERATE_TEMPLATE: BRIEF_GENERATE_LIMIT}
    assert BRIEF_GENERATE_TEMPLATE == "/api/v1/meetings/{meetingId}/brief"
    assert BRIEF_GENERATE_LIMIT == 20


async def test_brief_burst_over_limit_429_with_retry_after(store_engine):
    """Forged fresh meetingIds share ONE template bucket; #21 hits the wall."""
    app = _client_app(_factory(store_engine))
    ip = "10.61.0.1"
    statuses = []
    for i in range(BRIEF_GENERATE_LIMIT):
        res = await _post_brief(ip, app, f"m-{i}")
        statuses.append(res.status_code)
    assert all(s != 429 for s in statuses)

    blocked = await _post_brief(ip, app, "m-overflow")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60


# ---------------------------------------------------------------------------
# 3: other routes unaffected
# ---------------------------------------------------------------------------


async def test_other_routes_unaffected_by_spent_brief_bucket(store_engine):
    """Exhausting brief generation never throttles message posting from the
    same IP — buckets key on ROUTE TEMPLATE and only the brief route is
    configured here."""
    app = _client_app(
        _factory(store_engine),
        routes=(BRIEF_GENERATE_TEMPLATE,),
        route_limits={BRIEF_GENERATE_TEMPLATE: 1},
    )
    ip = "10.62.0.1"
    res = await _post_brief(ip, app, "m-a")
    assert res.status_code != 429  # budget consumed by this first call...
    res = await _post_brief(ip, app, "m-b")
    assert res.status_code == 429  # ...second is capped...

    transport = ASGITransport(app=app, client=(ip, 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        posted = await c.post(
            "/api/v1/meetings/m-a/messages",
            json={"userId": "u-g2", "content": "still fine"},
        )
    assert posted.status_code != 429  # ...messages untouched.


# ---------------------------------------------------------------------------
# 4: window reset
# ---------------------------------------------------------------------------


async def test_window_reset_restores_brief_budget(store_engine, monkeypatch):
    """Crossing the fixed-window boundary restarts the SAME bucket at 1."""
    clock = _Clock(5_000_000.0)
    monkeypatch.setattr(rl, "time", clock)
    app = _client_app(
        _factory(store_engine),
        routes=(BRIEF_GENERATE_TEMPLATE,),
        route_limits={BRIEF_GENERATE_TEMPLATE: 2},
    )
    ip = "10.63.0.1"
    for i in range(2):
        assert (await _post_brief(ip, app, f"m-{i}")).status_code != 429
    assert (await _post_brief(ip, app, "m-x")).status_code == 429

    clock.now_value += 61  # cross the boundary
    assert (await _post_brief(ip, app, "m-y")).status_code != 429

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


async def test_store_outage_fails_open_on_brief(store_engine, caplog):
    """With the counter STORE down, every brief request keeps reaching the
    router and a warning is logged. Availability parity: NEVER closed."""
    broken = FastAPI()
    broken.include_router(router)
    broken.dependency_overrides[get_principal] = lambda: Principal(
        userId="u-g2", teamId="team-g2", role=Role.ADMIN
    )
    install_meeting_rate_limit(broken, session_factory=_ExplodingStore())

    ratelimit_logger = logging.getLogger("harness_identity.ratelimit")
    ratelimit_logger.propagate = True
    with caplog.at_level(logging.WARNING, logger="harness_identity.ratelimit"):
        statuses = [
            (await _post_brief("10.64.0.1", broken, f"m-{i}")).status_code
            for i in range(BRIEF_GENERATE_LIMIT + 5)  # far past any budget
        ]
    assert all(s != 429 for s in statuses)  # limiter stayed out of the way
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("failing OPEN" in r.getMessage() for r in warnings)
