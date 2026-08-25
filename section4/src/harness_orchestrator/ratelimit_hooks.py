"""M12-W2.2 lane G2 — thin rate-limit wiring hook for the orchestrator app.

H-021 rebalance: POST /api/v1/orchestrator/runs/{runId}/start spawns agents,
so it joins the DB-backed fixed-window limiter (per-IP + route TEMPLATE,
generous 30/min budget — an abuse guard, not a UX blocker).

THIN BY DESIGN: every mechanic lives in ``harness_contracts.ratelimit``
(M17-B3: promoted from section1's ratelimit.py so sections stop importing
each other's packages). This module only resolves the session factory and
installs the middleware at factory time:

    app = simple_app(router, "harness-orchestrator")
    install_orchestrator_rate_limit(app)          # store = app.state.session_factory

Wiring point: ATLAS merge in deploy/runners/services.py app_orchestrator() /
section4 app factories. Storage is the shared ``rate_buckets`` table
(section1/migrations/0011). DB failure FAILS OPEN with a warning — availability
parity with the auth edge; never let the limiter take the API down.
"""

from __future__ import annotations

from fastapi import FastAPI
from harness_contracts.ratelimit import (
    RUN_START_LIMIT,
    RUN_START_TEMPLATE,
    DEFAULT_PROTECTED_ROUTES,
    install_rate_limit,
)

__all__ = ["ORCHESTRATOR_ROUTE_LIMITS", "install_orchestrator_rate_limit"]

#: Budgets this service enforces on top of the shipped default set.
ORCHESTRATOR_ROUTE_LIMITS = {RUN_START_TEMPLATE: RUN_START_LIMIT}


def install_orchestrator_rate_limit(
    app: FastAPI,
    *,
    session_factory=None,
    routes=None,
    route_limits=None,
) -> None:
    """Install the DB-backed limiter on the orchestrator app.

    ``session_factory`` pins the counter store explicitly; omit it to resolve
    ``app.state.session_factory`` lazily on first limited request (deploy
    wiring sets that attribute, tests pass their own factory). Any store
    failure fails OPEN with a logged warning — see harness_contracts.ratelimit.
    """
    install_rate_limit(
        app,
        routes=routes if routes is not None else tuple(DEFAULT_PROTECTED_ROUTES),
        route_limits=(
            route_limits if route_limits is not None else dict(ORCHESTRATOR_ROUTE_LIMITS)
        ),
        session_factory=session_factory,
    )
