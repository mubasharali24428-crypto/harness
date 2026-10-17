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

4. VERIFY-OTP THROTTLED UNDER THE SAME POLICY. The default install charges
   ``/auth/verify-otp`` from its own per-IP+route bucket (429 + Retry-After);
   exhausting it never bleeds into the same IP's request-login bucket.

5. OTP SINGLE USE. Replaying a consumed code answers 401 INVALID_CODE — no
   second session from one proof-of-ownership.

6. INVITE-MINT COVERAGE. ``POST /api/v1/teams/{team_id}/invites`` (invites.py
   — one bearer-grade join token per mint) joins the shipped default protected
   set, keyed on the ROUTE TEMPLATE so one IP cannot farm a fresh budget per
   forged team id. ``DEFAULT_ROUTES`` stays byte-equal (tests/test_ratelimit.py
   freezes that pair); ``DEFAULT_PROTECTED_ROUTES`` is what installs ship.
"""

from datetime import UTC, datetime, timedelta

import pytest
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

import harness_identity.ratelimit as rl
from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import (
    AuthSession,
    Base,
    Team,
    TeamMember,
    User,
)
from harness_identity.ratelimit import (
    INVITE_MINT_TEMPLATE,
    install_rate_limit,
    route_matches,
)

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


# ---------------------------------------------------------------------------
# Shared plumbing for the scenarios below (conftest.py stays frozen).
# ---------------------------------------------------------------------------


class _CodeRecorder:
    """Notifier stand-in capturing issued codes IN MEMORY ONLY.

    Codes are credentials: tests may use the latest one for an address to
    drive verify attempts, but must never log or print them.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __call__(self, email: str, code: str) -> None:
        self.sent.append((email.lower().strip(), code))

    def latest_for(self, email: str) -> str:
        wanted = email.lower().strip()
        for addr, code in reversed(self.sent):
            if addr == wanted:
                return code
        raise AssertionError(f"no OTP was issued for {wanted}")


@pytest.fixture
async def otp_factory():
    """Fresh identity store (full schema + rate_buckets mirror) per test."""
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text(RATE_BUCKETS_DDL))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        # aiosqlite spawns a non-daemon thread per engine; dispose or hang.
        await engine.dispose()


def _throttled_app(factory, *, limit: int, window_seconds: int = 60):
    """Real app + rate limiting with the SHIPPED default route set (no routes
    override anywhere below: that override-free spelling is the point)."""
    app = make_app(factory, Settings(master_key=MASTER, cors_origins=[]))
    recorder = _CodeRecorder()
    app.state.notifier = recorder
    install_rate_limit(app, limit=limit, window_seconds=window_seconds)
    return app, recorder


async def _post_as(app, ip: str, url: str, **kw):
    transport = ASGITransport(app=app, client=(ip, 4444))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post(url, **kw)


async def _request_login(app, ip: str, email: str):
    return await _post_as(app, ip, OTP_REQUEST, json={"email": email})


async def _verify_otp(app, ip: str, email: str, code: str):
    return await _post_as(
        app,
        ip,
        "/api/v1/auth/verify-otp",
        json={"email": email, "code": code},
    )


async def _seed_admin(factory, email: str) -> dict:
    """Seed team/user/admin-membership/session rows straight into the store;
    returns ids + bearer (same pattern as tests/test_invites.py)."""
    import secrets

    async with factory() as db:
        team = Team(name=f"admins-{email}")
        db.add(team)
        await db.flush()
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        db.add(
            TeamMember(
                team_id=team.id, user_id=user.id, display_name=user.name, role="admin"
            )
        )
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team.id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    return {"teamId": team.id, "userId": user.id, "token": token}


async def _mint(app, ip: str, admin: dict, email: str = "invitee@example.com"):
    return await _post_as(
        app,
        ip,
        f"/api/v1/teams/{admin['teamId']}/invites",
        json={"email": email, "role": "viewer"},
        headers=_bearer(admin["token"]),
    )


# ---------------------------------------------------------------------------
# Scenario 4: verify-otp throttled under the same default policy
# ---------------------------------------------------------------------------


async def test_verify_otp_throttled_under_same_default_policy(otp_factory):
    factory = otp_factory
    app, recorder = _throttled_app(factory, limit=3, window_seconds=60)
    ip, email = "203.0.113.81", "throttle@example.com"

    assert (await _request_login(app, ip, email)).status_code == 200
    ok = await _verify_otp(app, ip, email, recorder.latest_for(email))
    assert ok.status_code == 200
    assert ok.json()["token"]

    # Wrong-code retries charge the SAME bucket (the limiter sits in front of
    # routing, right or wrong): two 401s drain the remaining budget...
    for _ in range(2):
        wrong = await _verify_otp(app, ip, email, "000000")
        assert wrong.status_code == 401

    # ...then the MIDDLEWARE answers, not the app: RATE_LIMITED + Retry-After.
    blocked = await _verify_otp(app, ip, email, "000000")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60

    # Keying is IP + route template: draining verify-otp never bleeds into the
    # same IP's request-login bucket.
    assert (
        await _request_login(app, ip, "other@example.com")
    ).status_code == 200


# ---------------------------------------------------------------------------
# Scenario 5: one OTP proves inbox ownership exactly once
# ---------------------------------------------------------------------------


async def test_otp_double_redeem_rejected(otp_factory):
    factory = otp_factory
    app, recorder = _throttled_app(factory, limit=10, window_seconds=60)
    ip, email = "203.0.113.82", "reuse@example.com"

    assert (await _request_login(app, ip, email)).status_code == 200
    code = recorder.latest_for(email)

    first = await _verify_otp(app, ip, email, code)
    assert first.status_code == 200
    assert first.json()["token"]

    # Replay of the SAME code: consumed_at is set, so no active row remains.
    # The second attempt must never mint a second session.
    second = await _verify_otp(app, ip, email, code)
    assert second.status_code == 401
    body = second.json()
    assert body["detail"]["error"]["code"] == "INVALID_CODE"
    assert "token" not in body


# ---------------------------------------------------------------------------
# Scenario 6: invite minting is inside the shipped default protection set
# ---------------------------------------------------------------------------


async def test_invite_mint_throttled_by_default_installation(otp_factory):
    factory = otp_factory
    app, _recorder = _throttled_app(factory, limit=2, window_seconds=60)
    ip = "203.0.113.83"
    boss = await _seed_admin(factory, "boss@example.com")

    for i in range(2):
        minted = await _mint(app, ip, boss, email=f"m{i}@example.com")
        assert minted.status_code == 201, minted.text

    blocked = await _mint(app, ip, boss, email="overflow@example.com")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60

    # Buckets are per CLIENT IP: a second address still mints mid-window.
    other = await _mint(app, "203.0.113.84", boss, email="fresh-ip@example.com")
    assert other.status_code == 201


async def test_invite_mint_bucket_keys_on_route_template(otp_factory):
    """The TEMPLATE keys the bucket: draining team A also caps team B paths
    from the same IP. Raw-path keying would hand attackers a fresh budget per
    forged team id and defeat the cap entirely."""
    factory = otp_factory
    app, _recorder = _throttled_app(factory, limit=1, window_seconds=60)
    ip = "203.0.113.85"
    team_a = await _seed_admin(factory, "boss-a@example.com")
    team_b = await _seed_admin(factory, "boss-b@example.com")

    assert (await _mint(app, ip, team_a)).status_code == 201
    assert (
        await _mint(app, ip, team_a, email="second@example.com")
    ).status_code == 429
    # Same IP, DIFFERENT team id in the path: still throttled.
    cross_team = await _mint(app, ip, team_b)
    assert cross_team.status_code == 429
    assert cross_team.json()["error"]["code"] == "RATE_LIMITED"


def test_default_protection_set_covers_invite_mint():
    """Pin the shipped contract: the auth-edge pair (kept byte-equal because
    tests/test_ratelimit.py freezes it) PLUS the previously-unthrottled
    invite-mint template AND passwordless login (F2, V-Pair-1) PLUS the G2
    expensive-op templates (H-021) PLUS the M15-W5-S1 unauthed CSP collector,
    flowing into BOTH wire-up spellings."""
    assert rl.DEFAULT_ROUTES == ("/api/v1/auth/request-login", "/api/v1/auth/verify-otp")
    assert INVITE_MINT_TEMPLATE == "/api/v1/teams/{team_id}/invites"
    # Mission 22 M4: the SSO login edges joined the shipped set (pin updated by
    # that lane; the CSP/expensive-op tail below is unchanged).
    assert rl.SSO_START_ROUTE == "/api/v1/auth/sso/start"
    assert rl.SSO_COMPLETE_ROUTE == "/api/v1/auth/sso/complete"
    assert rl.DEFAULT_PROTECTED_ROUTES == (
        "/api/v1/auth/request-login",
        "/api/v1/auth/verify-otp",
        INVITE_MINT_TEMPLATE,
        "/api/v1/auth/login",
        rl.SSO_START_ROUTE,
        rl.SSO_COMPLETE_ROUTE,
        # M18-C7 (MAC2-F4): redemption + WS-ticket edges joined before the
        # CSP/expensive-op tail (pin updated by that lane; tail unchanged).
        rl.REDEEM_ROUTE,
        rl.WS_TICKET_ROUTE,
        # M15-W5-S1: unauthed browser CSP violation collector.
        rl.CSP_REPORT_TEMPLATE,
        rl.RUN_START_TEMPLATE,
        rl.MODEL_CALL_TEMPLATE,
        rl.BRIEF_GENERATE_TEMPLATE,
    )
    import inspect

    for obj in (rl.RateLimitMiddleware.__init__, rl.install_rate_limit):
        param = inspect.signature(obj).parameters["routes"]
        assert param.default is rl.DEFAULT_PROTECTED_ROUTES


def test_route_template_matching_rules():
    tpl = INVITE_MINT_TEMPLATE
    assert route_matches(tpl, "/api/v1/teams/t-123/invites")
    assert not route_matches(tpl, "/api/v1/teams/t-123/members")  # wrong tail
    assert not route_matches(tpl, "/api/v1/teams/t-123")  # truncated
    assert not route_matches(tpl, "/api/v1/teams/t-123/invites/x")  # extra depth
    assert not route_matches(tpl, "/api/v1/teams//invites")  # empty segment
    # Literal (non-template) routes stay exact-match.
    assert route_matches("/api/v1/auth/request-login", "/api/v1/auth/request-login")
    assert not route_matches("/api/v1/auth/request-login", "/api/v1/auth/request-login/")
