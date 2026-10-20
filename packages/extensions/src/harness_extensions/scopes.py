"""Deny-by-default authorization for external-tool calls.

A grant is the ONLY thing that makes a discovered external tool callable, and
grants are narrow by construction: team + server + tool (or ``*``), optionally
bound to one project and/or one run, optionally expiring. Wildcard grants do
not bypass the per-call checks — they are just less narrow, and the decision
records which grant matched.

Expiry is fail-closed: an unparsable timestamp counts as expired.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import ServerRecord, ToolGrant
from .store import Store


class ScopeDenied(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class Authorization:
    grant: ToolGrant
    exact: bool  # exact tool match vs wildcard


def _parse_expiry(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=UTC)  # fail closed: treat as expired
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def authorize_tool_call(
    store: Store,
    *,
    team_id: str,
    server: ServerRecord,
    tool_name: str,
    run_id: str | None = None,
    project_id: str | None = None,
    project_owner_lookup: Callable[[str], str | None] | None = None,
    now: datetime | None = None,
) -> Authorization:
    """Authorize one external tool call or raise :class:`ScopeDenied`."""
    now = now or datetime.now(UTC)

    if server.teamId != team_id:
        # Callers normally resolve servers team-scoped first; this is defense
        # in depth, and it must not leak existence (no-oracle).
        raise ScopeDenied("SERVER_NOT_FOUND", "no such MCP server")

    if server.status != "enabled":
        raise ScopeDenied("SERVER_DISABLED", f"server is {server.status}")

    if project_id is not None:
        if project_owner_lookup is None:
            # Safety-critical dependency unavailable: fail closed rather than
            # trusting a caller-supplied project id.
            raise ScopeDenied(
                "PROJECT_CHECK_UNAVAILABLE",
                "project ownership verification is not configured on this service",
            )
        owner = project_owner_lookup(project_id)
        if owner is None:
            raise ScopeDenied("PROJECT_NOT_FOUND", "no such project")
        if owner != team_id:
            raise ScopeDenied("PROJECT_NOT_FOUND", "no such project")

    grants = store.find_active_grants(team_id, server.serverId, tool_name)
    if not grants:
        raise ScopeDenied("SCOPE_DENIED", f"no active grant for tool {tool_name!r}")

    saw_expired = False
    eligible: list[Authorization] = []
    for grant in grants:
        expiry = _parse_expiry(grant.expiresAt)
        if expiry is not None and expiry <= now:
            saw_expired = True
            continue
        if grant.runId is not None and grant.runId != run_id:
            continue
        if grant.projectId is not None and grant.projectId != project_id:
            continue
        eligible.append(Authorization(grant=grant, exact=grant.toolName == tool_name))

    if not eligible:
        if saw_expired:
            raise ScopeDenied("GRANT_EXPIRED", f"grant for tool {tool_name!r} has expired")
        raise ScopeDenied(
            "SCOPE_DENIED",
            f"grants for tool {tool_name!r} do not cover this run/project context",
        )
    # Prefer exact-tool grants over wildcards; newest first within a class.
    eligible.sort(key=lambda a: a.grant.createdAt, reverse=True)
    eligible.sort(key=lambda a: not a.exact)
    return eligible[0]
