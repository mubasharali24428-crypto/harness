"""Mission 12 E3a — roster management: member removal + role change (H-010/W2-C1).

Own router module so nothing else has to touch app.py this wave: expose
`router` (same prefix convention as invites.py/api_keys.py) and ATLAS
registers it at merge with ``app.include_router(router)``.

Contract W2-C1:
- DELETE /api/v1/teams/{team_id}/members/{user_id} -> 204
- PATCH  /api/v1/teams/{team_id}/members/{user_id} {role} -> 200 {userId, role}

Security posture:
- Both verbs are admin-only; engineer/viewer/guest callers get 403 FORBIDDEN
  (contract envelope) before anything else is consulted.
- Cross-team access answers 404 NOT_FOUND with no existence oracle, matching
  the api_keys revoke convention: an admin of team B probing a team A userId
  gets the identical answer as an unknown id.
- You cannot remove your own membership (409 SELF_REMOVAL) and the last admin
  can never be removed or demoted (409 LAST_ADMIN). Guard order note: the
  last-admin check runs first, so the only admin deleting/demoting THEMSELVES
  reports LAST_ADMIN (the stronger, pinned code); with a peer admin present
  self-removal reports SELF_REMOVAL.
- Assignable roles are {admin, engineer, viewer}; anything else is 422
  VALIDATION_ERROR. guest stays invite-flow-only legacy and is never granted
  through this endpoint.
- Both transitions emit member.removed / member.role_changed via audit_emit
  helpers, which never throw into the request path. A PATCH that does not
  change the role stays silent (same convention as the idempotent double
  revoke in api_keys) yet still answers 200.

Storage note (verified empirically against this codebase): TeamMember.role is
a SQLAlchemy Enum(native_enum=False) column that persists member NAMES
('ADMIN') on ORM writes while application code passes VALUES ('admin'). Every
read/write here therefore goes through ORM sessions only — a raw-SQL string
comparison against 'admin' silently matches nothing. ORM-side filters
(``TeamMember.role == "admin"``) are translated correctly by SQLAlchemy and
are safe.
"""

import enum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, error_body
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from .audit_emit import emit_member_removed, emit_member_role_changed
from .auth import get_principal
from .models import Role as DbRole
from .models import TeamMember
from .policy import require_team_stepup

router = APIRouter(prefix="/api/v1")

ASSIGNABLE_ROLES = frozenset({"admin", "engineer", "viewer"})


def _role_value(role: object) -> str:
    """Normalize TeamMember.role to its lowercase value string.

    ORM reads surface models.Role (StrEnum) members; plain strings appear only
    via defensive paths. StrEnum .value is the lowercase form either way.
    """
    return role.value if isinstance(role, enum.StrEnum) else str(role)


async def _find_member(
    db, team_id: str, user_id: str
) -> TeamMember | None:
    """Membership row scoped to (team_id, user_id) — None means 404 territory."""
    return (
        await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()


async def _count_other_admins(db, team_id: str, exclude_member_id: str) -> int:
    """Admin seats in the team OTHER than the targeted membership row."""
    rows = (
        await db.execute(
            select(TeamMember.id).where(
                TeamMember.team_id == team_id,
                TeamMember.role == DbRole.ADMIN,
                TeamMember.id != exclude_member_id,
            )
        )
    ).all()
    return len(rows)


def _require_admin_of_team(principal: Principal, team_id: str) -> None:
    """Admin-only gate with the W2-C1 no-oracle rule for the team mismatch.

    Order matters and is deliberate: role is checked first (a non-admin gets
    403 wherever they probe), THEN the caller's own team (a foreign admin gets
    exactly the same 404 an unknown member id would produce).
    """
    if principal.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "admin only"),
        )
    if principal.teamId != team_id:
        # Cross-team access: identical answer to an unknown id — no oracle.
        raise HTTPException(
            status_code=404,
            detail=error_body("NOT_FOUND", "member not found"),
        )


class MemberRoleBody(BaseModel):
    # Deliberately loose here: membership-set validation ({admin, engineer,
    # viewer}) lives in the handler so EVERY out-of-set value — including ""
    # — gets the contract error_body(422 VALIDATION_ERROR) envelope instead of
    # FastAPI's default list-shaped validation detail. Type mismatches (e.g.
    # {"role": 123}) still die at Pydantic; F1 unifies that surface later.
    role: str = Field(max_length=20)


@router.delete("/teams/{team_id}/members/{user_id}", status_code=204)
async def remove_member(
    team_id: str,
    user_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Admin-only removal. 204 on success; foreign/unknown members 404."""
    _require_admin_of_team(principal, team_id)
    async with request.app.state.session_factory() as db:
        target = await _find_member(db, team_id, user_id)
        if target is None:
            # Unknown id OR another team's member: identical answer, no oracle.
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "member not found"),
            )
        if _role_value(target.role) == "admin":
            if await _count_other_admins(db, team_id, target.id) == 0:
                raise HTTPException(
                    status_code=409,
                    detail=error_body("LAST_ADMIN", "cannot remove the last admin"),
                )
        if target.user_id == principal.userId:
            raise HTTPException(
                status_code=409,
                detail=error_body("SELF_REMOVAL", "ask a fellow admin to remove you"),
            )
        removed_role = _role_value(target.role)  # capture BEFORE expiry-prone ops
        membership_id = str(target.id)
        # Revoke this team's sessions BEFORE the membership row disappears:
        # get_principal already 401s a removed member, and deleting the
        # sessions here removes the credential itself (defense in depth).
        await db.execute(
            text("DELETE FROM auth_sessions WHERE user_id = :u AND team_id = :t"),
            {"u": user_id, "t": team_id},
        )
        await db.delete(target)
        await db.commit()
        # H-010 audit trail: the transition is the event; failure to emit must
        # never break the 204 (helpers degrade to a warning internally).
        await emit_member_removed(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=membership_id,
            detail={
                "removedUserId": user_id,
                "role": removed_role,
                "before": {"role": removed_role, "member": True},
                "after": {"role": None, "member": False},
            },
        )
    # 204: empty body by contract.


@router.patch("/teams/{team_id}/members/{user_id}")
async def change_member_role(
    team_id: str,
    user_id: str,
    body: MemberRoleBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Admin-only role change. 200 {userId, role}; invalid role 422."""
    _require_admin_of_team(principal, team_id)
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=422,
            detail=error_body(
                "VALIDATION_ERROR",
                "role must be one of: admin, engineer, viewer",
            ),
        )
    new_role = body.role
    async with request.app.state.session_factory() as db:
        target = await _find_member(db, team_id, user_id)
        if target is None:
            # Same no-oracle answer as DELETE for foreign/unknown ids.
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "member not found"),
            )
        old_role = _role_value(target.role)
        if old_role == "admin" and new_role != "admin":
            if await _count_other_admins(db, team_id, target.id) == 0:
                raise HTTPException(
                    status_code=409,
                    detail=error_body("LAST_ADMIN", "cannot demote the last admin"),
                )
        if new_role != old_role:
            # Part 38.010: org policy may demand a fresh re-auth for role changes.
            await require_team_stepup(db, request, principal, team_id, "role.change")
            membership_id = str(target.id)  # capture pre-commit (see DELETE note)
            target.role = DbRole(new_role)
            await db.commit()
            await emit_member_role_changed(
                db,
                actor_user_id=principal.userId,
                team_id=team_id,
                target_id=membership_id,
                detail={
                    "targetUserId": user_id,
                    "fromRole": old_role,
                    "toRole": new_role,
                    # part 11.003: uniform before/after for consequential changes
                    "before": {"role": old_role},
                    "after": {"role": new_role},
                },
            )
        # No-op PATCH: 200 with current state, no audit row (api_keys parity).
        return {"userId": user_id, "role": new_role}
