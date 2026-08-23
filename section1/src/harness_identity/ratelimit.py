"""Mission 10 node B5: Postgres fixed-window rate limiting for the auth edge.

Guards the credential-spray surface (login + OTP request) with per-IP+route
fixed-window counters persisted in ``rate_buckets`` (migration 0011). The
counter lives in Postgres, not process memory, so the limit holds across
uvicorn workers and restarts — in-memory buckets reset on every deploy and
are invisible to sibling workers.

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
from datetime import UTC, datetime

from harness_contracts import error_body
from sqlalchemy import text

__all__ = ["RateLimitMiddleware", "install_rate_limit"]

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 10
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_ROUTES = ("/api/v1/auth/login", "/api/v1/auth/otp/request")

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


class RateLimitMiddleware:
    """Wrap the ASGI callable directly; enforce limits on configured routes."""

    def __init__(
        self,
        app,
        session_factory=None,
        *,
        limit: int = DEFAULT_LIMIT,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        routes: tuple[str, ...] = DEFAULT_ROUTES,
        bind_state=None,  # noqa: ANN001 — app.state for lazy factory lookup
    ) -> None:  # noqa: ANN001 — pure ASGI signature
        self.app = app
        self._session_factory = session_factory
        self.bind_state = bind_state
        self.limit = limit
        self.window_seconds = window_seconds
        self.routes = frozenset(routes)

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

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path not in self.routes:
            await self.app(scope, receive, send)
            return

        allowed, retry_after = await self._check(path, scope.get("client"))
        if not allowed:
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
        retry_after = max(1, self.window_seconds - int(time.time() - window_epoch))
        return int(count) <= self.limit, retry_after


def install_rate_limit(
    app,
    *,
    limit: int = DEFAULT_LIMIT,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    routes: tuple[str, ...] = DEFAULT_ROUTES,
    session_factory=None,
):
    """Register the middleware stack entry and return the concrete class.

    Safe to call exactly once per app at factory time, BEFORE the middleware
    stack builds (i.e. before startup), same constraint as add_middleware.
    By default the counter store is ``app.state.session_factory`` (resolved
    lazily on the first limited request, so wiring order doesn't matter);
    pass ``session_factory=`` explicitly to pin a dedicated/broken store
    (tests do exactly that).
    """

    def _factory(inner):
        return RateLimitMiddleware(
            inner,
            session_factory,
            limit=limit,
            window_seconds=window_seconds,
            routes=routes,
            bind_state=app.state,
        )

    app.add_middleware(_factory)
    return RateLimitMiddleware
