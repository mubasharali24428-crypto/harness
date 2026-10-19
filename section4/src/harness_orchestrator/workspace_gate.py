"""Session 3 lane W (part 39.008): non-active workspaces accept no new runs.

Sections may import only ``harness_contracts``, so this speaks raw SQL to the
frozen ``projects`` / ``workspaces`` tables (migration 0040). Semantics:

- project has no ``workspace_id`` (or no project) -> allowed, exactly as before;
- workspace ``state`` is ``active`` -> allowed;
- any other state (paused/archived/deleted) -> refused with HTTP 409
  ``WORKSPACE_NOT_ACTIVE`` and a best-effort ``workspace.run_refused`` audit row;
- the workspace tables/column do not exist yet (pre-0040 schema, bare unit
  stores) -> allowed and logged. This is the one deliberate fail-open: a store
  that has no workspaces cannot have a non-active one.

Callers: ``OrchestratorService.start_execution`` (so a start is refused with a
409) and ``_dispatch_claim`` (so a run queued BEFORE the archive cannot start
after it). The state is re-read from the store on every call (no cache).
"""

import json
import logging
from typing import Any

from fastapi import HTTPException
from harness_contracts.timeutil import db_now

logger = logging.getLogger(__name__)

CODE = "WORKSPACE_NOT_ACTIVE"
AUDIT_ACTION = "workspace.run_refused"

_STATE_SQL = (
    "SELECT w.id AS wid, w.state AS state FROM projects p"
    " JOIN workspaces w ON w.id = p.workspace_id WHERE p.id = :pid"
)


class WorkspaceNotActiveError(HTTPException):
    """409 envelope raised straight through any FastAPI route."""

    def __init__(self, workspace_id: str, state: str) -> None:
        self.workspace_id = workspace_id
        self.state = state
        super().__init__(
            status_code=409,
            detail={
                "error": {
                    "code": CODE,
                    "message": f"workspace is {state}; it cannot accept new runs",
                }
            },
        )


async def workspace_block(factory: Any, project_id: str | None) -> tuple[str, str] | None:
    """(workspace_id, state) when the project's workspace is NOT active, else None."""
    if factory is None or not project_id:
        return None
    from sqlalchemy import text

    try:
        async with factory() as db:
            row = (await db.execute(text(_STATE_SQL), {"pid": project_id})).first()
    except Exception:  # noqa: BLE001 — schema without workspaces: nothing to enforce
        logger.warning("workspace gate: workspaces schema unavailable; allowing", exc_info=True)
        return None
    if row is None or row.state == "active":
        return None
    return str(row.wid), str(row.state)


async def _audit_refusal(factory: Any, run_id: str, project_id: str, wid: str, state: str,
                         where: str) -> None:
    from sqlalchemy import text

    detail = {"runId": run_id, "projectId": project_id, "workspaceId": wid,
              "state": state, "where": where}
    try:
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action,"
                    " target_type, target_id, detail, ts)"
                    " VALUES (NULL, 'human', :action, 'workspace', :wid, :detail, :ts)"
                ),
                {"action": AUDIT_ACTION, "wid": wid, "detail": json.dumps(detail),
                 "ts": db_now()},
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — audit must never mask the refusal
        logger.exception("workspace.run_refused audit failed (run=%s)", run_id)


async def check_run_start(service: Any, run_id: str, project_id: str | None, *,
                          where: str = "start") -> tuple[str, str] | None:
    """Return the block (and audit it) when the run's workspace is not active."""
    factory = getattr(service, "session_factory", None)
    block = await workspace_block(factory, project_id)
    if block is not None:
        await _audit_refusal(factory, run_id, project_id or "", block[0], block[1], where)
    return block


async def enforce_run_start(service: Any, run_id: str, project_id: str | None) -> None:
    """Raise :class:`WorkspaceNotActiveError` (409) for a non-active workspace."""
    block = await check_run_start(service, run_id, project_id, where="start")
    if block is not None:
        raise WorkspaceNotActiveError(*block)
