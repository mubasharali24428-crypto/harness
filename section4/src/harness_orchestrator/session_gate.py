"""Session 4 lane S (part 40.005): runs of a non-active session accept no new start.

Sections may import only ``harness_contracts``, so this speaks raw SQL to the frozen
``session_runs`` / ``collab_sessions`` tables (migration 0041). Semantics:

- the run is not linked to any session -> allowed, exactly as before;
- the linked session ``state`` is ``active`` -> allowed;
- any other state (paused/completed/archived/deleted) -> refused with HTTP 409
  ``SESSION_NOT_ACTIVE`` and a best-effort ``session.run_refused`` audit row;
- the session tables do not exist (pre-0041 schema, bare unit stores) -> allowed. Only a
  missing-table error is tolerated; any other database error propagates (fail closed), so
  a flaky read can never start a run inside an archived session.

Callers: ``OrchestratorService.start_execution`` (409 on start) and ``_dispatch_claim``
(a run queued BEFORE the session was paused/archived cannot start after it). The state is
re-read on every call (no cache). Runs already executing are not stopped by a state change.
"""

import json
import logging
from typing import Any

from fastapi import HTTPException
from harness_contracts.timeutil import db_now

logger = logging.getLogger(__name__)

CODE = "SESSION_NOT_ACTIVE"
AUDIT_ACTION = "session.run_refused"

_STATE_SQL = (
    "SELECT s.id AS sid, s.state AS state FROM session_runs sr"
    " JOIN collab_sessions s ON s.id = sr.session_id WHERE sr.run_id = :rid"
)
_MISSING_TABLE = ("no such table", "does not exist", "undefinedtable")


class SessionNotActiveError(HTTPException):
    """409 envelope raised straight through any FastAPI route."""

    def __init__(self, session_id: str, state: str) -> None:
        self.session_id = session_id
        self.state = state
        super().__init__(
            status_code=409,
            detail={
                "error": {
                    "code": CODE,
                    "message": f"session is {state}; it cannot accept new runs",
                }
            },
        )


async def session_block(factory: Any, run_id: str | None) -> tuple[str, str] | None:
    """(session_id, state) when the run's session is NOT active, else None."""
    if factory is None or not run_id:
        return None
    from sqlalchemy import text

    try:
        async with factory() as db:
            row = (await db.execute(text(_STATE_SQL), {"rid": run_id})).first()
    except Exception as exc:  # noqa: BLE001 — only "schema not there" is tolerated
        if any(m in str(exc).lower() for m in _MISSING_TABLE):
            logger.debug("session gate: sessions schema unavailable; allowing")
            return None
        raise
    if row is None or row.state == "active":
        return None
    return str(row.sid), str(row.state)


async def _audit_refusal(factory: Any, run_id: str, sid: str, state: str, where: str) -> None:
    from sqlalchemy import text

    detail = {"runId": run_id, "sessionId": sid, "state": state, "where": where}
    try:
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action,"
                    " target_type, target_id, detail, ts)"
                    " VALUES (NULL, 'human', :action, 'session', :sid, :detail, :ts)"
                ),
                {"action": AUDIT_ACTION, "sid": sid, "detail": json.dumps(detail),
                 "ts": db_now()},
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — audit must never mask the refusal
        logger.exception("session.run_refused audit failed (run=%s)", run_id)


async def check_session_run_start(service: Any, run_id: str, *,
                                  where: str = "start") -> tuple[str, str] | None:
    """Return the block (and audit it) when the run's session is not active."""
    factory = getattr(service, "session_factory", None)
    block = await session_block(factory, run_id)
    if block is not None:
        await _audit_refusal(factory, run_id, block[0], block[1], where)
    return block


async def enforce_session_run_start(service: Any, run_id: str) -> None:
    """Raise :class:`SessionNotActiveError` (409) for a non-active session."""
    block = await check_session_run_start(service, run_id, where="start")
    if block is not None:
        raise SessionNotActiveError(*block)
