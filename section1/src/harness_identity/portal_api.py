"""P-17 client trust portal: per-project grants for team guests.

An admin grants a guest (a partner's end-client) read access to named projects.
The access check itself is `harness_contracts.fastapi_auth.guest_gate`, applied
when the principal is resolved; this module only manages the grant rows and
tells a caller which projects the portal may show them.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, error_body
from sqlalchemy import delete, select

from .audit_emit import _emit
from .auth import get_principal
from .models import Project, ProjectGuestGrant, TeamMember
from .models import Role as DbRole

router = APIRouter(prefix="/api/v1")
Caller = Annotated[Principal, Depends(get_principal)]


async def _admin_project(db, principal: Principal, project_id: str) -> Project:
    if principal.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail=error_body("FORBIDDEN", "admin only"))
    project = await db.get(Project, project_id)
    if project is None or project.team_id != principal.teamId:
        # Another team's project answers like an unknown id: no oracle.
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "project not found"))
    return project


@router.get("/portal/projects")
async def portal_projects(request: Request, principal: Caller) -> dict:
    """Projects the portal may show the caller: a guest's grants, or the team's projects."""
    stmt = select(Project).where(Project.team_id == principal.teamId).order_by(Project.name)
    if principal.role == Role.GUEST:
        stmt = stmt.join(ProjectGuestGrant, ProjectGuestGrant.project_id == Project.id).where(
            ProjectGuestGrant.user_id == principal.userId
        )
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(stmt)).scalars().all()
    return {"items": [{"id": p.id, "name": p.name} for p in rows]}


@router.get("/projects/{project_id}/guests")
async def list_guests(project_id: str, request: Request, principal: Caller) -> dict:
    async with request.app.state.session_factory() as db:
        await _admin_project(db, principal, project_id)
        rows = (
            await db.execute(
                select(ProjectGuestGrant).where(ProjectGuestGrant.project_id == project_id)
            )
        ).scalars().all()
    return {"items": [{"userId": g.user_id, "grantedBy": g.granted_by} for g in rows]}


@router.put("/projects/{project_id}/guests/{user_id}")
async def grant_guest(project_id: str, user_id: str, request: Request, principal: Caller) -> dict:
    """Admin only. The target must already be a guest of this team (invite them as guest first)."""
    async with request.app.state.session_factory() as db:
        await _admin_project(db, principal, project_id)
        member = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == principal.teamId, TeamMember.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if member is None or DbRole(member.role) is not DbRole.GUEST:
            raise HTTPException(
                status_code=422,
                detail=error_body("VALIDATION_ERROR", "the person must be a guest of this team"),
            )
        if await db.get(ProjectGuestGrant, (project_id, user_id)) is None:
            db.add(
                ProjectGuestGrant(
                    project_id=project_id, user_id=user_id, granted_by=principal.userId
                )
            )
            await db.commit()
            await _emit(
                db, "project.guest_granted", user_id=principal.userId, team_id=principal.teamId,
                target_type="project", target_id=project_id, detail={"targetUserId": user_id},
            )
    return {"projectId": project_id, "userId": user_id}


@router.delete("/projects/{project_id}/guests/{user_id}", status_code=204)
async def revoke_guest(project_id: str, user_id: str, request: Request, principal: Caller) -> None:
    async with request.app.state.session_factory() as db:
        await _admin_project(db, principal, project_id)
        gone = await db.execute(
            delete(ProjectGuestGrant).where(
                ProjectGuestGrant.project_id == project_id, ProjectGuestGrant.user_id == user_id
            )
        )
        await db.commit()
        if gone.rowcount:
            await _emit(
                db, "project.guest_revoked", user_id=principal.userId, team_id=principal.teamId,
                target_type="project", target_id=project_id, detail={"targetUserId": user_id},
            )
