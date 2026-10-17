"""Mission 10 node B5: Postgres fixed-window rate limiting for the auth edge.

M12-W2.2 lane G2 (H-021): guards the credential-spray surface (OTP request +
verify), the invite-mint write, AND the platform's expensive ops — orchestrator
run-start (spawn cost), model-call (S3 spend), brief-generate (LLM work).
M18-C7 (MAC2-F4): invite redemption + WS-ticket minting join the shipped set
(the last two auth-adjacent edges that lacked coverage).

Per-IP+route fixed-window counters persist in ``rate_buckets`` (migration
0011). The counter lives in Postgres, not process memory, so the limit holds
across uvicorn workers and restarts — in-memory buckets reset on every deploy
and are invisible to sibling workers.

DESIGN CHOICE — DB failure FAILS OPEN (availability beats strictness):
if the rate-limit store is unreachable or errors, requests are allowed
through and a warning is logged. A login outage is worse than a temporary
limiting blind spot; an attacker gains at most one degraded window while the
store recovers. This is deliberate and must not be "fixed" into fail-closed.

Wire-up contract (one line per app at factory time, BEFORE startup — same
constraint as add_middleware):

    install_rate_limit(app)

The factory returns the concrete class so callers can use either spelling:
``install_rate_limit(app)`` or ``app.add_middleware(install_rate_limit(app))``.
Pure-ASGI middleware (no BaseHTTPMiddleware task overhead), matching
security_headers.py. The session factory is read off ``app.state`` lazily so
wiring order never matters.

Concurrency: one atomic statement per limited request —

    INSERT INTO rate_buckets (bucket_key, window_start, count)
    VALUES (:bucket_key, :window_start, 1)
    ON CONFLICT (bucket_key) DO UPDATE SET
        count = CASE WHEN rate_buckets.window_start = excluded.window_start
                     THEN rate_buckets.count + 1 ELSE 1 END,
        window_start = excluded.window_start
    RETURNING count

Under concurrent upserts Postgres serializes on the PK row lock: exactly one
statement wins per key per instant, so two racing requests can never observe
the same count and slip past the limit together.

M18-C5 (MAC2-F9) — bucket garbage collection: ``rate_buckets`` rows were
previously immortal (migration 0011 creates the table; nothing ever deleted),
so every unique ``(ip|template)`` pair was a permanent PK row and an attacker
rotating IPv6 addresses could grow the store unboundedly — with ``_check``
failing OPEN under store pressure, the same attack then disabled limiting.
Fix: an opportunistic sweep deletes buckets whose fixed window is fully in
the past (they can only be reset by a NEW request, which re-inserts), riding
the existing per-request DB session exactly like the ws_tickets sweep rides
mint (ephemeral.py). Sweep cadence is bounded by BUCKET_GC_EVERY so the
DELETE runs at most once per window instead of on every limited request;
a sweep failure never affects the request's own limit decision.
"""

import ipaddress
import json
import logging
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from harness_contracts import error_body
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

__all__ = [
    "DEFAULT_PROTECTED_ROUTES",
    "DEFAULT_TRUSTED_PROXY_CIDRS",
    "EXPENSIVE_OP_LIMITS",
    "INVITE_MINT_TEMPLATE",
    "MODEL_CALL_LIMIT",
    "MODEL_CALL_TEMPLATE",
    "REDEEM_ROUTE",
    "RUN_START_LIMIT",
    "RUN_START_TEMPLATE",
    "BRIEF_GENERATE_LIMIT",
    "BRIEF_GENERATE_TEMPLATE",
    "WS_TICKET_LIMIT",
    "WS_TICKET_ROUTE",
    "RateLimitMiddleware",
    "effective_client_ip",
    "expensive_op_limits",
    "install_rate_limit",
    "limit_for_route",
    "normalize_client_ip",
    "route_matches",
    "sweep_expired_buckets",
]

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 10
DEFAULT_WINDOW_SECONDS = 60

# M18-C5 (MAC2-F9): bucket GC cadence — the sweep runs on at most one
# ``_check`` per window per middleware instance. Buckets are fixed-window
# counters: once their window_start is in the past they are inert (the next
# request for that key resets to 1 via the upsert's CASE), so deleting them
# loses nothing.
BUCKET_GC_EVERY_WINDOWS = 1

# M11-W1 rescue: guard the REAL auth edge. The legacy "/auth/otp/request"
# path never existed in app.py (routes are /auth/request-login +
# /auth/verify-otp); the old default limited a phantom route while leaving
# the actual OTP endpoints unprotected.
DEFAULT_ROUTES = ("/api/v1/auth/request-login", "/api/v1/auth/verify-otp")

# Wave9-C follow-up: invite MINTING (invites.py POST /teams/{team_id}/invites)
# emails one bearer-grade join token per call, so an unthrottled mint means
# mail-bombing plus token farming. It joins the shipped default coverage via
# DEFAULT_PROTECTED_ROUTES below (what install_rate_limit ships by default);
# DEFAULT_ROUTES itself stays byte-identical because tests/test_ratelimit.py
# pins it as the auth-edge contract.
# A "{seg}" segment marks a route TEMPLATE: concrete paths are matched against
# it, and the TEMPLATE keys the bucket, so one IP shares one budget across
# every team id instead of farming a fresh bucket per forged team id.
INVITE_MINT_TEMPLATE = "/api/v1/teams/{team_id}/invites"
# F2 (V-Pair-1): passwordless /auth/login mints admin teams when
# require_ownership is off — it belongs in the shipped protected set beside
# the OTP edge. (In-process _login_rate_limited in app.py stays as a second
# layer; this makes the DB-backed limiter cover the route too.)
LOGIN_TEMPLATE = "/api/v1/auth/login"
# Mission 22 M4 (S1): the two unauth SSO login edges join the shipped
# protected set — sso/start mints bearer-grade state rows and sso/complete
# performs an outbound token exchange per call, so both need abuse budgets
# exactly like the OTP edge. Literal routes (no path params) => exact keys.
SSO_START_ROUTE = "/api/v1/auth/sso/start"
SSO_COMPLETE_ROUTE = "/api/v1/auth/sso/complete"

# M18-C7 (MAC2-F4): two auth-adjacent edges were unthrottled — invite
# REDEMPTION (authenticated, but per-call token-hash lookup + single-use
# claim on bearer-grade invite tokens) and WS-ticket MINTING (one DB row per
# call; the minted ticket is bearer-grade for the WS upgrade). Redeem rides
# the DEFAULT_LIMIT credential-edge budget like OTP/login; the ticket mint
# gets a wider named budget: it is authenticated and NAT-shared (one mint
# per WS connect, not per message), so the guard must not become a UX
# blocker. Literal routes (no path params) => exact keys.
REDEEM_ROUTE = "/api/v1/invites/redeem"
WS_TICKET_ROUTE = "/api/v1/ws-ticket"
WS_TICKET_LIMIT = 60  # per minute per IP: authenticated ticket mint (DB row)

# M12-W2.2 lane G2 (H-021): rate-limit rebalance onto EXPENSIVE OPS.
#
# H-021 was a protection inversion: credential edges throttled at 10/min while
# run-start (agent spawn), model-call (S3 spend per request) and brief-generate
# (LLM work) answered unthrottled. These templates join the shipped protected
# set with GENEROUS budgets — an abuse guard, not a UX blocker (a human driving
# the UI cannot approach 30 run-starts or 60 model-calls in one minute; a
# script draining someone's S3 budget can).
#
# Keying stays per-IP + ROUTE TEMPLATE exactly like the auth edge, so forging
# fresh run ids / meeting ids cannot farm a fresh bucket.
RUN_START_TEMPLATE = "/api/v1/orchestrator/runs/{runId}/start"  # spawn cost
MODEL_CALL_TEMPLATE = "/api/v1/models/call"  # S3 provider spend
BRIEF_GENERATE_TEMPLATE = "/api/v1/meetings/{meetingId}/brief"  # LLM generation

# M15-W5-S1: the browser CSP violation collector is UNAUTH by design (browsers
# fire reports without credentials), which makes it exactly the kind of route
# that must join the shipped protected set — a scripted flood otherwise spins
# the violation log. Same template-keying law as above: one IP shares one
# budget regardless of report content.
CSP_REPORT_TEMPLATE = "/api/v1/csp-report"
CSP_REPORT_LIMIT = 30  # per minute per IP: real pages emit reports sparsely

# G2 budgets: generous caps — abuse guards, not UX blockers (see block above).
RUN_START_LIMIT = 30  # per minute per IP: agent spawn cost
MODEL_CALL_LIMIT = 60  # per minute per IP: S3 provider spend
BRIEF_GENERATE_LIMIT = 20  # per minute per IP: LLM generation work

EXPENSIVE_OP_LIMITS: dict[str, int] = {
    RUN_START_TEMPLATE: RUN_START_LIMIT,
    MODEL_CALL_TEMPLATE: MODEL_CALL_LIMIT,
    BRIEF_GENERATE_TEMPLATE: BRIEF_GENERATE_LIMIT,
    # M15-W5-S1: unauthed browser collector joins the shipped budget table.
    CSP_REPORT_TEMPLATE: CSP_REPORT_LIMIT,
    # M18-C7 (MAC2-F4): the WS-ticket mint is the one authenticated
    # non-credential edge in the table — wider than DEFAULT_LIMIT (NAT-safe).
    WS_TICKET_ROUTE: WS_TICKET_LIMIT,
}

# The expensive ops join the SHIPPED protected set — this tuple is what
# install_rate_limit guards by default. DEFAULT_ROUTES itself stays
# byte-identical (tests/test_ratelimit.py pins it as the auth-edge contract).
# NOTE ordering law: M15-W5-S1's collector slot sits BEFORE the G2 expensive-op
# trio so tests/test_ratelimit_expensive_ops.py's [-3:] contract stays intact.
DEFAULT_PROTECTED_ROUTES = (
    *DEFAULT_ROUTES,
    INVITE_MINT_TEMPLATE,
    LOGIN_TEMPLATE,
    # Mission 22 M4: SSO login edges (start mints state rows; complete does
    # an outbound token exchange per attempt) — abuse-guarded like the OTP edge.
    SSO_START_ROUTE,
    SSO_COMPLETE_ROUTE,
    # M18-C7 (MAC2-F4): redemption + ticket-mint edges join the shipped set.
    REDEEM_ROUTE,
    WS_TICKET_ROUTE,
    # M15-W5-S1: unauthed browser CSP collector ships rate-limited.
    CSP_REPORT_TEMPLATE,
    RUN_START_TEMPLATE,
    MODEL_CALL_TEMPLATE,
    BRIEF_GENERATE_TEMPLATE,
)


def expensive_op_limits() -> dict[str, int]:
    """Copy of the expensive-op budget table (callers may mutate freely)."""
    return dict(EXPENSIVE_OP_LIMITS)


def limit_for_route(route_template: str) -> int | None:
    """Per-route limit override for a TEMPLATE, or None to use ``limit``."""
    return EXPENSIVE_OP_LIMITS.get(route_template)


def _effective_limits(
    route_template: str, limit: int, overrides: Mapping[str, int] | None
) -> int:
    if not overrides:
        return limit
    return int(overrides.get(route_template, limit))

# Single-statement fixed-window counter (see module docstring for why this is
# race-free). RETURNING yields the post-increment count in the same round trip;
# the CASE resets to 1 whenever the stored window no longer matches the current
# one. Portable SQL: also exercised by the test suite on SQLite (aiosqlite).
_UPSERT = text(
    """
    INSERT INTO rate_buckets (bucket_key, window_start, count)
    VALUES (:bucket_key, :window_start, 1)
    ON CONFLICT (bucket_key) DO UPDATE SET
        count = CASE WHEN rate_buckets.window_start = excluded.window_start
                     THEN rate_buckets.count + 1 ELSE 1 END,
        window_start = excluded.window_start
    RETURNING count
    """
)


def current_window(window_seconds: int, now_epoch: float | None = None) -> tuple[int, float]:
    """Current fixed-window index and its epoch start (floor(now / window))."""
    if now_epoch is None:
        now_epoch = time.time()
    index = int(now_epoch // window_seconds)
    return index, index * window_seconds


# M18-C5 (MAC2-F9): opportunistic bucket GC — the ws_tickets sweep pattern
# (ephemeral.py deletes expired tickets at every mint) applied to rate_buckets.
# A bucket whose window_start is strictly older than the CURRENT window start
# is dead: its counter can never be consulted again without being reset first
# (the upsert's CASE writes count=1 whenever stored window != current window),
# so deleting it cannot change any limit decision. The cutoff binds a real
# datetime because asyncpg rejects string params for TIMESTAMPTZ while SQLite
# accepts datetime objects natively (repo dialect law).
async def sweep_expired_buckets(
    factory: async_sessionmaker,
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    now_epoch: float | None = None,
) -> int:
    """DELETE buckets from fully-elapsed windows; return rows removed.

    Called opportunistically by ``RateLimitMiddleware._check`` at most once
    per window; also importable for direct use (maintenance runners, tests).
    Raises on store failure — the CALLER decides the failure posture (the
    limiter must keep failing OPEN; see module docstring).
    """
    _, current_epoch = current_window(window_seconds, now_epoch)
    cutoff = datetime.fromtimestamp(current_epoch, tz=UTC)
    async with factory() as db:
        result = await db.execute(
            text("DELETE FROM rate_buckets WHERE window_start < :cutoff"),
            {"cutoff": cutoff},
        )
        await db.commit()
        return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# M17-B4 (MAC2-F8): client-identity keying.
#
# Phase A found bucket_key fed the RAW peer address into the key, so a client
# holding an IPv6 block (a /64 is the minimum routable allocation) owned 2^64
# distinct buckets and multiplied every budget by rotation speed — while
# behind any reverse proxy ALL clients collapsed into the ONE proxy-IP bucket
# (a global DoS fuse). Both halves are fixed here:
#
#   normalize_client_ip  — collapse each address to its ROUTABLE UNIT
#                          (IPv6 → /64, IPv4 → /32 = unchanged) so rotating
#                          addresses inside one block shares one budget.
#   effective_client_ip  — documented proxy-header policy: XFF is trusted
#                          ONLY from configured trusted-proxy CIDRs (default
#                          loopback + link-local + private ranges; override
#                          with HARNESS_TRUSTED_PROXY_CIDRS); otherwise the
#                          peer address is used verbatim and spoofed headers
#                          are ignored.
# ---------------------------------------------------------------------------

DEFAULT_TRUSTED_PROXY_CIDRS: tuple[str, ...] = (
    "127.0.0.0/8",  # IPv4 loopback
    "::1/128",  # IPv6 loopback
    "::/128",  # unspecified
    "fc00::/7",  # IPv6 unique-local
    "fe80::/10",  # IPv6 link-local
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",  # RFC1918 private
)


def _parse_cidrs(cidrs: Sequence[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    parsed = []
    for cidr in cidrs:
        try:
            parsed.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("ignoring unparseable trusted-proxy CIDR %r", cidr)
    return tuple(parsed)


def normalize_client_ip(client_ip: str) -> str:
    """Collapse an IP to its routable unit for rate-limit keying.

    IPv6 → /64 network address (the smallest allocation a client can rotate
    through), IPv4 → /32 (unchanged — v4 has no meaningful prefix to share
    and NAT pools must not be merged into shared budgets). IPv4-mapped IPv6
    peers (::ffff:a.b.c.d, how dual-stack sockets surface v4 clients on v6
    sockets) are unwrapped FIRST so they key as their real v4 form instead of
    all landing in one giant ::ffff:/96 bucket. Unparseable inputs (hostnames,
    "unknown") pass through unchanged so they still get SOME bucket.

    Key stability note: ip_network(...).network_address normalizes
    abbreviations (::1 vs 0:0:0:0:0:0:1) to canonical compressed form.
    """
    text_ip = (client_ip or "").strip()
    if not text_ip:
        return ""
    try:
        addr = ipaddress.ip_address(text_ip)
    except ValueError:
        return text_ip
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        # Dual-stack surfacing of a plain IPv4 peer: key as v4 (/32).
        return str(addr.ipv4_mapped)
    if addr.version == 6:
        network = ipaddress.ip_network(f"{addr}/64", strict=False)
        return str(network.network_address)
    return str(addr)


def effective_client_ip(
    scope_headers: Mapping[str, str],
    peer_addr: str,
    trusted_proxy_cidrs: Sequence[str] = DEFAULT_TRUSTED_PROXY_CIDRS,
) -> str:
    """Client identity for keying, honoring the documented proxy policy.

    X-Forwarded-For is consulted ONLY when the direct peer is itself a
    TRUSTED proxy (in ``trusted_proxy_cidrs``): the leftmost untrusted entry
    wins — the rightmost entries are what trusted proxies APPENDED from
    their own observations, so anything at or beyond the first untrusted hop
    is attacker-supplied. With no trusted proxy configured to match, the raw
    peer address is used and any XFF present is ignored entirely (spoofed
    headers cannot split or steer budgets).
    """
    peer = (peer_addr or "").strip()
    proxies = _parse_cidrs(trusted_proxy_cidrs)
    peer_trusted = False
    try:
        peer_addr_obj = ipaddress.ip_address(peer)
        peer_trusted = any(peer_addr_obj in net for net in proxies)
    except ValueError:
        pass

    candidate: str | None = None
    if peer_trusted:
        raw_xff = scope_headers.get("x-forwarded-for", "")
        chain = [hop.strip() for hop in raw_xff.split(",") if hop.strip()]
        for hop in reversed(chain):
            try:
                hop_ip = ipaddress.ip_address(hop)
            except ValueError:
                break  # malformed entry: distrust everything before it too
            if not any(hop_ip in net for net in proxies):
                candidate = str(hop_ip)
                break

    return normalize_client_ip(candidate if candidate is not None else peer)


def bucket_key(client_ip: str, route_template: str) -> str:
    """Limit key: NORMALIZED client IP + route TEMPLATE (not the raw path),
    capped at the column width so oversized keys fail safe instead of
    erroring."""
    return f"{client_ip}|{route_template}"[:200]


def route_matches(route_template: str, path: str) -> bool:
    """Match a concrete request path against a configured route.

    Literal routes compare exactly. A "{param}" segment matches exactly one
    non-empty path segment (FastAPI 404s the empty case anyway), and segment
    counts must agree, so prefix overlaps never false-positive.
    """
    wanted = route_template.split("/")
    got = path.split("/")
    if len(wanted) != len(got):
        return False
    for tmpl_seg, path_seg in zip(wanted, got, strict=True):
        if tmpl_seg.startswith("{") and tmpl_seg.endswith("}"):
            if not path_seg:
                return False
        elif tmpl_seg != path_seg:
            return False
    return True


class RateLimitMiddleware:
    """Wrap the ASGI callable directly; enforce limits on configured routes."""

    def __init__(
        self,
        app,
        session_factory=None,
        *,
        limit: int = DEFAULT_LIMIT,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        routes: tuple[str, ...] = DEFAULT_PROTECTED_ROUTES,
        route_limits: Mapping[str, int] | None = None,
        trusted_proxy_cidrs: Sequence[str] | None = None,
        bind_state=None,  # noqa: ANN001 — app.state for lazy factory lookup
    ) -> None:  # noqa: ANN001 — pure ASGI signature
        self.app = app
        self._session_factory = session_factory
        self.bind_state = bind_state
        self.limit = limit
        self.window_seconds = window_seconds
        self.routes = tuple(routes)
        # M17-B4 (F8): proxy-header trust policy. Default keeps the historical
        # peer-address-only behavior for every deployment that has not opted
        # in; pass explicit CIDRs (or HARNESS_TRUSTED_PROXY_CIDRS) when the
        # limiter sits behind a reverse proxy that overwrites XFF.
        self.trusted_proxy_cidrs = (
            tuple(trusted_proxy_cidrs)
            if trusted_proxy_cidrs is not None
            else DEFAULT_TRUSTED_PROXY_CIDRS
        )
        # G2 (H-021): per-route budget overrides keyed by ROUTE TEMPLATE.
        # Defaults to the shipped expensive-op table; passing an explicit dict
        # replaces it wholesale (tests pin exact budgets that way). A None
        # entry would mean "use limit", but get() already yields None → fall
        # back below, so only positive ints are meaningful here.
        self.route_limits: Mapping[str, int] | None = (
            dict(route_limits)
            if route_limits is not None
            else dict(EXPENSIVE_OP_LIMITS)
        )
        # Exact-hit fast path; templates checked in configured order (first
        # match wins, deterministic even if two templates overlap).
        self._exact = frozenset(r for r in self.routes if "{" not in r)
        self._templates = tuple(r for r in self.routes if "{" in r)
        # M18-C5 (MAC2-F9): GC throttle — the fixed-window index whose sweep
        # last ran; a window index may only run once per middleware instance.
        self._gc_window_index: int | None = None

    @property
    def session_factory(self):
        if self._session_factory is not None:
            return self._session_factory
        factory = getattr(self.bind_state, "session_factory", None)
        if factory is None:
            raise RuntimeError(
                "rate limiter has no session factory: pass one explicitly or "
                "set app.state.session_factory before the first request"
            )
        return factory

    def _match_route(self, path: str) -> str | None:
        """Configured route governing this path — the TEMPLATE for templated
        routes (buckets key on templates, never raw paths) — else None."""
        if path in self._exact:
            return path
        for template in self._templates:
            if route_matches(template, path):
                return template
        return None

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        route = self._match_route(path)
        if route is None:
            await self.app(scope, receive, send)
            return

        allowed, retry_after = await self._check(
            route, scope.get("client"), scope.get("headers")
        )
        if not allowed:
            # Message stays byte-stable: existing suites pin the exact 429 body
            # ("too many attempts; slow down") as part of the auth-edge contract.
            body = json.dumps(error_body("RATE_LIMITED", "too many attempts; slow down"))
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(retry_after).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body.encode()})
            return

        await self.app(scope, receive, send)

    async def _maybe_gc(self, window_index: int) -> None:
        """M18-C5 (MAC2-F9): opportunistic bucket GC, throttled to one sweep
        per window per middleware instance.

        Piggybacks the request's limiter session exactly like mint sweeps
        expired ws_tickets (ephemeral.py). Deliberately OUTSIDE the try/except
        below: a GC failure must fail OPEN together with the limiter's own
        documented posture, never turn into a request-killing error path.
        """
        if window_index == self._gc_window_index:
            return
        self._gc_window_index = window_index
        try:
            removed = await sweep_expired_buckets(
                self.session_factory, window_seconds=self.window_seconds
            )
        except Exception:  # noqa: BLE001 — GC is best-effort; limiter law holds
            logger.warning("rate_buckets GC sweep failed; continuing", exc_info=True)
            return
        if removed:
            logger.info("rate_buckets GC swept %d expired bucket(s)", removed)

    async def _check(self, route_template: str, client, headers=None) -> tuple[bool, int]:
        """Consume one slot; return (allowed, retry_after_seconds).

        The effective budget is the per-route override for this TEMPLATE when
        one exists (G2 expensive ops), else the middleware-wide ``limit``.
        The keying identity is effective_client_ip() — normalized peer
        address plus XFF only through trusted proxies (M17-B4 / F8). Any
        store failure fails OPEN with a warning (see module docstring).
        """
        if isinstance(client, tuple) and client:
            peer_addr = str(client[0])
        else:
            peer_addr = "unknown"
        header_view = (
            {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in headers}
            if headers
            else {}
        )
        client_ip = effective_client_ip(header_view, peer_addr, self.trusted_proxy_cidrs)
        window_index, window_epoch = current_window(self.window_seconds)
        await self._maybe_gc(window_index)
        try:
            async with self.session_factory() as db:
                count = (
                    await db.execute(
                        _UPSERT,
                        {
                            "bucket_key": bucket_key(client_ip, route_template),
                            "window_start": datetime.fromtimestamp(window_epoch, tz=UTC),
                        },
                    )
                ).scalar_one()
                await db.commit()
        except Exception:  # noqa: BLE001 — fail open: availability > strictness
            logger.warning(
                "rate-limit store unavailable; failing OPEN for %s",
                route_template,
                exc_info=True,
            )
            return True, 0
        budget = _effective_limits(route_template, self.limit, self.route_limits)
        retry_after = max(1, self.window_seconds - int(time.time() - window_epoch))
        return int(count) <= budget, retry_after


def install_rate_limit(
    app,
    *,
    limit: int = DEFAULT_LIMIT,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    routes: tuple[str, ...] = DEFAULT_PROTECTED_ROUTES,
    route_limits: Mapping[str, int] | None = None,
    trusted_proxy_cidrs: Sequence[str] | None = None,
    session_factory=None,
):
    """Register the middleware stack entry and return the concrete class.

    Safe to call exactly once per app at factory time, BEFORE the middleware
    stack builds (i.e. before startup), same constraint as add_middleware.
    By default the counter store is ``app.state.session_factory`` (resolved
    lazily on the first limited request, so wiring order doesn't matter);
    pass ``session_factory=`` explicitly to pin a dedicated/broken store
    (tests do exactly that). ``route_limits`` overrides the shipped G2
    expensive-op budgets wholesale (keyed by ROUTE TEMPLATE).
    ``trusted_proxy_cidrs`` overrides the proxy-header trust policy
    wholesale (M17-B4 / F8); default is DEFAULT_TRUSTED_PROXY_CIDRS.
    """

    def _factory(inner):
        return RateLimitMiddleware(
            inner,
            session_factory,
            limit=limit,
            window_seconds=window_seconds,
            routes=routes,
            route_limits=route_limits,
            trusted_proxy_cidrs=trusted_proxy_cidrs,
            bind_state=app.state,
        )

    app.add_middleware(_factory)
    return RateLimitMiddleware
