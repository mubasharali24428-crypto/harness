"""Project ownership guard for Section 11 routes (fail-closed).

Every context route that touches a project's index must prove
``projects.team_id`` matches the caller's team before doing any work.
Mirrors Section 4's M18-C1 discipline (authz.py there) for the
context-pillar surface:

* unwired/unqueryable store → DENY (``AuthzUnavailableError``, 403
  ``AUTHZ_UNAVAILABLE``) — an unverifiable check is never an allow;
* no row for the project OR a row owned by another team → the SAME typed
  denial (``ProjectAccessDenied``) so the API can answer one no-oracle 404
  for both cases: a foreign team can never probe which project ids exist;
* the check reads only the frozen ``projects`` table via parameterized SQL —
  no other section's tables are touched.

Deliberately absent: Section 4's ``HARNESS_AUTHZ_TEST_MODE`` escape hatch.
Section 11's bare-router tests fail closed at authentication (401) before
authorization is reached, so no allow-without-proof path is needed here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from harness_contracts.roles import Principal
    from sqlalchemy.ext.asyncio import async_sessionmaker

#: Stable machine reason surfaced in the 403 envelope when the check itself
#: cannot run. Kept identical to Section 4's AUTHZ_UNAVAILABLE vocabulary.
REASON_UNWIRED = "unwired_session_factory"
REASON_LOOKUP_FAILED = "project_lookup_failed"


class ProjectAccessDenied(Exception):
    """The project does not exist OR belongs to another team.

    ``reason`` distinguishes the two for LOGS only (``not_found`` /
    ``foreign_team``); the API must map both to one indistinguishable 404.
    """

    def __init__(self, project_id: str, reason: str) -> None:
        self.project_id = project_id
        self.reason = reason
        super().__init__(f"project '{project_id}' not accessible ({reason})")


class AuthzUnavailableError(Exception):
    """Ownership could not be evaluated, so the request is DENIED."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"ownership check unavailable ({reason}): {detail}")


async def authorize_project(
    session_factory: async_sessionmaker | None,
    principal: Principal,
    project_id: str,
) -> None:
    """Raise unless ``project_id`` is owned by ``principal.teamId``.

    Returns None on success. All failure modes are typed exceptions; callers
    (api.py) translate them into no-oracle 404 / 403 envelopes.
    """
    if session_factory is None:
        raise AuthzUnavailableError(
            REASON_UNWIRED,
            f"no session factory wired while authorizing project '{project_id}'",
        )

    try:
        async with session_factory() as db:
            row = (
                await db.execute(
                    text("SELECT team_id FROM projects WHERE id = :p"),
                    {"p": project_id},
                )
            ).first()
    except Exception as exc:  # noqa: BLE001 — store trouble must never ALLOW
        raise AuthzUnavailableError(
            REASON_LOOKUP_FAILED,
            f"projects lookup failed for '{project_id}': {type(exc).__name__}",
        ) from exc

    if row is None:
        raise ProjectAccessDenied(project_id, "not_found")
    if row.team_id != principal.teamId:
        raise ProjectAccessDenied(project_id, "foreign_team")
