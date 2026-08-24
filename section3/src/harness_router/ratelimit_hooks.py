"""M12-W2.2 lane G2 — thin rate-limit wiring hook for the model-router app.

H-021 rebalance: POST /api/v1/models/call spends real provider budget (S3
spend per request), so it joins the DB-backed fixed-window limiter (per-IP +
route TEMPLATE, generous 60/min budget — an abuse guard, not a UX blocker).

THIN BY DESIGN: every mechanic lives in section1's ``harness_identity
.ratelimit`` (single upsert statement, fail-open policy, Retry-After). This
module only installs the middleware at factory time:

    app = simple_app(router, "harness-router")
    install_router_rate_limit(app)                # store = app.state.session_factory

Wiring point: ATLAS merge in deploy/runners/services.py app_models() /
section3 app factories. Storage is the shared ``rate_buckets`` table
(section1/migrations/0011); set ``app.state.session_factory`` before the first
limited request or pass ``session_factory=`` explicitly. DB failure FAILS OPEN
with a warning — availability parity with the auth edge.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI

# Shared limiter lives in section1; this venv does not carry harness_identity
# as an installed distribution, so the src tree is appended exactly like the
# M12-G3 limits seam (envelope_middleware consumers) already do at import time.
_ROOT = Path(__file__).resolve().parents[3]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

from harness_identity.ratelimit import (  # noqa: E402
    MODEL_CALL_LIMIT,
    MODEL_CALL_TEMPLATE,
    install_rate_limit,
)

__all__ = ["ROUTER_ROUTE_LIMITS", "install_router_rate_limit"]

#: Budgets this service enforces on top of the shipped default set.
ROUTER_ROUTE_LIMITS = {MODEL_CALL_TEMPLATE: MODEL_CALL_LIMIT}


def install_router_rate_limit(
    app: FastAPI,
    *,
    session_factory=None,
    routes=None,
    route_limits=None,
) -> None:
    """Install the DB-backed limiter on the model-router app.

    ``session_factory`` pins the counter store explicitly; omit it to resolve
    ``app.state.session_factory`` lazily on first limited request. Any store
    failure fails OPEN with a logged warning — see ratelimit.py docstring.
    """
    install_rate_limit(
        app,
        routes=routes if routes is not None else _default_routes(),
        route_limits=(
            route_limits if route_limits is not None else dict(ROUTER_ROUTE_LIMITS)
        ),
        session_factory=session_factory,
    )


def _default_routes():
    from harness_identity.ratelimit import DEFAULT_PROTECTED_ROUTES

    return DEFAULT_PROTECTED_ROUTES
