"""Mission 12 G10 — user-authored project rules CRUD (part3 gap #9, X2 slice 1).

Own router module per house convention (api_keys.py / teams_api.py): ATLAS
registers it at merge with ``app.include_router(router)`` — app.py stays
untouched this lane.

Contract M12-G10:
- GET  /api/v1/projects/{projectId}/rules -> 200
      {projectId, rulesText, updatedAt, updatedBy}
      Missing row = "no rules configured": 200 with rulesText "" (the editor
      opens empty; the loader treats "" as no-block). Unknown/foreign project:
      identical 404, no existence oracle across tenants.
- PUT  /api/v1/projects/{projectId}/rules {rulesText} -> 200 same projection.
      Upsert (one live document per project); over-cap is 413 RULES_TOO_LARGE;
      blank-after-strip saves as the empty string (explicitly clearing).
- DELETE /api/v1/projects/{projectId}/rules -> 204. Idempotent: deleting when
      nothing is stored still answers 204.

Security posture:
- Admin/engineer gated on every mutation (can(principal, "write")); viewers
  and guests may READ only — matching how plan approvals treat roles.
- Team scoping via get_principal + projects.team_id; a foreign or unknown
  projectId always answers 404 (never 403) so ids can't be probed.
- Size cap enforced at the API edge (413 before any write); migration CHECK
  is defense in depth against non-API writers.
- Audited as allowlisted actions rules.saved / rules.deleted via audit_emit
  helpers (never raise into the request path).
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, can, error_body
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit_emit import emit_rules_deleted, emit_rules_saved
from .auth import get_principal
from .models import Project, ProjectRule

router = APIRouter(prefix="/api/v1")

MAX_RULES_BYTES = 64 * 1024  # 64KB — mirrors migration CHECK + S4 loader cap


def _rules_public(row: ProjectRule, project_id: str) -> dict:
    """camelCase projection per api-conventions.md."""
    return {
        "projectId": project_id,
        "rulesText": row.rules_text,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "updatedBy": row.updated_by,
    }


async def _owned_project(db, project_id: str, principal: Principal) -> bool:
    """True iff the project exists AND belongs to the caller's team.

    Unknown and foreign projects are indistinguishable to the caller (both
    False → 404 upstream): no cross-tenant existence oracle.
    """
    team_id = (
        await db.execute(
            select(Project.team_id).where(Project.id == project_id)
        )
    ).scalar_one_or_none()
    return team_id is not None and team_id == principal.teamId


class RulesBody(BaseModel):
    # Two-layer cap: this Field is only an anti-abuse sanity bound (absurd
    # payloads die at validation); the AUTHORITATIVE 64KB policy cap is the
    # UTF-8 byte check in the handler, because multi-byte text can exceed
    # 64KB while staying under any char count (e.g. CJK) — and it must answer
    # the policy code 413 RULES_TOO_LARGE, not a generic 422.
    rules_text: str = Field(alias="rulesText", max_length=MAX_RULES_BYTES * 16)

    model_config = {"populate_by_name": True}


@router.get("/projects/{project_id}/rules")
async def get_rules(
    project_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Read the project's rules document ("" when none was ever saved)."""
    async with request.app.state.session_factory() as db:
        if not await _owned_project(db, project_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "project not found"),
            )
        row = (
            await db.execute(
                select(ProjectRule).where(ProjectRule.project_id == project_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return {
                "projectId": project_id,
                "rulesText": "",
                "updatedAt": None,
                "updatedBy": None,
            }
        return _rules_public(row, project_id)


@router.put("/projects/{project_id}/rules")
async def save_rules(
    project_id: str,
    body: RulesBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Upsert the single rules document for an owned project (admin/engineer)."""
    if not can(principal, "write"):
        raise HTTPException(
            status_code=403,
            detail=error_body(
                "FORBIDDEN", "viewer/guest roles cannot edit project rules"
            ),
        )
    text_value = body.rules_text
    if len(text_value.encode("utf-8")) > MAX_RULES_BYTES:
        raise HTTPException(
            status_code=413,
            detail=error_body(
                "RULES_TOO_LARGE",
                f"project rules exceed the {MAX_RULES_BYTES // 1024}KB limit",
            ),
        )
    async with request.app.state.session_factory() as db:
        if not await _owned_project(db, project_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "project not found"),
            )
        row = (
            await db.execute(
                select(ProjectRule).where(ProjectRule.project_id == project_id)
            )
        ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = ProjectRule(project_id=project_id)
            db.add(row)
        row.rules_text = text_value
        row.updated_by = principal.userId
        row.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        await emit_rules_saved(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=project_id,
            detail={
                "chars": len(text_value),
                "created": created,
            },
        )
        return _rules_public(row, project_id)


@router.delete("/projects/{project_id}/rules", status_code=204)
async def delete_rules(
    project_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Remove the rules document entirely ("no rules configured"). Idempotent."""
    if not can(principal, "write"):
        raise HTTPException(
            status_code=403,
            detail=error_body(
                "FORBIDDEN", "viewer/guest roles cannot edit project rules"
            ),
        )
    async with request.app.state.session_factory() as db:
        if not await _owned_project(db, project_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "project not found"),
            )
        row = (
            await db.execute(
                select(ProjectRule).where(ProjectRule.project_id == project_id)
            )
        ).scalar_one_or_none()
        had_row = row is not None
        if had_row:
            await db.delete(row)
            await db.commit()
            await emit_rules_deleted(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                target_id=project_id,
                detail={"chars": len(row.rules_text)},
            )
    # No row stored: fall through — DELETE of "no rules" stays 204 (idempotent).
