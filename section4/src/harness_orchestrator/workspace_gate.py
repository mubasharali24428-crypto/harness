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
409), ``_dispatch_claim`` (so a run queued BEFORE the archive cannot start
after it) and ``resume_agent`` (a resume that would respawn the engine is a new
start). The state is re-read from the store on every call (no cache).

Two interval jobs finish the lifecycle (any replica may run them; both are idempotent):

- :func:`sweep_inactive_workspace_runs` stops runs that are still executing when their
  workspace is archived or deleted (a paused workspace only blocks new starts);
- :func:`purge_expired_workspaces` scrubs a deleted workspace once ``purge_after`` has
  passed. The row stays as a tombstone, so linked projects remain blocked.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from harness_contracts.timeutil import db_now

logger = logging.getLogger(__name__)

CODE = "WORKSPACE_NOT_ACTIVE"
AUDIT_ACTION = "workspace.run_refused"
STOP_AUDIT_ACTION = "workspace.run_stopped"
PURGE_AUDIT_ACTION = "workspace.purged"
PURGED_NAME = "[purged]"

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


async def _audit(factory: Any, action: str, wid: str, detail: dict[str, Any]) -> None:
    from sqlalchemy import text

    try:
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action,"
                    " target_type, target_id, detail, ts)"
                    " VALUES (NULL, 'human', :action, 'workspace', :wid, :detail, :ts)"
                ),
                {"action": action, "wid": wid, "detail": json.dumps(detail), "ts": db_now()},
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — audit must never mask the refusal or the job
        logger.exception("%s audit failed (workspace=%s)", action, wid)


async def _audit_refusal(factory: Any, run_id: str, project_id: str, wid: str, state: str,
                         where: str) -> None:
    await _audit(factory, AUDIT_ACTION, wid, {
        "runId": run_id, "projectId": project_id, "workspaceId": wid,
        "state": state, "where": where})


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


_RUNNING_IN_INACTIVE_SQL = (
    "SELECT c.run_id AS run_id, p.id AS pid, w.id AS wid, w.state AS state FROM run_claims c"
    " JOIN projects p ON p.id = c.project_id JOIN workspaces w ON w.id = p.workspace_id"
    " WHERE c.state = 'running' AND w.state IN ('archived', 'deleted')"
)


async def sweep_inactive_workspace_runs(service: Any) -> list[str]:
    """Stop runs that are executing (live claim) in an archived or deleted workspace.

    Returns the stopped run ids. Stop is terminal and releases the claim, which the owning
    worker's fence loop notices; a second sweep finds no running claim. A paused workspace is
    left alone: it refuses new starts and resumes, not work already underway."""
    factory = getattr(service, "session_factory", None)
    if factory is None:
        return []
    from sqlalchemy import text

    try:
        async with factory() as db:
            rows = (await db.execute(text(_RUNNING_IN_INACTIVE_SQL))).all()
    except Exception:  # noqa: BLE001 — no workspaces/run_claims schema: nothing to stop
        logger.warning("workspace sweep: schema unavailable; skipping", exc_info=True)
        return []
    stopped: list[str] = []
    for r in rows:
        try:
            changed = await service.stop_agent(r.run_id, reason="workspace_inactive")
        except Exception:  # noqa: BLE001 — one bad run must not stop the sweep
            logger.exception("workspace sweep could not stop run %s", r.run_id)
            continue
        if changed:
            stopped.append(r.run_id)
            await _audit(factory, STOP_AUDIT_ACTION, str(r.wid), {
                "runId": r.run_id, "projectId": str(r.pid), "workspaceId": str(r.wid),
                "state": str(r.state)})
    return stopped


async def workspace_run_sweeper(service: Any, interval: float) -> None:
    """Run :func:`sweep_inactive_workspace_runs` every ``interval`` seconds until cancelled."""
    while True:
        try:
            await sweep_inactive_workspace_runs(service)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the next tick retries
            logger.exception("workspace run sweep failed")
        await asyncio.sleep(interval)


_PURGE_TABLES = ("workspace_members", "workspace_bindings", "workspace_settings_versions")


async def purge_expired_workspaces(factory: Any, now: datetime | None = None) -> list[str]:
    """Scrub deleted workspaces whose ``purge_after`` has passed; return their ids.

    Members, bindings and settings history are deleted, and the row keeps only its identity:
    name, tags, settings and reason are cleared. The tombstone stays ``deleted`` (the run gate
    keeps refusing linked projects) and ``name`` marks it done, so a rerun is a no-op."""
    if factory is None:
        return []
    from sqlalchemy import text

    now = now or db_now()
    try:
        async with factory() as db:
            ids = [str(r.id) for r in (await db.execute(text(
                "SELECT id FROM workspaces WHERE state = 'deleted' AND purge_after IS NOT NULL"
                " AND purge_after <= :now AND name <> :marker"),
                {"now": now, "marker": PURGED_NAME})).all()]
    except Exception:  # noqa: BLE001 — pre-0040 schema: nothing to purge
        logger.warning("workspace purge: schema unavailable; skipping", exc_info=True)
        return []
    for wid in ids:
        async with factory() as db:
            for table in _PURGE_TABLES:
                await db.execute(text(f"DELETE FROM {table} WHERE workspace_id = :w"), {"w": wid})
            await db.execute(text(
                "UPDATE workspaces SET name = :marker, name_key = :w, tags = '[]',"
                " settings = '{}', state_reason = NULL, template_name = NULL, updated_at = :now"
                " WHERE id = :w AND state = 'deleted'"),
                {"marker": PURGED_NAME, "w": wid, "now": now})
            await db.commit()
        await _audit(factory, PURGE_AUDIT_ACTION, wid, {"workspaceId": wid})
    return ids
