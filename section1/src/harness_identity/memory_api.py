"""Marathon 2.0 W12 — project-scoped memory (Section 1 side).

User-authored advisory notes per project, injected by the orchestrator's
memory_loader at plan-proposal time next to project rules. They are explicitly
NOT instructions: the rendered block states memory cannot override rules, the
approved scope or any policy. Section 5 remains the only enforcement layer.

Contract M2-W12:
- GET    /api/v1/projects/{projectId}/memory          -> 200 {entries:[...]}
        All statuses visible to the project's team (viewers/guests read).
- POST   /api/v1/projects/{projectId}/memory          -> 201 {entry}
        {memoryText, expiresAt?} Creates a DRAFT (source=user). A draft is
        never injected. source=run is reserved for a future lane that lets a
        completed run propose memory; this API never writes it.
- POST   /api/v1/projects/{projectId}/memory/{entryId}/review -> 200 {entry}
        Admin only. Makes the note injectable.
- DELETE /api/v1/projects/{projectId}/memory/{entryId} -> 204 idempotent.
        Write roles (owners remove their project's notes).

Security posture:
- Team scoping via get_principal + projects.team_id; a foreign or unknown
  projectId always answers 404 (never 403) so ids can't be probed.
- Mutations are role-gated (write for create/delete, admin for review);
  viewers/guests may READ only — matching how plan approvals treat roles.
- Size cap enforced at the API edge (413 MEMORY_TOO_LARGE) before any write;
  the migration CHECK is defense in depth.
- Audited as allowlisted actions memory.created / memory.reviewed /
  memory.deleted via audit_emit helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, can, error_body
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit_emit import emit_memory_created, emit_memory_deleted, emit_memory_reviewed
from .auth import get_principal
from .models import Project, ProjectMemory

router = APIRouter(prefix="/api/v1")

MAX_MEMORY_BYTES = 16_384  # 16KB — mirrors migration 0067 CHECK + S4 loader cap


def _memory_public(row: ProjectMemory, project_id: str) -> dict[str, Any]:
    """camelCase projection per api-conventions.md."""
    return {
        "id": row.id,
        "projectId": project_id,
        "memoryText": row.memory_text,
        "source": row.source,
        "status": row.status,
        "reviewedBy": row.reviewed_by,
        "reviewedAt": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


async def _owned_project(db, project_id: str, principal: Principal) -> bool:
    """True iff the project exists AND belongs to the caller's team.

    Unknown and foreign projects are indistinguishable to the caller (both
    False -> 404 upstream): no cross-tenant existence oracle.
    """
    team_id = (
        await db.execute(select(Project.team_id).where(Project.id == project_id))
    ).scalar_one_or_none()
    return team_id is not None and team_id == principal.teamId


def _require_write(principal: Principal) -> None:
    if not can(principal, "write"):
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "viewer/guest roles cannot edit project memory"),
        )


def _require_admin(principal: Principal) -> None:
    if principal.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "only team admins can review memory"),
        )


class MemoryBody(BaseModel):
    memory_text: str = Field(alias="memoryText", min_length=1, max_length=MAX_MEMORY_BYTES * 4)
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = {"populate_by_name": True}


async def _get_entry_or_404(db, project_id: str, entry_id: str) -> ProjectMemory:
    row = (
        await db.execute(
            select(ProjectMemory).where(
                ProjectMemory.id == entry_id, ProjectMemory.project_id == project_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=error_body("NOT_FOUND", "memory entry not found"),
        )
    return row


@router.get("/projects/{project_id}/memory")
async def list_memory(
    project_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Every note of an owned project, drafts included."""
    async with request.app.state.session_factory() as db:
        if not await _owned_project(db, project_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "project not found"),
            )
        rows = (
            await db.execute(
                select(ProjectMemory)
                .where(ProjectMemory.project_id == project_id)
                .order_by(ProjectMemory.created_at)
            )
        ).scalars().all()
        return {"entries": [_memory_public(row, project_id) for row in rows]}


@router.post("/projects/{project_id}/memory", status_code=201)
async def create_memory(
    project_id: str,
    body: MemoryBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Author a draft note. A draft is visible to the team but never injected."""
    _require_write(principal)
    text_value = body.memory_text
    if len(text_value.encode("utf-8")) > MAX_MEMORY_BYTES:
        raise HTTPException(
            status_code=413,
            detail=error_body(
                "MEMORY_TOO_LARGE",
                f"memory notes exceed the {MAX_MEMORY_BYTES // 1024}KB limit",
            ),
        )
    if not text_value.strip():
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", "memoryText must be non-empty text"),
        )
    async with request.app.state.session_factory() as db:
        if not await _owned_project(db, project_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "project not found"),
            )
        row = ProjectMemory(
            project_id=project_id,
            memory_text=text_value,
            source="user",
            status="draft",
            expires_at=body.expires_at,
            created_by=principal.userId,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        await emit_memory_created(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=row.id,
            detail={"projectId": project_id, "chars": len(text_value)},
        )
        return {"entry": _memory_public(row, project_id)}


@router.post("/projects/{project_id}/memory/{entry_id}/review")
async def review_memory(
    project_id: str,
    entry_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Make a note injectable. The text is unchanged; the status is the act."""
    _require_admin(principal)
    async with request.app.state.session_factory() as db:
        if not await _owned_project(db, project_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "project not found"),
            )
        row = await _get_entry_or_404(db, project_id, entry_id)
        row.status = "reviewed"
        row.reviewed_by = principal.userId
        row.reviewed_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        await emit_memory_reviewed(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=row.id,
            detail={"projectId": project_id},
        )
        return {"entry": _memory_public(row, project_id)}


@router.delete("/projects/{project_id}/memory/{entry_id}", status_code=204)
async def delete_memory(
    project_id: str,
    entry_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Remove a note. Idempotent: deleting what is absent is 204."""
    _require_write(principal)
    async with request.app.state.session_factory() as db:
        if not await _owned_project(db, project_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "project not found"),
            )
        row = (
            await db.execute(
                select(ProjectMemory).where(
                    ProjectMemory.id == entry_id, ProjectMemory.project_id == project_id
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            await db.delete(row)
            await db.commit()
            await emit_memory_deleted(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                target_id=row.id,
                detail={"projectId": project_id},
            )
        return None
