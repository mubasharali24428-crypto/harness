"""Mission 10 node B5: Postgres fixed-window rate limiting for the auth edge.

M12-W2.2 lane G2 (H-021): guards the credential-spray surface (OTP request +
verify), the invite-mint write, AND the platform's expensive ops — orchestrator
run-start (spawn cost), model-call (S3 spend), brief-generate (LLM work).

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
"""

import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime

from harness_contracts import error_body
from sqlalchemy import text

__all__ = [
    "DEFAULT_PROTECTED_ROUTES",
    "EXPENSIVE_OP_LIMITS",
    "INVITE_MINT_TEMPLATE",
    "MODEL_CALL_LIMIT",
    "MODEL_CALL_TEMPLATE",
    "RUN_START_LIMIT",
    "RUN_START_TEMPLATE",
    "BRIEF_GENERATE_LIMIT",
    "BRIEF_GENERATE_TEMPLATE",
    "RateLimitMiddleware",
    "expensive_op_limits",
    "install_rate_limit",
    "limit_for_route",
    "route_matches",
]

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 10
DEFAULT_WINDOW_SECONDS = 60

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


def bucket_key(client_ip: str, route_template: str) -> str:
    """Limit key: client IP + route TEMPLATE (not the raw path), capped at the
    column width so oversized keys fail safe instead of erroring."""
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
        bind_state=None,  # noqa: ANN001 — app.state for lazy factory lookup
    ) -> None:  # noqa: ANN001 — pure ASGI signature
        self.app = app
        self._session_factory = session_factory
        self.bind_state = bind_state
        self.limit = limit
        self.window_seconds = window_seconds
        self.routes = tuple(routes)
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

        allowed, retry_after = await self._check(route, scope.get("client"))
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

    async def _check(self, route_template: str, client) -> tuple[bool, int]:
        """Consume one slot; return (allowed, retry_after_seconds).

        The effective budget is the per-route override for this TEMPLATE when
        one exists (G2 expensive ops), else the middleware-wide ``limit``.
        Any store failure fails OPEN with a warning (see module docstring).
        """
        client_ip = str(client[0]) if isinstance(client, tuple) and client else "unknown"
        _, window_epoch = current_window(self.window_seconds)
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
    """

    def _factory(inner):
        return RateLimitMiddleware(
            inner,
            session_factory,
            limit=limit,
            window_seconds=window_seconds,
            routes=routes,
            route_limits=route_limits,
            bind_state=app.state,
        )

    app.add_middleware(_factory)
    return RateLimitMiddleware
