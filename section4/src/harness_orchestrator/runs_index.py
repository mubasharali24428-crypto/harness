"""M10-C2 server side: team-scoped runs index — GET /api/v1/orchestrator/runs.

Contract (docs/briefs/mission10-graph.md, M10-C2, served to S7's runs page)::

    GET /api/v1/orchestrator/runs?projectId=&status=&limit=&offset=
    -> {items: [{id, projectId, status, createdAt, startedAt, finishedAt}],
        total, limit, offset}

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
clamps to <= 100 (default 25), offset floors at 0, so a bad client gets a sane
page instead of an error or the whole table. Without a wired session factory
(bare unit mode) the route degrades to an empty page, mirroring rehydration.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
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
    LIMIT :limit OFFSET :offset
"""

_COUNT_TEMPLATE = """
    SELECT COUNT(*) AS total
    FROM agent_runs r
    JOIN projects p ON p.id = r.project_id
    WHERE {where}
"""


def _build_queries(project_id: str | None, run_state: str | None):
    """Compose the static WHERE fragment + bind expanding event-type lists."""
    conditions = ["p.team_id = :team_id"]
    if project_id:
        conditions.append("r.project_id = :project_id")
    if run_state:
        conditions.append("r.state = :run_state")
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

    return _bound(_SELECT_TEMPLATE), _bound(_COUNT_TEMPLATE)


class RunItem(BaseModel):
    """One row of the M10-C2 index payload (camelCase per api-conventions)."""

    id: str
    projectId: str
    status: str
    createdAt: datetime | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None


class RunsPage(BaseModel):
    items: list[RunItem]
    total: int
    limit: int
    offset: int


def _clamp_paging(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, _LIMIT_MAX)), max(0, offset)


@router.get("/runs", response_model=RunsPage)
async def list_runs(
    principal: RequirePrincipal,
    projectId: str | None = None,
    status: str | None = None,
    limit: int = Query(default=_LIMIT_DEFAULT),
    offset: int = Query(default=0),
) -> RunsPage:
    """List the caller's team's runs, newest first (contract M10-C2)."""
    page_limit, page_offset = _clamp_paging(limit, offset)
    service = get_orchestrator_service()
    factory = getattr(service, "session_factory", None)
    if factory is None:  # bare unit mode: no DB wired, nothing to list
        return RunsPage(items=[], total=0, limit=page_limit, offset=page_offset)

    params: dict[str, Any] = {
        "team_id": principal.teamId,
        "project_id": projectId,
        "run_state": status,
        "limit": page_limit,
        "offset": page_offset,
    }
    runs_sql, count_sql = _build_queries(projectId, status)
    async with factory() as db:
        total = int((await db.execute(count_sql, params)).scalar_one())
        rows = (await db.execute(runs_sql, params)).mappings().all()

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
    return RunsPage(items=items, total=total, limit=page_limit, offset=page_offset)
