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

M16-X1 (runs-index tuning at 100k events): the two per-row ``(SELECT
MIN(ts) ...)`` probes were an N+1 — 2 x LIMIT correlated probes against
agent_events on every request. The page is now fetched WITHOUT lifecycle
columns and the extremes are resolved by ONE batched aggregate per marker
family over exactly the page's run ids::

    SELECT e.run_id, MIN(e.ts) FROM agent_events e
    WHERE e.run_id IN (:...page_run_ids) AND e.type IN (:...markers)
    GROUP BY e.run_id

2 x LIMIT probes -> 2 statements per request regardless of page size, and
migration 0026's idx_agent_events_run_type_ts_covering (run_id, type, ts)
makes each grouped MIN an index-only range scan while
idx_agent_runs_team_created_id turns the page itself into an ordered index
range (no sort of the team's whole history).

SQL is fully parameterized (event-type lists via expanding bindparams;
filter values never interpolated — only static SQL fragments are composed).
limit clamps to <= 100 (default 25). An undecodable/garbage cursor answers
400 INVALID_CURSOR rather than silently restarting from the top. Without a
wired session factory (bare unit mode) the route degrades to an empty page,
mirroring rehydration.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import String, bindparam, text

from harness_orchestrator.api import RequirePrincipal, get_orchestrator_service

router = APIRouter(prefix="/api/v1/orchestrator", tags=["orchestrator"])

_LIMIT_MAX = 100
_LIMIT_DEFAULT = 25

# Lifecycle markers read out of agent_events (frozen EventType vocabulary).
_START_EVENT_TYPES = ("execution_started", "resumed")
_FINISHED_EVENT_TYPES = ("engine_completed", "engine_failed", "pr_opened")

# M16-X1: page fetch WITHOUT the per-row lifecycle probes. Ordering +
# keyset predicate are unchanged (M14-W4 F3a contract); migration 0026's
# idx_agent_runs_team_created_id makes this an ordered index range.
_PAGE_TEMPLATE = """
    SELECT r.id AS id,
           r.project_id AS project_id,
           r.state AS state,
           r.created_at AS created_at
    FROM agent_runs r
    JOIN projects p ON p.id = r.project_id
    WHERE {where}
    ORDER BY r.created_at DESC, r.id DESC
    LIMIT :limit
"""

# ONE batched aggregate per marker family over exactly the page's runs —
# replaces the old 2 x LIMIT correlated MIN(ts) probes (the N+1). Covered
# end-to-end by idx_agent_events_run_type_ts_covering (run_id, type, ts):
# each grouped MIN is an index-only range scan, no heap visits. Runs on
# both Postgres and SQLite unchanged; an empty page skips it entirely.
_LIFECYCLE_TEMPLATE = """
    SELECT e.run_id AS run_id, MIN(e.ts) AS first_ts
    FROM agent_events e
    WHERE e.run_id IN :page_run_ids AND e.type IN :marker_types
    GROUP BY e.run_id
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
    """Compose the page statement's static WHERE fragment + expanding binds."""
    conditions = ["p.team_id = :team_id"]
    if project_id:
        conditions.append("r.project_id = :project_id")
    if run_state:
        conditions.append("r.state = :run_state")
    if cursor:
        conditions.append(_CURSOR_PREDICATE)
    where = " AND ".join(conditions)
    return text(_PAGE_TEMPLATE.format(where=where))


def _lifecycle_stmt(marker_types: tuple[str, ...]):
    """One batched MIN(ts) aggregate per marker family (M16-X1).

    Both lists bind as typed expanding params — the run ids are data (page
    rows), the marker types a frozen module constant baked in as the bind
    default; nothing is ever interpolated. Typed String so plan probes can
    compile with ``literal_binds`` on both dialects.
    """
    return text(_LIFECYCLE_TEMPLATE).bindparams(
        bindparam("page_run_ids", type_=String, expanding=True),
        bindparam("marker_types", type_=String, expanding=True, value=list(marker_types)),
    )


# Marker families are frozen vocabulary: build each statement ONCE so every
# request reuses the compiled form instead of paying a rebuild per call.
_LIFECYCLE_STATEMENTS: dict[str, Any] = {
    "started_at": _lifecycle_stmt(_START_EVENT_TYPES),
    "finished_at": _lifecycle_stmt(_FINISHED_EVENT_TYPES),
}


async def _fetch_lifecycle_extremes(db, run_ids: list[str]) -> dict[str, dict[str, Any]]:
    """first started/finished event ts per page run id — 2 statements total."""
    out: dict[str, dict[str, Any]] = {
        run_id: {"started_at": None, "finished_at": None} for run_id in run_ids
    }
    if not run_ids:
        return out
    for key, stmt in _LIFECYCLE_STATEMENTS.items():
        rows = await db.execute(stmt, {"page_run_ids": run_ids})
        for run_id, first_ts in rows:
            if run_id in out:
                out[run_id][key] = first_ts
    return out


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
    async with factory() as db:
        extremes = await _fetch_lifecycle_extremes(
            db, [row["id"] for row in rows]
        )
    items = [
        RunItem(
            id=row["id"],
            projectId=row["project_id"],
            status=row["state"],
            createdAt=row["created_at"],
            startedAt=extremes.get(row["id"], {}).get("started_at"),
            finishedAt=extremes.get(row["id"], {}).get("finished_at"),
        )
        for row in rows
    ]
    next_cursor = (
        _encode_cursor(items[-1].createdAt, items[-1].id) if has_next and items else None
    )
    return RunsPage(items=items, nextCursor=next_cursor)
