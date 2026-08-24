"""M12-W2.2 lane G2 tests (Squad BETA) — section4 rate-limit hook, H-021.

POST /api/v1/orchestrator/runs/{runId}/start spawns agents (spawn cost), so
install_orchestrator_rate_limit puts the DB-backed fixed-window limiter in
front of the orchestrator router:

1. burst past the shipped start budget -> 429 RATE_LIMITED + Retry-After
2. other routes unaffected by the spent start bucket
3. fixed window reset restores the budget in the SAME bucket row
4. store outage fails OPEN with a warning (availability parity, never fail-closed)
5. template keying: forged fresh runIds share ONE per-IP budget

No repo conftest exists for section4; this module builds its own apps, same as
test_f1_envelope_idempotency.py / test_plan_gate.py. The counter store runs on
aiosqlite — the upsert SQL in ratelimit.py is portable, so these exercise the
exact statement Postgres runs against rate_buckets (migration 0011).
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

import harness_identity.ratelimit as rl
import pytest
from fastapi import FastAPI
from harness_contracts.roles import Principal, Role
from harness_identity.models import Base
from harness_identity.ratelimit import RUN_START_LIMIT, RUN_START_TEMPLATE
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal, router
from harness_orchestrator.ratelimit_hooks import (
    ORCHESTRATOR_ROUTE_LIMITS,
    install_orchestrator_rate_limit,
)
from harness_orchestrator.service import OrchestratorService

MASTER = b"test-master-key-0123456789abcdef"
START_PATH = "/api/v1/orchestrator/runs/run-g2/start"

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


def _principal() -> Principal:
    return Principal(userId="u-g2", teamId="team-g2", role=Role.ADMIN)


def _client_app(session_factory, **kw):
    """Orchestrator router + G2 hook wired exactly as deploy assembly would."""
    service = OrchestratorService()
    orch_api._service = service
    app = FastAPI()
    app.include_router(router)
    install_orchestrator_rate_limit(app, session_factory=session_factory, **kw)
    app.dependency_overrides[get_principal] = lambda: _principal()
    return app


async def _client_for(app, ip: str):
    transport = ASGITransport(app=app, client=(ip, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


async def _post(ip: str, app, path: str):
    async with await _client_for(app, ip) as c:
        return await c.post(path)


# ---------------------------------------------------------------------------
# 1: burst over the new limit -> 429 + Retry-After
# ---------------------------------------------------------------------------


async def test_hook_carries_shipped_start_budget(store_engine):
    """Contract pin: the hook enforces the G2 start template at 30/min."""
    assert ORCHESTRATOR_ROUTE_LIMITS == {RUN_START_TEMPLATE: RUN_START_LIMIT}
    assert RUN_START_LIMIT == 30


async def test_start_burst_over_limit_429_with_retry_after(store_engine):
    """Requests 1..30 pass the limiter through to the router (any non-429);
    #31 hits the wall: 429 RATE_LIMITED envelope + machine-readable header."""
    app = _client_app(_factory(store_engine))
    ip = "10.41.0.1"
    statuses = []
    for _ in range(RUN_START_LIMIT):
        res = await _post(ip, app, START_PATH)
        statuses.append(res.status_code)
    assert all(s != 429 for s in statuses)

    blocked = await _post(ip, app, START_PATH)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60


# ---------------------------------------------------------------------------
# 2: other routes unaffected
# ---------------------------------------------------------------------------


async def test_other_routes_unaffected_by_spent_start_bucket(store_engine):
    """Exhausting the start budget never throttles pause/status from the same
    IP — buckets key on ROUTE TEMPLATE, and only /start is configured here."""
    app = _client_app(
        _factory(store_engine),
        routes=(RUN_START_TEMPLATE,),
        route_limits={RUN_START_TEMPLATE: 3},
    )
    ip = "10.42.0.1"
    for _ in range(4):  # past the tiny budget
        res = await _post(ip, app, START_PATH)
    assert res.status_code == 429  # start saturated...

    paused = await _post(ip, app, "/api/v1/orchestrator/runs/run-g2/pause")
    assert paused.status_code != 429  # ...pause untouched (router answered)
    async with await _client_for(app, ip) as c:
        status_resp = await c.get("/api/v1/orchestrator/runs/run-g2/status")
    assert status_resp.status_code != 429


# ---------------------------------------------------------------------------
# 3: window reset
# ---------------------------------------------------------------------------


async def test_window_reset_restores_start_budget(store_engine, monkeypatch):
    """Crossing the fixed-window boundary restarts the SAME bucket at 1."""
    clock = _Clock(3_000_000.0)
    monkeypatch.setattr(rl, "time", clock)
    app = _client_app(
        _factory(store_engine),
        routes=(RUN_START_TEMPLATE,),
        route_limits={RUN_START_TEMPLATE: 2},
    )
    ip = "10.43.0.1"
    for _ in range(2):
        assert (await _post(ip, app, START_PATH)).status_code != 429
    assert (await _post(ip, app, START_PATH)).status_code == 429

    clock.now_value += 61  # cross the boundary
    assert (await _post(ip, app, START_PATH)).status_code != 429

    async with _factory(store_engine)() as db:
        rows = (
            await db.execute(text("SELECT count FROM rate_buckets"))
        ).all()
    assert len(rows) == 1  # one bucket reused...
    assert rows[0][0] == 1  # ...counter restarted at 1: reset proven.


# ---------------------------------------------------------------------------
# 4: fail-open parity on the expensive op
# ---------------------------------------------------------------------------


class _ExplodingStore:
    """Session factory standing in for an unreachable rate-limit store."""

    def __call__(self):
        raise RuntimeError("simulated store outage")


async def test_store_outage_fails_open_on_start(store_engine, caplog):
    """With the counter STORE down, run-start keeps reaching the router (403
    here — plan gate answers, not the limiter) and a warning is logged.
    Availability parity with the auth edge: NEVER fail-closed."""
    service = OrchestratorService()
    orch_api._service = service
    app = FastAPI()
    app.include_router(router)
    install_orchestrator_rate_limit(app, session_factory=_ExplodingStore())
    app.dependency_overrides[get_principal] = lambda: _principal()

    ratelimit_logger = logging.getLogger("harness_identity.ratelimit")
    ratelimit_logger.propagate = True
    with caplog.at_level(logging.WARNING, logger="harness_identity.ratelimit"):
        statuses = [
            (
                await _post("10.44.0.1", app, START_PATH)
            ).status_code
            for _ in range(RUN_START_LIMIT + 5)  # far past any budget
        ]
    assert all(s != 429 for s in statuses)  # limiter stayed out of the way
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("failing OPEN" in r.getMessage() for r in warnings)


# ---------------------------------------------------------------------------
# 5: template keying defeats id-farming
# ---------------------------------------------------------------------------


async def test_template_keying_defeats_id_farming(store_engine):
    """One IP drains ONE start bucket across every forged run id."""
    app = _client_app(
        _factory(store_engine),
        routes=(RUN_START_TEMPLATE,),
        route_limits={RUN_START_TEMPLATE: 3},
    )
    ip = "10.45.0.1"
    for i in range(3):
        assert (
            await _post(ip, app, f"/api/v1/orchestrator/runs/forged-{i}/start")
        ).status_code != 429
    for j in range(2):  # brand-new ids mid-window: still capped
        res = await _post(ip, app, f"/api/v1/orchestrator/runs/fresh-{j}/start")
        assert res.status_code == 429
