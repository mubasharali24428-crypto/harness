"""M12-W2.2 lane G2 — thin rate-limit wiring hook for the meeting app.

H-021 rebalance: POST /api/v1/meetings/{meetingId}/brief does LLM work, so it
joins the DB-backed fixed-window limiter (per-IP + route TEMPLATE, generous
20/min budget — an abuse guard, not a UX blocker).

THIN BY DESIGN: every mechanic lives in ``harness_contracts.ratelimit``
(M17-B3: promoted from section1's ratelimit.py so sections stop importing
each other's packages). This module only installs the middleware at factory
time:

    app = simple_app(router, "harness-meeting")
    install_meeting_rate_limit(app)               # store = app.state.session_factory

Wiring point: ATLAS merge in deploy/runners/services.py app_meeting() /
section6 app factories. Storage is the shared ``rate_buckets`` table
(section1/migrations/0011); set ``app.state.session_factory`` before the first
limited request or pass ``session_factory=`` explicitly. DB failure FAILS OPEN
with a warning — availability parity with the auth edge.
"""

from __future__ import annotations

from fastapi import FastAPI
from harness_contracts.ratelimit import (
    BRIEF_GENERATE_LIMIT,
    BRIEF_GENERATE_TEMPLATE,
    DEFAULT_PROTECTED_ROUTES,
    install_rate_limit,
)

__all__ = ["MEETING_ROUTE_LIMITS", "install_meeting_rate_limit"]

#: Budgets this service enforces on top of the shipped default set.
MEETING_ROUTE_LIMITS = {BRIEF_GENERATE_TEMPLATE: BRIEF_GENERATE_LIMIT}


def install_meeting_rate_limit(
    app: FastAPI,
    *,
    session_factory=None,
    routes=None,
    route_limits=None,
) -> None:
    """Install the DB-backed limiter on the meeting app.

    ``session_factory`` pins the counter store explicitly; omit it to resolve
    ``app.state.session_factory`` lazily on first limited request. Any store
    failure fails OPEN with a logged warning — see harness_contracts.ratelimit.
    """
    install_rate_limit(
        app,
        routes=routes if routes is not None else tuple(DEFAULT_PROTECTED_ROUTES),
        route_limits=(
            route_limits if route_limits is not None else dict(MEETING_ROUTE_LIMITS)
        ),
        session_factory=session_factory,
    )
