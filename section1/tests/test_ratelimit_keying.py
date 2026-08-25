"""M17-B4 (MAC2-F8) — rate-limit keying: prefix normalization + proxy policy.

Phase A finding MAC2-F8: bucket keys used the RAW peer address, so a client
holding an IPv6 block (a /64 is the minimum routable allocation) owned 2^64
distinct buckets — every budget multiplied by address-rotation speed — while
behind ANY reverse proxy all clients shared ONE proxy-IP bucket (a global
DoS fuse). The design was simultaneously too coarse and too fine.

Fix under test:
  1. normalize_client_ip collapses each address to its routable unit
     (IPv6 → /64 network, IPv4 → /32 = unchanged, v4-mapped v6 unwrapped).
  2. effective_client_ip implements the documented proxy-header policy:
     XFF consulted ONLY when the direct peer is a trusted proxy; leftmost
     untrusted entry wins; otherwise peer verbatim and XFF ignored.
  3. The live middleware enforces both: rotating v6 addresses inside one
     block exhausts ONE budget; spoofed XFF from a non-proxy peer is inert.

Runs against the real identity app over aiosqlite exactly like
test_ratelimit.py (the upsert SQL is portable to Postgres/migration 0011).
"""

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

RATE_BUCKETS_DDL = """
CREATE TABLE rate_buckets (
    bucket_key VARCHAR(200) PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
)
"""


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
    app = make_app(
        session_factory,
        Settings(master_key=MASTER, cors_origins=[], require_ownership=False),
    )
    app.state.notifier = lambda email, code: None
    install_rate_limit(app, **kw)
    return app


async def _post(app, ip: str, path: str, headers: dict | None = None):
    transport = ASGITransport(app=app, client=(ip, 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post(path, json={"email": "k@example.com"}, headers=headers or {})


# ---------------------------------------------------------------------------
# 1: /64 (+/32) normalization units
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("addr", "expected"),
    [
        # Same /64 -> same key regardless of interface bits.
        ("2001:db8:1:2:0:0:0:1", "2001:db8:1:2::"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::"),
        ("2001:DB8:1:2::5", "2001:db8:1:2::"),  # case + zero-group normalized
        ("2001:db8:0001:0002::9", "2001:db8:1:2::"),  # long form == short form
        # Neighboring /64 stays DISTINCT.
        ("2001:db8:1:3::1", "2001:db8:1:3::"),
        # Plain v4 passes through (/32 = unchanged), including mapped unwrap.
        ("10.1.2.3", "10.1.2.3"),
        ("::ffff:203.0.113.7", "203.0.113.7"),  # dual-stack surfacing of v4
        ("::ffff:cb00:7104", "203.0.113.4"),  # integer spelling of the same
        # Non-IP inputs keep SOME bucket instead of exploding.
        ("unknown", "unknown"),
        ("", ""),
        ("proxy.local", "proxy.local"),
    ],
)
def test_normalize_client_ip(addr, expected):
    assert rl.normalize_client_ip(addr) == expected


def test_normalized_v6_shares_one_bucket_key_and_v4_stays_distinct():
    assert (
        rl.bucket_key(rl.normalize_client_ip("2001:db8:1:2::1"), "/t")
        == rl.bucket_key(rl.normalize_client_ip("2001:db8:1:2:f00d::"), "/t")
    )
    assert (
        rl.bucket_key(rl.normalize_client_ip("10.0.0.1"), "/t")
        != rl.bucket_key(rl.normalize_client_ip("10.0.0.2"), "/t")
    )


async def test_v6_rotation_within_one_slash64_shares_budget(rl_engine):
    """The exploit from Phase A, end-to-end: 20 requests spread across many
    addresses inside ONE /64 must exhaust at 20 — not mint 20 fresh budgets."""
    app = _limited_app(_factory(rl_engine), limit=20, window_seconds=60)
    for i in range(20):
        addr = f"2001:db8:9:9::{i:x}" if i else "2001:db8:9:9::1"
        res = await _post(app, addr, "/api/v1/auth/request-login")
        assert res.status_code == 200, i
    # A brand-new address in the SAME block mid-window: already capped.
    blocked = await _post(app, "2001:db8:9:9:dead:beef::f", "/api/v1/auth/request-login")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


async def test_v6_rotation_across_blocks_gets_fresh_budgets(rl_engine):
    """Scoping law: DIFFERENT /64s are different clients (a /64 IS the routable
    unit) — normalization must not collapse unrelated allocations."""
    app = _limited_app(_factory(rl_engine), limit=1, window_seconds=60)
    first = await _post(app, "2001:db8:a:a::1", "/api/v1/auth/request-login")
    assert first.status_code == 200
    drained_same_block = await _post(app, "2001:db8:a:a::2", "/api/v1/auth/request-login")
    assert drained_same_block.status_code == 429
    fresh_block = await _post(app, "2001:db8:b:b::1", "/api/v1/auth/request-login")
    assert fresh_block.status_code == 200


# ---------------------------------------------------------------------------
# 2: proxy-policy matrix (effective_client_ip units)
# ---------------------------------------------------------------------------

DEFAULTS = rl.DEFAULT_TRUSTED_PROXY_CIDRS


@pytest.mark.parametrize(
    ("headers", "peer", "expected"),
    [
        # Untrusted peer: XFF present but IGNORED (spoof-proof default)...
        ({"x-forwarded-for": "198.51.100.9"}, "203.0.113.44", "203.0.113.44"),
        # ...including attacker-crafted chains naming internal addresses...
        (
            {"x-forwarded-for": "10.0.0.99, 198.51.100.9"},
            "203.0.113.44",
            "203.0.113.44",
        ),
        # ...or claiming to be loopback.
        ({"x-forwarded-for": "127.0.0.1"}, "203.0.113.44", "203.0.113.44"),
        # Trusted (loopback) peer: leftmost untrusted entry wins...
        ({"x-forwarded-for": "198.51.100.9"}, "127.0.0.1", "198.51.100.9"),
        ({"x-forwarded-for": "198.51.100.9, 10.9.9.9"}, "127.0.0.1", "198.51.100.9"),
        # ...trusted hops are skipped, even several of them...
        (
            {"x-forwarded-for": "198.51.100.9, 172.16.0.9"},
            "127.0.0.1",
            "198.51.100.9",
        ),
        # ...ALL-trusted chain falls back to the peer itself...
        ({"x-forwarded-for": "10.1.1.1, 192.168.5.5"}, "127.0.0.1", "127.0.0.1"),
        # ...RIGHTMOST garbage poisons toward the peer (nothing trustworthy
        # to the right of it to anchor on)...
        ({"x-forwarded-for": "198.51.100.9, not-an-ip"}, "127.0.0.1", "127.0.0.1"),
        ({}, "127.0.0.1", "127.0.0.1"),
        # ...but garbage DEEPER in the chain is unreachable: the walk stops
        # at the first untrusted VALID entry from the right.
        (
            {"x-forwarded-for": "not-an-ip, 198.51.100.9"},
            "127.0.0.1",
            "198.51.100.9",
        ),
        # ...v6 peers normalize through the same path...
        ({}, "2001:db8:7:7::1234", "2001:db8:7:7::"),
        (
            {"x-forwarded-for": "198.51.100.9"},
            "::ffff:203.0.113.44",  # trusted? no: public v4 -> XFF ignored
            "203.0.113.44",
        ),
        # ...unparseable peer never matches trust -> peer verbatim.
        ({"x-forwarded-for": "198.51.100.9"}, "gateway.internal", "gateway.internal"),
    ],
)
def test_proxy_policy_matrix(headers, peer, expected):
    assert rl.effective_client_ip(headers, peer, DEFAULTS) == expected


def test_proxy_policy_matrix_with_empty_trust_set():
    """No trusted proxies configured: EVERY XFF is ignored, even from
    loopback peers — deployments can disable header trust wholesale."""
    assert (
        rl.effective_client_ip({"x-forwarded-for": "198.51.100.9"}, "127.0.0.1", [])
        == "127.0.0.1"
    )
    assert rl.effective_client_ip({}, "127.0.0.1", []) == "127.0.0.1"


def test_proxy_policy_matrix_with_custom_cidr_set():
    """Operator-declared edge LB: only THAT subnet is trusted."""
    lb_only = ["100.64.0.0/10"]  # CGNAT range as a stand-in edge tier
    assert (
        rl.effective_client_ip({"x-forwarded-for": "198.51.100.9"}, "100.64.1.1", lb_only)
        == "198.51.100.9"
    )
    # Loopback is NOT special once the set is overridden.
    assert (
        rl.effective_client_ip({"x-forwarded-for": "198.51.100.9"}, "127.0.0.1", lb_only)
        == "127.0.0.1"
    )


# ---------------------------------------------------------------------------
# 3: policy enforced live through the middleware
# ---------------------------------------------------------------------------


async def test_spoofed_xff_from_untrusted_peer_is_inert_live(rl_engine):
    """Direct client sends XFF naming fresh addresses: budgets do NOT split —
    every request keys to the peer's own normalized identity."""
    app = _limited_app(_factory(rl_engine), limit=3, window_seconds=60)
    for i in range(3):
        res = await _post(
            app,
            "203.0.113.10",
            "/api/v1/auth/request-login",
            headers={"X-Forwarded-For": f"198.51.100.{100 + i}"},
        )
        assert res.status_code == 200, i
    # Fourth attempt — even claiming ANOTHER fresh XFF — hits the wall.
    blocked = await _post(
        app,
        "203.0.113.10",
        "/api/v1/auth/request-login",
        headers={"X-Forwarded-For": "198.51.100.250"},
    )
    assert blocked.status_code == 429


async def test_xff_through_trusted_proxy_splits_budgets_by_claimed_client(rl_engine):
    """Behind a TRUSTED proxy (loopback here), distinct real clients get their
    own buckets — one busy office no longer shares a single global fuse —
    while a forged INTERNAL claim inside the chain is still distrusted."""
    app = _limited_app(_factory(rl_engine), limit=1, window_seconds=60)

    r1 = await _post(
        app, "127.0.0.1", "/api/v1/auth/request-login",
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert r1.status_code == 200
    # Same claimed client again: capped (bucket follows the CLAIMED identity).
    r1_again = await _post(
        app, "127.0.0.1", "/api/v1/auth/request-login",
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert r1_again.status_code == 429

    # Different real client behind the same proxy: untouched budget.
    r2 = await _post(
        app, "127.0.0.1", "/api/v1/auth/request-login",
        headers={"X-Forwarded-For": "198.51.100.8"},
    )
    assert r2.status_code == 200

    # Forged CHAIN: a client behind the proxy prepends a trusted-range id
    # ('10.0.0.5') to look like infrastructure. Distrust rule: the walk from
    # the right stops at the FIRST untrusted VALID entry — 198.51.100.9 — so
    # the forgery only ever lets a client CHOOSE which untrusted id to claim;
    # it can never escalate into an internal/trusted one.
    forged = await _post(
        app, "127.0.0.1", "/api/v1/auth/request-login",
        headers={"X-Forwarded-For": "10.0.0.5, 198.51.100.9"},
    )
    assert forged.status_code == 200  # keyed to .9's own FRESH bucket...
    again = await _post(
        app, "127.0.0.1", "/api/v1/auth/request-login",
        headers={"X-Forwarded-For": "10.0.0.5, 198.51.100.9"},
    )
    assert again.status_code == 429  # ...which then caps like any other id

    # Residual risk (documented in ratelimit.py): behind a trusting proxy a
    # client may still rotate CLAIMED untrusted ids — that is inherent to any
    # XFF policy and is why trust must be scoped to proxies that overwrite,
    # not append, client-supplied chains.
    fresh_claim = await _post(
        app, "127.0.0.1", "/api/v1/auth/request-login",
        headers={"X-Forwarded-For": "198.51.100.200"},
    )
    assert fresh_claim.status_code == 200


async def test_trusted_proxy_policy_overridable_per_install(rl_engine):
    """install_rate_limit(trusted_proxy_cidrs=...) replaces the policy
    wholesale: with NO trusted proxies, even loopback XFF is ignored."""
    app = _limited_app(_factory(rl_engine), limit=1, window_seconds=60,
                       trusted_proxy_cidrs=[])
    first = await _post(
        app, "127.0.0.1", "/api/v1/auth/request-login",
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert first.status_code == 200  # keyed to 127.0.0.1 itself
    second = await _post(
        app, "127.0.0.1", "/api/v1/auth/request-login",
        headers={"X-Forwarded-For": "198.51.100.8"},  # new claim, same peer
    )
    assert second.status_code == 429  # SAME bucket: header ignored entirely
