"""M12-W2.2 lane G2 tests (Squad BETA): rate-limit rebalance onto expensive ops.

H-021 (docs/clientela/part1a-critical-high.md) — protection inversion: the
credential edge was throttled while the EXPENSIVE routes answered unthrottled:

  POST /api/v1/orchestrator/runs/{runId}/start   (agent spawn cost)
  POST /api/v1/models/call                        (S3 provider spend)
  POST /api/v1/meetings/{meetingId}/brief         (LLM generation work)

Coverage, mapped to the mission brief:
  1. shipped protected set carries the three templates
                                          -> test_shipped_set_covers_expensive_ops
  2. generous budgets are abuse guards    -> test_expensive_op_budgets_are_generous
  3. burst past a template's own budget -> 429 + Retry-After
                                          -> test_run_start_burst_over_limit_429
                                          -> test_model_call_burst_over_limit_429
                                          -> test_brief_generate_burst_over_limit_429
  4. other routes stay unaffected         -> test_other_routes_unaffected_by_start_budget
                                          -> test_auth_edge_budget_independent_of_expensive_ops
  5. window reset works                   -> test_new_window_resets_start_bucket
  6. template keying defeats id-farming   -> test_template_keying_defeats_id_farming

Runs against the real identity app (make_app) over aiosqlite; the fixed-window
upsert in ratelimit.py is portable SQL, so this exercises the exact statement
Postgres runs (migration 0011's rate_buckets). The orchestrator/model/meeting
routes don't exist on the identity app and that is fine: ASGI middleware runs
BEFORE routing, so the limiter still answers (429) or passes through to the
router (404/405) exactly as it would in front of any service.
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
from harness_identity.ratelimit import (
    BRIEF_GENERATE_LIMIT,
    BRIEF_GENERATE_TEMPLATE,
    MODEL_CALL_LIMIT,
    MODEL_CALL_TEMPLATE,
    RUN_START_LIMIT,
    RUN_START_TEMPLATE,
    install_rate_limit,
)

MASTER = b"test-master-key-0123456789abcdef"

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


def _factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def _limited_app(session_factory, **kw):
    """Real identity app + G2 limiter installed at factory time."""
    app = make_app(
        session_factory,
        Settings(master_key=MASTER, cors_origins=[], require_ownership=False),
    )
    # Wire-up contract (ratelimit.py): one install per app at factory time.
    # The counter store resolves lazily off app.state.session_factory, which
    # make_app set above. kw forwards limiter tuning (limit=, routes=, ...).
    install_rate_limit(app, **kw)
    # Burst suites hit /auth/request-login; swallow the dev OTP printer.
    app.state.notifier = lambda email, code: None
    return app


async def _client_for(app, ip: str):
    transport = ASGITransport(app=app, client=(ip, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


async def _post(ip: str, app, path: str, json_body=None):
    async with await _client_for(app, ip) as c:
        return await c.post(path, json=json_body)


# ---------------------------------------------------------------------------
# 1+2: contract shape of the shipped set / budget table
# ---------------------------------------------------------------------------


def test_shipped_set_covers_expensive_ops():
    """The DEFAULT install now guards run-start, model-call and brief-generate
    beside the auth edge — that IS the H-021 rebalance."""
    assert rl.DEFAULT_PROTECTED_ROUTES[-3:] == (
        RUN_START_TEMPLATE,
        MODEL_CALL_TEMPLATE,
        BRIEF_GENERATE_TEMPLATE,
    )
    assert RUN_START_TEMPLATE == "/api/v1/orchestrator/runs/{runId}/start"
    assert MODEL_CALL_TEMPLATE == "/api/v1/models/call"
    assert BRIEF_GENERATE_TEMPLATE == "/api/v1/meetings/{meetingId}/brief"
    # Mission 22 M4: SSO login edges joined the shipped set BEFORE the
    # expensive-op trio — this lane's [-3:] contract stays intact.
    assert rl.DEFAULT_PROTECTED_ROUTES.count(rl.SSO_START_ROUTE) == 1
    assert rl.DEFAULT_PROTECTED_ROUTES.count(rl.SSO_COMPLETE_ROUTE) == 1
    # M18-C7 (MAC2-F4): redemption + WS-ticket edges join the same set,
    # still BEFORE the expensive-op trio.
    assert rl.DEFAULT_PROTECTED_ROUTES.count(rl.REDEEM_ROUTE) == 1
    assert rl.DEFAULT_PROTECTED_ROUTES.count(rl.WS_TICKET_ROUTE) == 1
    assert rl.DEFAULT_PROTECTED_ROUTES.index(rl.WS_TICKET_ROUTE) < len(
        rl.DEFAULT_PROTECTED_ROUTES
    ) - 3


def test_expensive_op_budgets_are_generous():
    """Abuse guard, not UX blocker: every expensive op gets a WIDER budget than
    the auth edge's DEFAULT_LIMIT, keyed per route TEMPLATE."""
    # M15-W5-S1 added the unauthed CSP collector to the shipped budget table
    # (same 30/min abuse-guard tier as run-start); M18-C7 added the
    # authenticated WS-ticket mint (60/min NAT-safe tier); the G2 expensive
    # ops below are unchanged.
    assert rl.EXPENSIVE_OP_LIMITS == {
        RUN_START_TEMPLATE: RUN_START_LIMIT,
        MODEL_CALL_TEMPLATE: MODEL_CALL_LIMIT,
        BRIEF_GENERATE_TEMPLATE: BRIEF_GENERATE_LIMIT,
        rl.CSP_REPORT_TEMPLATE: rl.CSP_REPORT_LIMIT,
        rl.WS_TICKET_ROUTE: rl.WS_TICKET_LIMIT,
    }
    for budget in (RUN_START_LIMIT, MODEL_CALL_LIMIT, BRIEF_GENERATE_LIMIT):
        assert budget > rl.DEFAULT_LIMIT
    # Mission-brief examples: ~30/min start, ~60/min call.
    assert RUN_START_LIMIT == 30
    assert MODEL_CALL_LIMIT == 60
    assert BRIEF_GENERATE_LIMIT >= 20
    # M18-C7: the ticket mint is wider than the credential edge and rides
    # the override table (redeem stays on DEFAULT_LIMIT by omission).
    assert rl.WS_TICKET_LIMIT == 60
    assert rl.WS_TICKET_LIMIT > rl.DEFAULT_LIMIT
    assert rl.limit_for_route(rl.REDEEM_ROUTE) is None
    # And matching semantics flow through both wire-up spellings.
    assert rl.limit_for_route(RUN_START_TEMPLATE) == RUN_START_LIMIT
    assert rl.limit_for_route("/api/v1/auth/request-login") is None


# ---------------------------------------------------------------------------
# 3: burst past the limit -> 429 + Retry-After (per expensive op)
# ---------------------------------------------------------------------------


async def test_run_start_burst_over_limit_429(rl_engine):
    """30/min start budget: #31 answers 429 RATE_LIMITED with Retry-After."""
    app = _limited_app(_factory(rl_engine))
    ip = "10.21.0.1"
    statuses = []
    for _ in range(RUN_START_LIMIT):
        res = await _post(
            ip, app, "/api/v1/orchestrator/runs/run-g2-a/start"
        )
        statuses.append(res.status_code)
    assert all(s != 429 for s in statuses)  # full budget passes

    blocked = await _post(ip, app, "/api/v1/orchestrator/runs/run-g2-a/start")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60


async def test_model_call_burst_over_limit_429(rl_engine):
    """60/min call budget: #61 answers 429 — S3 spend can't be farmed."""
    app = _limited_app(_factory(rl_engine))
    ip = "10.22.0.1"
    for _ in range(MODEL_CALL_LIMIT):
        res = await _post(ip, app, MODEL_CALL_TEMPLATE)
        assert res.status_code != 429
    blocked = await _post(ip, app, MODEL_CALL_TEMPLATE)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"].isdigit()


async def test_brief_generate_burst_over_limit_429(rl_engine):
    """20/min generate budget with a forged meeting id — the TEMPLATE keys the
    bucket, so the forged ids share one budget and the burst stops at #21."""
    app = _limited_app(_factory(rl_engine))
    ip = "10.23.0.1"
    for i in range(BRIEF_GENERATE_LIMIT):
        res = await _post(ip, app, f"/api/v1/meetings/m-{i}/brief")
        assert res.status_code != 429
    blocked = await _post(ip, app, "/api/v1/meetings/m-overflow/brief")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


async def test_per_route_budgets_apply_together(rl_engine):
    """One install enforces each template's OWN budget: start blocks at 30
    while model-call still has room (its budget is 60)."""
    app = _limited_app(_factory(rl_engine))
    ip = "10.24.0.1"
    for _ in range(RUN_START_LIMIT):
        await _post(ip, app, "/api/v1/orchestrator/runs/r/start")
    assert (
        await _post(ip, app, "/api/v1/orchestrator/runs/r/start")
    ).status_code == 429  # start bucket spent...
    ok_call = await _post(ip, app, MODEL_CALL_TEMPLATE)
    assert ok_call.status_code != 429  # ...model-call budget untouched.


# ---------------------------------------------------------------------------
# 4: other routes unaffected
# ---------------------------------------------------------------------------


async def test_other_routes_unaffected_by_start_budget(rl_engine):
    """Draining the start budget never throttles an unrelated route from the
    same IP (bucketed per route template; /me is not in the protected set)."""
    app = _limited_app(_factory(rl_engine))
    ip = "10.25.0.1"
    for _ in range(RUN_START_LIMIT + 3):
        res = await _post(ip, app, "/api/v1/orchestrator/runs/r/start")
    assert res.status_code == 429  # saturated as expected
    async with await _client_for(app, ip) as c:
        resp = await c.get("/api/v1/me")  # unconfigured route: never 429
    assert resp.status_code != 429


async def test_auth_edge_budget_independent_of_expensive_ops(rl_engine):
    """Rebalance, not merge: burning the OTP-request budget leaves the same
    IP's run-start budget intact (and vice versa)."""
    app = _limited_app(_factory(rl_engine), limit=2)
    ip, path = "10.26.0.1", "/api/v1/auth/request-login"
    for _ in range(3):  # limit=2 → third attempt hits the wall
        res = await _post(ip, app, path, {"email": "g2@example.com"})
    assert res.status_code == 429  # auth-edge bucket spent
    assert (
        await _post(ip, app, "/api/v1/orchestrator/runs/r/start")
    ).status_code != 429  # expensive-op buckets untouched by auth drain


# ---------------------------------------------------------------------------
# 5: window reset
# ---------------------------------------------------------------------------


async def test_new_window_resets_start_bucket(rl_engine, monkeypatch):
    """Crossing the fixed-window boundary restarts the counter at 1 in the
    SAME bucket row (the CASE branch proves the reset happened)."""
    clock = _Clock(2_000_000.0)
    monkeypatch.setattr(rl, "time", clock)
    app = _limited_app(_factory(rl_engine))
    ip, path = "10.27.0.1", "/api/v1/orchestrator/runs/r/start"

    for _ in range(RUN_START_LIMIT):
        assert (await _post(ip, app, path)).status_code != 429
    assert (await _post(ip, app, path)).status_code == 429

    clock.now_value += 61  # cross the boundary
    assert (await _post(ip, app, path)).status_code != 429

    async with _factory(rl_engine)() as db:
        rows = (
            await db.execute(text("SELECT window_start, count FROM rate_buckets"))
        ).all()
    assert len(rows) == 1  # one bucket reused...
    assert rows[0][1] == 1  # ...counter restarted at 1: window reset works.


# ---------------------------------------------------------------------------
# 6: fail-open parity on expensive ops
# ---------------------------------------------------------------------------


class _ExplodingStore:
    """Session factory standing in for an unreachable rate-limit store."""

    def __call__(self):
        raise RuntimeError("simulated store outage")


async def test_expensive_op_fails_open_when_store_down(rl_engine, caplog):
    """Availability parity: with the counter STORE down, run-start keeps
    answering through to the router (404 here) — never a limiter 429 — and a
    warning is logged. Failing open is deliberate; do not 'fix' it."""
    broken = make_app(
        _factory(rl_engine), Settings(master_key=MASTER, cors_origins=[])
    )
    install_rate_limit(
        broken, limit=1, window_seconds=60, session_factory=_ExplodingStore()
    )
    ratelimit_logger = logging.getLogger("harness_identity.ratelimit")
    ratelimit_logger.propagate = True
    with caplog.at_level(logging.WARNING, logger="harness_identity.ratelimit"):
        statuses = [
            (
                await _post("10.28.0.1", broken, "/api/v1/orchestrator/runs/r/start")
            ).status_code
            for _ in range(35)  # far past limit=1 — every request must pass OPEN
        ]
    assert all(s == 404 for s in statuses)  # router answered, limiter stayed out
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("failing OPEN" in r.getMessage() for r in warnings)


# ---------------------------------------------------------------------------
# 7: template keying defeats id-farming
# ---------------------------------------------------------------------------


async def test_template_keying_defeats_id_farming(rl_engine):
    """Fresh run ids cannot mint fresh budgets: one IP drains ONE start bucket
    across every forged id, then every further id is throttled too."""
    app = _limited_app(_factory(rl_engine))
    ip = "10.29.0.1"
    for i in range(RUN_START_LIMIT):
        assert (
            await _post(ip, app, f"/api/v1/orchestrator/runs/forged-{i}/start")
        ).status_code != 429
    for j in range(3):  # brand-new ids mid-window: still capped
        assert (
            await _post(ip, app, f"/api/v1/orchestrator/runs/fresh-{j}/start")
        ).status_code == 429


async def test_different_ips_have_independent_start_budgets(rl_engine):
    """Second client IP starts from a clean bucket mid-window of the first."""
    app = _limited_app(_factory(rl_engine))
    first, second = "10.30.0.1", "10.30.0.2"
    path = "/api/v1/orchestrator/runs/r/start"
    for _ in range(RUN_START_LIMIT + 1):
        res = await _post(first, app, path)
    assert res.status_code == 429  # first IP exhausted
    # A fresh IP starts from a clean bucket even mid-window of the first.
    assert (await _post(second, app, path)).status_code != 429
