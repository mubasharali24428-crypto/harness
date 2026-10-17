"""A1-S4 — SCIM 2.0 provisioning (F145), alongside OIDC SSO.

Admin surface (bearer session, admin only): ``/api/v1/identity/scim/tokens``.
IdP surface (SCIM bearer token bound to ONE team): ``/api/v1/identity/scim/v2/*``.

Hard rules, each pinned by tests:
- A SCIM token only ever sees/changes members of its own team (foreign ids => 404).
- SCIM can never grant ``admin`` and never edits or deactivates an admin's role;
  it may deactivate an admin only when another active admin remains (last-admin law).
- Deprovisioning (``active:false`` or DELETE) DEACTIVATES the membership, deletes that
  team's login sessions, and keeps the row (audit history is RESTRICT-protected).
  ``active:true`` reactivates with the prior role. The global ``users`` row is never
  disabled — the person may belong to other teams.
- Only a SHA-256 hash of a SCIM token is stored; it is shown once at creation.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from harness_contracts import Principal, Role, error_body
from sqlalchemy import delete, select

from .audit_emit import emit_identity_event
from .auth import get_principal
from .models import AuthSession, ScimToken, TeamMember, User
from .models import Role as DbRole

router = APIRouter(prefix="/api/v1/identity")

SCIM_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCIM_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ASSIGNABLE = {"engineer", "viewer"}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
FILTER_RE = re.compile(r'^userName\s+eq\s+"([^"]{1,320})"$', re.IGNORECASE)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _err(status: int, detail: str, scim_type: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"schemas": [SCIM_ERROR], "status": str(status), "detail": detail}
    if scim_type:
        body["scimType"] = scim_type
    return JSONResponse(body, status_code=status, media_type="application/scim+json")


def _ok(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type="application/scim+json")


def _role_value(role: Any) -> str:
    return str(getattr(role, "value", role)).lower()


def _resource(user: User, member: TeamMember) -> dict:
    return {
        "schemas": [SCIM_USER],
        "id": user.id,
        "userName": user.email,
        "displayName": member.display_name,
        "active": member.deactivated_at is None,
        "roles": [{"value": _role_value(member.role)}],
    }


# ---------------------------------------------------------------- token admin


def _admin(p: Principal) -> None:
    if p.role != Role.ADMIN:
        raise HTTPException(403, detail=error_body("FORBIDDEN", "admin role required"))


@router.post("/scim/tokens", status_code=201)
async def create_token(
    request: Request, p: Annotated[Principal, Depends(get_principal)], label: str = ""
) -> dict:
    _admin(p)
    token = "scim_" + secrets.token_urlsafe(32)
    async with request.app.state.session_factory() as db:
        row = ScimToken(
            team_id=p.teamId, token_hash=_hash(token), label=label[:120], created_by=p.userId
        )
        db.add(row)
        await db.commit()
        await emit_identity_event(
            db,
            "scim.token_created",
            actor_user_id=p.userId,
            team_id=p.teamId,
            target_type="scim_token",
            target_id=row.id,
        )
    return {"id": row.id, "token": token, "note": "shown once; store it in the IdP"}


@router.get("/scim/tokens")
async def list_tokens(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict:
    _admin(p)
    async with request.app.state.session_factory() as db:
        rows = (
            (await db.execute(select(ScimToken).where(ScimToken.team_id == p.teamId)))
            .scalars()
            .all()
        )
    return {
        "items": [{"id": r.id, "label": r.label, "revoked": r.revoked_at is not None} for r in rows]
    }


@router.delete("/scim/tokens/{token_id}", status_code=204)
async def revoke_token(
    token_id: str, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> None:
    _admin(p)
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(ScimToken).where(ScimToken.id == token_id, ScimToken.team_id == p.teamId)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail=error_body("NOT_FOUND", "token not found"))
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            await db.commit()
            await emit_identity_event(
                db,
                "scim.token_revoked",
                actor_user_id=p.userId,
                team_id=p.teamId,
                target_type="scim_token",
                target_id=row.id,
            )


# ---------------------------------------------------------------- SCIM v2 (IdP)


async def _team_of(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    async with request.app.state.session_factory() as db:
        return (
            await db.execute(
                select(ScimToken.team_id).where(
                    ScimToken.token_hash == _hash(auth.removeprefix("Bearer ").strip()),
                    ScimToken.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()


async def _member(db, team_id: str, user_id: str):
    return (
        await db.execute(
            select(User, TeamMember)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id, User.id == user_id)
        )
    ).first()


async def _other_active_admins(db, team_id: str, member_id: str) -> int:
    rows = (
        await db.execute(
            select(TeamMember.id).where(
                TeamMember.team_id == team_id,
                TeamMember.role == DbRole.ADMIN,
                TeamMember.id != member_id,
                TeamMember.deactivated_at.is_(None),
            )
        )
    ).all()
    return len(rows)


async def _deactivate(db, team_id: str, user: User, member: TeamMember):
    """Deactivate + kill this team's sessions. Returns an error response or None."""
    if member.deactivated_at is not None:
        return None
    if (
        _role_value(member.role) == "admin"
        and await _other_active_admins(db, team_id, member.id) == 0
    ):
        return _err(409, "cannot deprovision the last active admin", "mutability")
    member.deactivated_at = datetime.now(UTC)
    await db.execute(
        delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.team_id == team_id)
    )
    await db.commit()
    await emit_identity_event(
        db,
        "scim.user_deprovisioned",
        actor_user_id=None,
        team_id=team_id,  # type: ignore[arg-type]
        target_type="user",
        target_id=user.id,
    )
    return None


@router.get("/scim/v2/ServiceProviderConfig")
async def service_provider_config(request: Request):
    if await _team_of(request) is None:
        return _err(401, "invalid SCIM token")
    return _ok(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False},
            "filter": {"supported": True, "maxResults": 100},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer token"}],
        }
    )


@router.get("/scim/v2/Users")
async def list_users(
    request: Request, filter: str | None = None, startIndex: int = 1, count: int = 50
):  # noqa: A002, N803
    team_id = await _team_of(request)
    if team_id is None:
        return _err(401, "invalid SCIM token")
    count = max(0, min(count, 100))
    startIndex = max(1, startIndex)  # noqa: N806
    stmt = (
        select(User, TeamMember)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id)
        .order_by(User.email)
    )
    if filter:
        m = FILTER_RE.match(filter.strip())
        if not m:
            return _err(400, "only 'userName eq \"...\"' filters are supported", "invalidFilter")
        stmt = stmt.where(User.email == m.group(1).lower())
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(stmt)).all()
    page = rows[startIndex - 1 : startIndex - 1 + count]
    return _ok(
        {
            "schemas": [SCIM_LIST],
            "totalResults": len(rows),
            "startIndex": startIndex,
            "itemsPerPage": len(page),
            "Resources": [_resource(u, m) for u, m in page],
        }
    )


@router.get("/scim/v2/Users/{user_id}")
async def get_user(user_id: str, request: Request):
    team_id = await _team_of(request)
    if team_id is None:
        return _err(401, "invalid SCIM token")
    async with request.app.state.session_factory() as db:
        found = await _member(db, team_id, user_id)
    if found is None:
        return _err(404, "user not found")
    return _ok(_resource(*found))


def _requested_role(doc: dict) -> tuple[str | None, JSONResponse | None]:
    roles = doc.get("roles")
    if roles is None:
        return None, None
    value = (
        roles[0].get("value")
        if isinstance(roles, list) and roles and isinstance(roles[0], dict)
        else None
    )
    if not isinstance(value, str) or value.lower() not in ASSIGNABLE:
        return None, _err(
            400, "role must be engineer or viewer; SCIM cannot grant other roles", "invalidValue"
        )
    return value.lower(), None


@router.post("/scim/v2/Users")
async def create_user(request: Request):
    team_id = await _team_of(request)
    if team_id is None:
        return _err(401, "invalid SCIM token")
    try:
        doc = await request.json()
    except ValueError:
        return _err(400, "invalid JSON", "invalidSyntax")
    email = doc.get("userName") if isinstance(doc, dict) else None
    if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
        return _err(400, "userName must be an email address", "invalidValue")
    email = email.strip().lower()
    role, bad = _requested_role(doc)
    if bad:
        return bad
    name = str(
        doc.get("displayName") or doc.get("name", {}).get("formatted") or email.split("@")[0]
    )[:200]
    async with request.app.state.session_factory() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, name=name)
            db.add(user)
            await db.flush()
        existing = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == team_id, TeamMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _err(409, "user already exists in this team", "uniqueness")
        member = TeamMember(
            team_id=team_id, user_id=user.id, display_name=name, role=role or "viewer"
        )
        db.add(member)
        await db.commit()
        await db.refresh(member)
        await emit_identity_event(
            db,
            "scim.user_provisioned",
            actor_user_id=None,
            team_id=team_id,  # type: ignore[arg-type]
            target_type="user",
            target_id=user.id,
            detail={"role": _role_value(member.role)},
        )
        return _ok(_resource(user, member), 201)


@router.patch("/scim/v2/Users/{user_id}")
async def patch_user(user_id: str, request: Request):
    team_id = await _team_of(request)
    if team_id is None:
        return _err(401, "invalid SCIM token")
    try:
        doc = await request.json()
        ops = doc["Operations"]
    except (ValueError, KeyError, TypeError):
        return _err(400, "Operations required", "invalidSyntax")
    async with request.app.state.session_factory() as db:
        found = await _member(db, team_id, user_id)
        if found is None:
            return _err(404, "user not found")
        user, member = found
        want_active: bool | None = None
        new_role: str | None = None
        new_name: str | None = None
        for op in ops if isinstance(ops, list) else []:
            if not isinstance(op, dict) or str(op.get("op", "")).lower() not in ("replace", "add"):
                return _err(400, "only replace/add operations are supported", "invalidSyntax")
            path, value = op.get("path"), op.get("value")
            if path is None and isinstance(value, dict):  # whole-object form
                items = list(value.items())
            else:
                items = [(path, value)]
            for k, v in items:
                if k == "active":
                    if isinstance(v, str):
                        v = v.lower() == "true"
                    want_active = bool(v)
                elif k == "roles":
                    r, bad = _requested_role(
                        {"roles": v if isinstance(v, list) else [{"value": v}]}
                    )
                    if bad:
                        return bad
                    new_role = r
                elif k == "displayName" and isinstance(v, str):
                    new_name = v[:200]
        if new_role is not None and _role_value(member.role) == "admin":
            return _err(403, "SCIM cannot change an admin's role", "mutability")
        if new_role is not None:
            member.role = new_role
        if new_name is not None:
            member.display_name = new_name
        if want_active is False:
            err = await _deactivate(db, team_id, user, member)
            if err:
                await db.rollback()
                return err
        elif want_active is True and member.deactivated_at is not None:
            member.deactivated_at = None
            await emit_identity_event(
                db,
                "scim.user_provisioned",
                actor_user_id=None,
                team_id=team_id,  # type: ignore[arg-type]
                target_type="user",
                target_id=user.id,
                detail={"reactivated": True},
            )
        await db.commit()
        await db.refresh(member)
        return _ok(_resource(user, member))


@router.delete("/scim/v2/Users/{user_id}")
async def delete_user(user_id: str, request: Request):
    team_id = await _team_of(request)
    if team_id is None:
        return _err(401, "invalid SCIM token")
    async with request.app.state.session_factory() as db:
        found = await _member(db, team_id, user_id)
        if found is None:
            return _err(404, "user not found")
        err = await _deactivate(db, team_id, *found)
        if err:
            return err
    return Response(status_code=204)
