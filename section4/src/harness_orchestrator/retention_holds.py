"""Audited team-admin lifecycle for run retention holds (Session 8 / part 58)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from harness_contracts.roles import Principal, Role
from harness_contracts.timeutil import db_now
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api, collab

router = APIRouter(prefix="/api/v1/orchestrator", tags=["retention"])
_RUN_GUARD = api._authz_dep("read", audit=True)
RunAdmin = Annotated[Principal, Depends(_RUN_GUARD)]


class HoldRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("reason must contain at least three non-space characters")
        return value


def _service() -> Any:
    service = api.get_orchestrator_service()
    if service.session_factory is None:
        raise HTTPException(503, detail={
            "error": {"code": "RETENTION_UNAVAILABLE", "message": "database not wired"}
        })
    return service


async def _lock_run(db: Any, run_id: str) -> None:
    """Match the purge worker's transaction lock to close hold/delete races."""
    if db.bind.dialect.name == "postgresql":
        await db.execute(text(
            "SELECT pg_advisory_xact_lock(hashtextextended('run-retention-holds', 0))"
        ))
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:run_id, 0))"),
            {"run_id": run_id},
        )


async def _admin_or_deny(actor: Principal, run_id: str, action: str) -> None:
    if actor.role == Role.ADMIN:
        return
    service = _service()
    async with service.session_factory() as db:
        await collab._audit(db, actor, "authz.denied", run_id, {
            "reason": "ROLE_DENIED", "attempt": action,
        })
        await db.commit()
    raise HTTPException(403, detail={
        "error": {"code": "ROLE_DENIED", "message": "only team admins may manage retention holds"}
    })


@router.get("/runs/{runId}/retention-hold")
async def get_hold(runId: str, actor: RunAdmin) -> dict[str, Any]:
    await _admin_or_deny(actor, runId, "retention_hold.read")
    service = _service()
    async with service.session_factory() as db:
        row = (await db.execute(text(
            "SELECT held_by, reason, created_at FROM run_retention_holds WHERE run_id=:r"
        ), {"r": runId})).first()
    if row is None:
        return {"runId": runId, "held": False}
    created = row.created_at
    return {
        "runId": runId,
        "held": True,
        "heldBy": row.held_by,
        "reason": row.reason,
        "createdAt": created.isoformat() if hasattr(created, "isoformat") else str(created),
    }


@router.put("/runs/{runId}/retention-hold", status_code=201)
async def create_hold(runId: str, req: HoldRequest, actor: RunAdmin) -> dict[str, Any]:
    await _admin_or_deny(actor, runId, "retention_hold.create")
    service = _service()
    now = db_now()
    async with service.session_factory() as db:
        await _lock_run(db, runId)
        try:
            await db.execute(text(
                "INSERT INTO run_retention_holds (run_id, held_by, reason, created_at) "
                "VALUES (:r, :u, :reason, :now)"
            ), {"r": runId, "u": actor.userId, "reason": req.reason, "now": now})
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, detail={
                "error": {"code": "RETENTION_HOLD_EXISTS", "message": "run already has a hold"}
            }) from exc
        await collab._audit(db, actor, "run.retention_hold_created", runId, {
            "heldBy": actor.userId,
        })
        await db.commit()
    return {"runId": runId, "held": True, "heldBy": actor.userId, "createdAt": now.isoformat()}


@router.delete("/runs/{runId}/retention-hold", status_code=204)
async def release_hold(runId: str, actor: RunAdmin) -> None:
    await _admin_or_deny(actor, runId, "retention_hold.release")
    service = _service()
    async with service.session_factory() as db:
        await _lock_run(db, runId)
        result = await db.execute(text(
            "DELETE FROM run_retention_holds WHERE run_id=:r"
        ), {"r": runId})
        if not result.rowcount:
            await db.rollback()
            raise HTTPException(404, detail={
                "error": {"code": "RETENTION_HOLD_NOT_FOUND", "message": "no hold for run"}
            })
        await collab._audit(db, actor, "run.retention_hold_released", runId, {
            "releasedBy": actor.userId,
        })
        await db.commit()
