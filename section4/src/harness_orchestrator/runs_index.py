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
# A ?status= filter is applied to the REPLAYED state (below), so it cannot be a WHERE clause:
# the page is filled by scanning batches. Bounded so one request stays cheap; when the cap is
# reached the response still carries a nextCursor and the client keeps paging.
_FILTER_SCAN_BATCH = 100
_FILTER_SCAN_MAX_BATCHES = 20

# 2026-11-13: agent_runs.state is written when the row is created and never per transition
# (service.py `_settled_run_ids` says so), so it read "idle" for runs waiting on plan approval.
# The list now replays the event log the same way OrchestratorService._rehydrate does.
_STATE_EVENT_TYPES = (
    "plan_proposed",
    "plan_approved",
    "plan.rejected",
    "execution_started",
    "paused",
    "resumed",
    "blocked_awaiting_clarification",
    "engine_completed",
    "engine_failed",
    "done_signal_received",
    "review_passed",
    "review_failed",
    "pr_opened",
)
_SIMPLE_STATE = {
    "plan_proposed": "awaiting_plan_approval",
    "plan.rejected": "idle",
    "execution_started": "executing",
    "paused": "paused",
    "resumed": "executing",
    "blocked_awaiting_clarification": "blocked",
    "engine_failed": "blocked",
    "review_failed": "blocked",
    "pr_opened": "pr_opened",
}
_TERMINAL = {"done"}


def _payload(raw: Any) -> dict:
    if isinstance(raw, str):  # SQLite returns JSON columns as text
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


_BLOCKED_BY = {
    "blocked_awaiting_clarification": "question",
    "engine_failed": "engine_failed",
    "review_failed": "review_failed",
}


def replay_run(events: list[tuple[str, Any]], stored: str) -> tuple[str, bool, str | None]:
    """(state, plan approved, what blocked it) from a run's ordered events.

    The state machine keeps AWAITING_PLAN_APPROVAL after approve_plan (it only sets a flag),
    so the list reports the flag separately: a run that is approved but not started needs a
    person to START it, not to approve it. ``blocked_by`` names the last event that blocked
    the run (question / engine_failed / review_failed) while it is blocked, else None.
    """
    state = replay_state(events, stored)
    approved = False
    blocked_by: str | None = None
    for etype, _raw in events:
        if etype == "plan_approved":
            approved = True
        elif etype in ("plan_proposed", "plan.rejected"):
            approved = False
        if etype in _BLOCKED_BY:
            blocked_by = _BLOCKED_BY[etype]
    return state, approved, (blocked_by if state == "blocked" else None)


def replay_state(events: list[tuple[str, Any]], stored: str) -> str:
    """Lifecycle state from a run's ordered (type, payload) events; ``stored`` when none apply.

    Mirrors OrchestratorService._rehydrate: a stop tombstone (PAUSED with reason
    user_requested_stop or stopped=true) is DONE; ENGINE_COMPLETED only counts from
    EXECUTING and opens the done gate; REVIEW_PASSED behind an open gate is DONE, else
    EXECUTING; nothing moves a run once it is DONE. One deliberate difference: plan.rejected
    returns the run to IDLE (the state machine's reject_plan), which _rehydrate does not map.
    """
    state: str | None = None
    done_gate = False
    for etype, raw in events:
        if state in _TERMINAL:
            break
        if etype == "paused":
            p = _payload(raw)
            if p.get("reason") == "user_requested_stop" or p.get("stopped") is True:
                state = "done"
                continue
        if etype == "engine_completed":
            if state == "executing":
                state, done_gate = "awaiting_done_signal", True
            continue
        if etype == "done_signal_received":
            if state == "awaiting_done_signal":
                done_gate = True
            state = "reviewing"
            continue
        if etype == "review_passed":
            state, done_gate = ("done" if done_gate else "executing"), False
            continue
        if etype in _SIMPLE_STATE:
            state = _SIMPLE_STATE[etype]
    return state or stored


_STATE_EVENTS_SQL = text(
    """
    SELECT e.run_id AS run_id, e.type AS type, e.payload AS payload
    FROM agent_events e
    WHERE e.run_id IN :page_run_ids AND e.type IN :state_types
    ORDER BY e.run_id, e.seq
    """
).bindparams(
    bindparam("page_run_ids", type_=String, expanding=True),
    bindparam("state_types", type_=String, expanding=True, value=list(_STATE_EVENT_TYPES)),
)


async def _replayed_states(db, rows) -> dict[str, tuple[str, bool, str | None]]:
    """One batched read of the page's state-bearing events, replayed per run."""
    ids = [row["id"] for row in rows]
    stored = {row["id"]: row["state"] for row in rows}
    if not ids:
        return {}
    events: dict[str, list[tuple[str, Any]]] = {i: [] for i in ids}
    for run_id, etype, payload in await db.execute(_STATE_EVENTS_SQL, {"page_run_ids": ids}):
        events[run_id].append((etype, payload))
    return {i: replay_run(events[i], stored[i]) for i in ids}

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


def _build_queries(project_id: str | None, cursor: bool):
    """Compose the page statement's static WHERE fragment + expanding binds."""
    conditions = ["p.team_id = :team_id"]
    if project_id:
        conditions.append("r.project_id = :project_id")
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
    # 2026-11-13: true once the current plan is approved (status stays awaiting_plan_approval
    # until the run is started); blockedBy says why a blocked run is blocked.
    planApproved: bool = False
    blockedBy: str | None = None
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
    }
    # Keyset scan. Without a filter this is one batch of limit+1, exactly as before. With a
    # filter, batches continue until the page is full, the runs run out, or the scan cap is hit.
    batch = page_limit + 1 if status_filter is None else _FILTER_SCAN_BATCH
    max_batches = 1 if status_filter is None else _FILTER_SCAN_MAX_BATCHES
    picked: list[tuple[Any, tuple[str, bool, str | None]]] = []  # (row, replayed)
    last_scanned = None
    exhausted = False
    after = (cursor_created, cursor_run) if cursor is not None else None
    async with factory() as db:
        for _ in range(max_batches):
            q = dict(params, limit=batch)
            if after is not None:
                q["cursor_created"], q["cursor_run"] = after
            sql = _build_queries(projectId, cursor=after is not None)
            rows = (await db.execute(sql, q)).mappings().all()
            if len(rows) < batch:
                exhausted = True
            states = await _replayed_states(db, rows)
            for row in rows:
                last_scanned = row
                if status_filter is None or states[row["id"]][0] == status_filter:
                    picked.append((row, states[row["id"]]))
                    if len(picked) > page_limit:
                        break
            if len(picked) > page_limit or exhausted or not rows:
                break
            after = (rows[-1]["created_at"], rows[-1]["id"])

    has_next = len(picked) > page_limit
    picked = picked[:page_limit]
    async with factory() as db:
        extremes = await _fetch_lifecycle_extremes(db, [row["id"] for row, _ in picked])
    items = [
        RunItem(
            id=row["id"],
            projectId=row["project_id"],
            status=state[0],
            planApproved=state[1],
            blockedBy=state[2],
            createdAt=row["created_at"],
            startedAt=extremes.get(row["id"], {}).get("started_at"),
            finishedAt=extremes.get(row["id"], {}).get("finished_at"),
        )
        for row, state in picked
    ]
    if has_next and items:
        next_cursor = _encode_cursor(items[-1].createdAt, items[-1].id)
    elif not has_next and not exhausted and last_scanned is not None:
        # scan cap reached with the page not full: resume after the last row looked at
        next_cursor = _encode_cursor(last_scanned["created_at"], last_scanned["id"])
    else:
        next_cursor = None
    return RunsPage(items=items, nextCursor=next_cursor)
