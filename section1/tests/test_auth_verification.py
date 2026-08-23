"""M11-W1 rescue — auth verification hardening regression tests.

Covers the W1-A1 leftovers this lane was dispatched to close:

1. UNVERIFIED EMAIL CANNOT CREATE A TEAM. The only credential-free
   team-creating path is the legacy direct exchange ``POST /auth/login``
   (``login_or_register`` provisions user + solo team + demo project).
   With ``require_ownership`` on, that path must answer
   403 OWNERSHIP_REQUIRED and point at the OTP flow.
   (There is no ``POST /teams`` route in section1 — teams are provisioned
   server-side during a successful identity exchange.)

2. VERIFIED INBOX OWNERSHIP ROUND-TRIP. ``/auth/request-login`` ->
   ``/auth/verify-otp`` proves the caller controls the inbox, provisions the
   account/solo team exactly like legacy login, and the resulting principal
   can perform its first real write: ``POST /api/v1/projects`` -> the
   contracted 201.

3. BURST PROTECTION ON THE REAL OTP EDGE. ``ratelimit.DEFAULT_ROUTES`` was
   still naming the phantom route ``/auth/otp/request``; after the fix the
   shipped defaults guard ``/auth/request-login`` + ``/auth/verify-otp``.
   A burst on request-login hits 429 with a machine-readable Retry-After
   (error_body envelope), and once the fixed window rolls over the route
   allows again — proving the counter resets instead of wedging shut.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

import harness_identity.ratelimit as rl
from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import Base
from harness_identity.ratelimit import install_rate_limit

MASTER = b"test-master-key-0123456789abcdef"

pytestmark = pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def otp_app():
    """In-memory identity app behind the ownership flag, notifier captured."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(master_key=MASTER, cors_origins=[], require_ownership=True)
    app = make_app(factory, settings)
    sent: list[tuple[str, str]] = []
    app.state.notifier = lambda email, code: sent.append((email, code))
    try:
        yield app, sent
    finally:
        # aiosqlite spawns a non-daemon thread per engine; dispose or hang.
        await engine.dispose()


# ---------------------------------------------------------------------------
# Scenario 1: unverified email -> no team, 403 OWNERSHIP_REQUIRED
# ---------------------------------------------------------------------------


def test_unverified_email_team_create_is_403_ownership_required(otp_app):
    app, _ = otp_app
    client = TestClient(app)
    r = client.post("/api/v1/auth/login", json={"email": "unverified@example.com"})
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert (detail.get("error") or {}).get("code") == "OWNERSHIP_REQUIRED"
    # And nothing leaked through: the email has no account/session to use.
    me = client.get("/api/v1/me")
    assert me.status_code == 401


# ---------------------------------------------------------------------------
# Scenario 2: verified OTP round-trip provisions and authorizes writes (201)
# ---------------------------------------------------------------------------


def test_verified_otp_round_trip_creates_project_201(otp_app):
    app, sent = otp_app
    client = TestClient(app)

    asked = client.post("/api/v1/auth/request-login", json={"email": "owner@example.com"})
    assert asked.status_code == 200
    assert asked.json()["ok"] is True
    code = sent[0][1]

    verified = client.post(
        "/api/v1/auth/verify-otp", json={"email": "owner@example.com", "code": code}
    )
    assert verified.status_code == 200
    body = verified.json()
    token, principal = body["token"], body["principal"]
    assert token
    assert principal["userId"]  # account provisioned on verified login
    assert principal["role"] == "admin"  # solo team, owner-admin

    me = client.get("/api/v1/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json()["userId"] == principal["userId"]

    # First real write with the verified identity answers the contracted 201
    # (no POST /teams route exists; the solo team came with provisioning).
    created = client.post(
        "/api/v1/projects", headers=_bearer(token), json={"name": "first real project"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "first real project"
    assert created.json()["id"]

    listed = client.get("/api/v1/projects", headers=_bearer(token))
    assert listed.status_code == 200
    names = {p["name"] for p in listed.json()["items"]}
    assert {"demo-repo", "first real project"} <= names


# ---------------------------------------------------------------------------
# Scenario 3: burst on request-login -> 429 + Retry-After -> window reset
# ---------------------------------------------------------------------------

OTP_REQUEST = "/api/v1/auth/request-login"

# Mirrors migrations/0011_rate_buckets.sql (SQLite accepts the same DDL).
RATE_BUCKETS_DDL = """
CREATE TABLE rate_buckets (
    bucket_key VARCHAR(200) PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
)
"""


class _Clock:
    """Replaceable time source so tests can roll the fixed window instantly."""

    def __init__(self, start: float) -> None:
        self.now_value = start

    def time(self) -> float:
        return self.now_value


async def test_request_login_burst_429_retry_after_then_window_reset(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(RATE_BUCKETS_DDL))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    clock = _Clock(1_000_000.0)
    monkeypatch.setattr(rl, "time", clock)

    # Default routes (post-rescue) already include /auth/request-login — pass
    # no routes override so THIS is exactly what the test pins.
    app = make_app(factory, Settings(master_key=MASTER, cors_origins=[]))
    app.state.notifier = lambda email, code: None
    install_rate_limit(app, limit=3, window_seconds=60)

    try:
        transport = ASGITransport(app=app, client=("203.0.113.77", 4444))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for i in range(3):
                r = await c.post(OTP_REQUEST, json={"email": f"burst{i}@example.com"})
                assert r.status_code == 200

            blocked = await c.post(OTP_REQUEST, json={"email": "overflow@example.com"})
            assert blocked.status_code == 429
            # error_body() envelope + machine-readable Retry-After.
            assert blocked.json() == {
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "too many attempts; slow down",
                }
            }
            retry_after = blocked.headers["retry-after"]
            assert retry_after.isdigit()
            assert 1 <= int(retry_after) <= 60

            clock.now_value += 61  # cross the fixed-window boundary
            allowed = await c.post(
                OTP_REQUEST, json={"email": "after-reset@example.com"}
            )
            assert allowed.status_code == 200
    finally:
        await engine.dispose()
