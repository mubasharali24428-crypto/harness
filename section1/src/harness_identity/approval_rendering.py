"""X-07 (10X Wave B) — approval rendering by reader: the per-member preference.

One preference, two doors:

- ``GET/PUT /api/v1/me/rendering`` — the member reads or sets their OWN rendering
  (self-service; any role including guest, because it is pure presentation state).
- ``GET/PUT /api/v1/teams/{team_id}/members/{user_id}/rendering`` — an admin sets it
  for a member of their team (X-D1); everyone else gets the same 404-without-oracle
  answer as the roster endpoints.

The preference is PRESENTATION ONLY: it selects which deterministic template section7
renders an approval with (technical | plain | decision). It never changes what the
policy engine discloses, the computed risk tier, or any gate. Values are validated
here; the default is ``technical`` (the historical engineer-to-engineer rendering).
Every successful change writes an audit row (``member.rendering_changed``) with the
before/after, and a no-op PUT stays silent but still answers 200 (the api_keys
idempotent-revoke convention).
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, error_body
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from .audit_emit import emit_member_rendering_changed
from .auth import get_principal
from .models import Role as DbRole
from .models import TeamMember

router = APIRouter(prefix="/api/v1")

RENDERINGS = ("technical", "plain", "decision")
_DEFAULT = "technical"
_SETTING = re.compile(r"^(technical|plain|decision)$")


class RenderingBody(BaseModel):
    rendering: str = Field(min_length=4, max_length=16)


def _fail(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail=error_body(code, msg))


def _valid(rendering: str) -> str:
    if not _SETTING.match(rendering or ""):
        raise _fail(422, "VALIDATION_ERROR",
                    f"rendering must be one of {', '.join(RENDERINGS)}")
    return rendering


async def _current(db, user_id: str, team_id: str) -> str:
    row = (await db.execute(
        select(TeamMember.approval_rendering).where(
            TeamMember.team_id == team_id, TeamMember.user_id == user_id
        )
    )).scalar_one_or_none()
    return row or _DEFAULT


@router.get("/me/rendering")
async def get_my_rendering(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    async with request.app.state.session_factory() as db:
        value = await _current(db, principal.userId, principal.teamId)
    return {"userId": principal.userId, "rendering": value}


@router.put("/me/rendering")
async def put_my_rendering(
    body: RenderingBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    value = _valid(body.rendering)
    async with request.app.state.session_factory() as db:
        before = await _current(db, principal.userId, principal.teamId)
        if before != value:
            await db.execute(
                text("UPDATE team_members SET approval_rendering = :r"
                     " WHERE team_id = :t AND user_id = :u"),
                {"r": value, "t": principal.teamId, "u": principal.userId},
            )
            await emit_member_rendering_changed(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                member_user_id=principal.userId,
                before=before,
                after=value,
                kind="self",
            )
    return {"userId": principal.userId, "rendering": value}


@router.get("/teams/{team_id}/members/{user_id}/rendering")
async def get_member_rendering(
    team_id: str,
    user_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    if principal.role != DbRole.ADMIN:
        raise _fail(403, "FORBIDDEN", "admin only")
    if team_id != principal.teamId:
        raise _fail(404, "NOT_FOUND", "no such member")  # no cross-team oracle
    async with request.app.state.session_factory() as db:
        row = (await db.execute(
            select(TeamMember.id).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )).scalar_one_or_none()
        if row is None:
            raise _fail(404, "NOT_FOUND", "no such member")
        value = await _current(db, user_id, team_id)
    return {"userId": user_id, "rendering": value}


@router.put("/teams/{team_id}/members/{user_id}/rendering")
async def put_member_rendering(
    team_id: str,
    user_id: str,
    body: RenderingBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    value = _valid(body.rendering)
    # Same gate law as the roster endpoints (teams_api._require_admin_of_team):
    # role first (a non-admin gets 403 wherever they probe), then the caller's
    # own team (a foreign admin gets the same 404 an unknown member would).
    if principal.role != DbRole.ADMIN:
        raise _fail(403, "FORBIDDEN", "only an admin can set another member's rendering")
    if team_id != principal.teamId:
        raise _fail(404, "NOT_FOUND", "no such member")  # no cross-team oracle
    async with request.app.state.session_factory() as db:
        row = (await db.execute(
            select(TeamMember.id).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )).scalar_one_or_none()
        if row is None:
            raise _fail(404, "NOT_FOUND", "no such member")
        before = await _current(db, user_id, team_id)
        if before != value:
            await db.execute(
                text("UPDATE team_members SET approval_rendering = :r"
                     " WHERE team_id = :t AND user_id = :u"),
                {"r": value, "t": team_id, "u": user_id},
            )
            await emit_member_rendering_changed(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                member_user_id=user_id,
                before=before,
                after=value,
                kind="admin",
            )
    return {"userId": user_id, "rendering": value}
