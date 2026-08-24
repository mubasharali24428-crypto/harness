"""M10-C2 server side: team-scoped runs index — GET /api/v1/orchestrator/runs.

Contract (docs/briefs/mission10-graph.md, M10-C2, served to S7's runs page),
extended by M14-W4 F3a to frozen api-conventions.md cursor pagination::

    GET /api/v1/orchestrator/runs?projectId=&status=&limit=&cursor=
    -> {items: [{id, projectId, status, createdAt, startedAt, finishedAt}],
        nextCursor}

``nextCursor`` is an opaque base64 token (JSON ``{"c": created_at_iso,
"r": run_id}``) marking the LAST row of the page; pass it back as ``?cursor=``
to fetch the following page. A null ``nextCursor`` means end-of-collection.
The keyset predicate is ``(created_at, id) < (cursor.c, cursor.r)::record``
against the same deterministic ``ORDER BY created_at DESC, id DESC`` the
offset pager used, so pages are stable under inserts and never skip or repeat
rows.

DEPRECATION WINDOW (one release): the legacy ``?offset=`` parameter is still
ACCEPTED but IGNORED — callers must migrate to cursors before the next
release removes it. ``total`` was dropped from the payload: keyset paging has
no stable total without a second COUNT per request; consumers page until
``nextCursor`` is null (the frozen convention's documented shape).

Team scoping reuses the D5 ownership join direction (agent_runs.project_id ->
projects.team_id) from authz.py, but as a row FILTER instead of a guard: the
caller only ever sees runs whose project belongs to their team — foreign rows
are invisible, never a 403.

startedAt/finishedAt are derived from agent_events timestamps because the
frozen schema (0001_initial.sql) has no such columns on agent_runs:
started = first execution_started/resumed event, finished = first
engine_completed/engine_failed/pr_opened event.

SQL is fully parameterized (event-type lists via expanding bindparams; filter
values never interpolated — only static SQL fragments are composed). limit
clamps to <= 100 (default 25). An undecodable/garbage cursor answers 400
INVALID_CURSOR rather than silently restarting from the top. Without a wired
session factory (bare unit mode) the route degrades to an empty page,
mirroring rehydration.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from harness_orchestrator.api import RequirePrincipal, get_orchestrator_service

router = APIRouter(prefix="/api/v1/orchestrator", tags=["orchestrator"])

_LIMIT_MAX = 100
_LIMIT_DEFAULT = 25

# Lifecycle markers read out of agent_events (frozen EventType vocabulary).
_STARTED_EVENT_TYPES = ("execution_started", "resumed")
_FINISHED_EVENT_TYPES = ("engine_completed", "engine_failed", "pr_opened")

_SELECT_TEMPLATE = """
    SELECT r.id AS id,
           r.project_id AS project_id,
           r.state AS state,
           r.created_at AS created_at,
           (SELECT MIN(e.ts) FROM agent_events e
             WHERE e.run_id = r.id AND e.type IN :started_types) AS started_at,
           (SELECT MIN(e.ts) FROM agent_events e
             WHERE e.run_id = r.id AND e.type IN :finished_types) AS finished_at
    FROM agent_runs r
    JOIN projects p ON p.id = r.project_id
    WHERE {where}
    ORDER BY r.created_at DESC, r.id DESC
    LIMIT :limit
"""

# Appended after the team/filters when a cursor is present. Row-value
# comparison keeps the DESC ordering consistent on (created_at, id) ties;
# parameterized throughout (never interpolated client data).
_CURSOR_PREDICATE = "(r.created_at, r.id) < (:cursor_created, :cursor_run)"


def _encode_cursor(created_at: datetime | None, run_id: str) -> str:
    """Opaque next-page token: base64url(JSON {c, r})."""
    raw = json.dumps(
        {"c": created_at.isoformat() if created_at else None, "r": run_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[datetime | None, str]:
    """Inverse of :func:`_encode_cursor`; raises ValueError on garbage."""
    try:
        blob = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError) as e:
        raise ValueError("undecodable cursor") from e
    if not isinstance(blob, dict) or "r" not in blob:
        raise ValueError("cursor missing fields")
    raw_ts = blob.get("c")
    ts = None
    if raw_ts is not None:
        try:
            ts = datetime.fromisoformat(str(raw_ts))
        except ValueError as e:
            raise ValueError("bad cursor timestamp") from e
    return ts, str(blob["r"])


def _build_queries(project_id: str | None, run_state: str | None, cursor: bool):
    """Compose the static WHERE fragment + bind expanding event-type lists."""
    conditions = ["p.team_id = :team_id"]
    if project_id:
        conditions.append("r.project_id = :project_id")
    if run_state:
        conditions.append("r.state = :run_state")
    if cursor:
        conditions.append(_CURSOR_PREDICATE)
    where = " AND ".join(conditions)

    def _bound(sql_template: str):
        # Bind each expanding list only where that statement actually
        # references it (the COUNT has no timestamp subqueries).
        stmt = text(sql_template.format(where=where))
        for name, values in (
            ("started_types", _STARTED_EVENT_TYPES),
            ("finished_types", _FINISHED_EVENT_TYPES),
        ):
            if f":{name}" in sql_template:
                stmt = stmt.bindparams(bindparam(name, values, expanding=True))
        return stmt

    return _bound(_SELECT_TEMPLATE)


class RunItem(BaseModel):
    """One row of the M10-C2 index payload (camelCase per api-conventions)."""

    id: str
    projectId: str
    status: str
    createdAt: datetime | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None


class RunsPage(BaseModel):
    """Frozen api-conventions pagination shape: items + opaque nextCursor."""

    items: list[RunItem]
    nextCursor: str | None = None


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, _LIMIT_MAX))


@router.get("/runs", response_model=RunsPage)
async def list_runs(
    principal: RequirePrincipal,
    projectId: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=_LIMIT_DEFAULT),
    offset: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
) -> RunsPage:
    """List the caller's team's runs, newest first (contract M10-C2).

    Cursor pagination per api-conventions.md: pass ``nextCursor`` back as
    ``?cursor=`` for the next page. ``?offset=`` is accepted but IGNORED
    during the one-release deprecation window (M14-W4 F3a).
    """
    del offset  # deprecated one-release window: accepted, never applied
    page_limit = _clamp_limit(limit)
    service = get_orchestrator_service()
    factory = getattr(service, "session_factory", None)
    if factory is None:  # bare unit mode: no DB wired, nothing to list
        return RunsPage(items=[], nextCursor=None)

    cursor_created: datetime | None = None
    cursor_run: str | None = None
    if cursor is not None:
        try:
            cursor_created, cursor_run = _decode_cursor(cursor)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "INVALID_CURSOR", "message": str(e)}},
            ) from e

    params: dict[str, Any] = {
        "team_id": principal.teamId,
        "project_id": projectId,
        "run_state": status_filter,
        "limit": page_limit + 1,  # peek one past the page for nextCursor
    }
    if cursor_created is not None or cursor_run is not None:
        params["cursor_created"] = cursor_created
        params["cursor_run"] = cursor_run
    runs_sql = _build_queries(projectId, status_filter, cursor=cursor is not None)
    async with factory() as db:
        rows = (await db.execute(runs_sql, params)).mappings().all()

    has_next = len(rows) > page_limit
    rows = rows[:page_limit]
    items = [
        RunItem(
            id=row["id"],
            projectId=row["project_id"],
            status=row["state"],
            createdAt=row["created_at"],
            startedAt=row["started_at"],
            finishedAt=row["finished_at"],
        )
        for row in rows
    ]
    next_cursor = (
        _encode_cursor(items[-1].createdAt, items[-1].id) if has_next and items else None
    )
    return RunsPage(items=items, nextCursor=next_cursor)
