"""Part 37 (Session 3, lane O): organizations, org roles, invitations, guests, service identities.

Tables come from migration 0038 and are used through SQLAlchemy Core text() SQL (no ORM
models, models.py is frozen this session). Every route is deny by default: a caller who is
not an active, unexpired member of the org gets 404 (no existence oracle), a member without
the needed role gets 403. Every consequential change writes an audit row (target_type "org",
target_id = org id) so GET /orgs/{id}/history can replay it with before/after.

Lane P imports ``org_role``. The lead mounts ``router`` in app.py.
"""

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, error_body, hash_token
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import write_audit
from .auth import get_principal
from .models import AuditLog, uid

router = APIRouter(prefix="/api/v1/orgs")

ROLES = ("owner", "admin", "billing_admin", "member", "guest")
INVITE_ROLES = ("admin", "billing_admin", "member", "guest")
MANAGER_ROLES = ("owner", "admin")
INVITE_TTL_DAYS = 7
GUEST_DEFAULT_DAYS = 7
GUEST_MAX_DAYS = 90
# Closed set: a service identity can only ever hold these. No owner/admin/org scope exists.
SERVICE_SCOPES = frozenset(
    {"runs:read", "runs:write", "artifacts:read", "members:read", "audit:read", "webhooks:send"}
)
HISTORY_ACTIONS = (
    "org.created",
    "org.updated",
    "org.member_added",
    "org.member_role_changed",
    "org.member_removed",
    "org.team_linked",
    "org.guest_added",
    "org.guest_expired",
    "org.domain_policy_changed",
    "org.service_identity_created",
    "org.service_identity_revoked",
    "invite.minted",
    "invite.redeemed",
    "invite.revoked",
)
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")
_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _now() -> datetime:
    return datetime.now(UTC)


def _dt(v: Any) -> datetime | None:
    """SQLite hands text() timestamps back as str, Postgres as aware datetimes."""
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v if v.tzinfo else v.replace(tzinfo=UTC)


def _iso(v: Any) -> str | None:
    d = _dt(v)
    return d.isoformat() if d else None


def _fail(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail=error_body(code, msg))


def _not_found() -> HTTPException:
    return _fail(404, "NOT_FOUND", "organization not found")


async def _audit(
    db: AsyncSession, action: str, org_id: str, actor: str | None, detail: dict, kind: str = "human"
) -> None:
    """Commits the session (atomic with the pending mutation). Actor user id lives in detail
    because audit_log.actor_member_id points at team_members, which org actors may lack."""
    await write_audit(
        db, action, None, kind, "org", org_id, {"orgId": org_id, "actorUserId": actor, **detail}
    )


# ---------------------------------------------------------------- access revocation


async def _revoke_access(db: AsyncSession, org_id: str, user_id: str, *, everywhere: bool = False):
    """Kill sessions, team seats and service identities a user holds through an org.

    Not committed: the caller's audit write commits it together with the membership change.
    Team seats referenced by an audit row cannot be deleted (append-only audit FK), their
    sessions are still revoked.
    """
    scope = "" if everywhere else " AND team_id IN (SELECT id FROM teams WHERE org_id = :o)"
    params = {"u": user_id, "o": org_id}
    await db.execute(text(f"DELETE FROM auth_sessions WHERE user_id = :u{scope}"), params)
    await db.execute(
        text(
            f"DELETE FROM team_members WHERE user_id = :u{scope}"
            " AND id NOT IN (SELECT actor_member_id FROM audit_log"
            " WHERE actor_member_id IS NOT NULL)"
        ),
        params,
    )
    own = "" if everywhere else " AND org_id = :o"
    await db.execute(
        text(
            "UPDATE org_service_identities SET revoked_at = :n"
            f" WHERE created_by = :u AND revoked_at IS NULL{own}"
        ),
        {**params, "n": _now()},
    )


async def _sweep(db: AsyncSession, *, org_id: str | None = None, user_id: str | None = None):
    """Lazily expire guests whose expires_at has passed (runs on every membership read)."""
    q = (
        "SELECT org_id, user_id FROM org_members"
        " WHERE removed_at IS NULL AND expires_at IS NOT NULL"
    )
    params: dict[str, Any] = {}
    if org_id:
        q += " AND org_id = :o"
        params["o"] = org_id
    if user_id:
        q += " AND user_id = :u"
        params["u"] = user_id
    now = _now()
    rows = (await db.execute(text(q + " AND expires_at <= :n"), {**params, "n": now})).all()
    for o, u in rows:
        res = await db.execute(
            text(
                "UPDATE org_members SET removed_at = :n WHERE org_id = :o AND user_id = :u"
                " AND removed_at IS NULL AND expires_at <= :n"
            ),
            {"n": now, "o": o, "u": u},
        )
        if res.rowcount != 1:
            await db.rollback()
            continue
        await _revoke_access(db, o, u)
        await _audit(
            db,
            "org.guest_expired",
            o,
            None,
            {
                "memberUserId": u,
                "before": {"role": "guest", "active": True},
                "after": {"role": None, "active": False},
            },
            kind="system",
        )


async def org_role(db: AsyncSession, org_id: str, user_id: str) -> str | None:
    """The user's active role in the org, or None (not a member, removed, expired guest,
    or org not active). Expires due guests as a side effect (audited, committed)."""
    await _sweep(db, org_id=org_id, user_id=user_id)
    row = (
        await db.execute(
            text(
                "SELECT m.role FROM org_members m JOIN organizations o ON o.id = m.org_id"
                " WHERE m.org_id = :o AND m.user_id = :u AND m.removed_at IS NULL"
                " AND o.status = 'active'"
            ),
            {"o": org_id, "u": user_id},
        )
    ).first()
    return row[0] if row else None


async def mark_session_assurance(
    db: AsyncSession, token_hash: str, *, sso: bool = False, mfa: bool = False
) -> None:
    """Hook for the SSO / MFA completion path: record how this session authenticated."""
    await db.execute(
        text("DELETE FROM org_session_assurance WHERE token_hash = :h"), {"h": token_hash}
    )
    await db.execute(
        text(
            "INSERT INTO org_session_assurance (token_hash, sso, mfa, created_at)"
            " VALUES (:h, :s, :m, :n)"
        ),
        {"h": token_hash, "s": sso, "m": mfa, "n": _now()},
    )
    await db.commit()


async def _authz(
    db: AsyncSession,
    request: Request,
    principal: Principal,
    org_id: str,
    roles: tuple[str, ...] | None = None,
    *,
    enforce_assurance: bool = True,
) -> str:
    role = await org_role(db, org_id, principal.userId)
    if role is None:
        raise _not_found()
    if roles is not None and role not in roles:
        raise _fail(403, "FORBIDDEN", "insufficient organization role")
    if enforce_assurance:
        flags = (
            await db.execute(
                text("SELECT require_sso, require_mfa FROM organizations WHERE id = :o"),
                {"o": org_id},
            )
        ).one()
        if flags[0] or flags[1]:
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            got = (
                await db.execute(
                    text("SELECT sso, mfa FROM org_session_assurance WHERE token_hash = :h"),
                    {"h": hash_token(token)},
                )
            ).first()
            if flags[0] and not (got and got[0]):
                raise _fail(403, "SSO_REQUIRED", "this organization requires an SSO session")
            if flags[1] and not (got and got[1]):
                raise _fail(403, "MFA_REQUIRED", "this organization requires an MFA session")
    return role


async def _lock_org(db: AsyncSession, org_id: str) -> None:
    """Row lock (Postgres) / write lock (SQLite) so owner-count checks serialize."""
    await db.execute(text("UPDATE organizations SET status = status WHERE id = :o"), {"o": org_id})


async def _owner_count(db: AsyncSession, org_id: str) -> int:
    return (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM org_members WHERE org_id = :o AND role = 'owner'"
                " AND removed_at IS NULL"
            ),
            {"o": org_id},
        )
    ).scalar_one()


def _domains(raw: Any) -> list[str]:
    return json.loads(raw) if isinstance(raw, str) else list(raw or [])


def _domain_ok(email: str, allowed: list[str]) -> bool:
    return not allowed or email.rsplit("@", 1)[-1].lower() in allowed


async def _org_public(db: AsyncSession, org_id: str) -> dict:
    r = (
        await db.execute(
            text(
                "SELECT id, name, slug, status, require_sso, require_mfa, allowed_domains,"
                " created_at FROM organizations WHERE id = :o"
            ),
            {"o": org_id},
        )
    ).one()
    return {
        "id": r[0],
        "name": r[1],
        "slug": r[2],
        "status": r[3],
        "requireSso": bool(r[4]),
        "requireMfa": bool(r[5]),
        "allowedDomains": _domains(r[6]),
        "createdAt": _iso(r[7]),
    }


# ---------------------------------------------------------------- bodies


class OrgCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(pattern=_SLUG.pattern)


class OrgPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    allowedDomains: list[str] | None = None
    requireSso: bool | None = None
    requireMfa: bool | None = None


class InviteBody(BaseModel):
    email: str = Field(pattern=_EMAIL)
    role: str = Field(pattern="^(admin|billing_admin|member|guest)$")
    guestDays: int | None = Field(default=None, ge=1, le=GUEST_MAX_DAYS)


class AcceptBody(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class RoleBody(BaseModel):
    role: str = Field(pattern="^(owner|admin|billing_admin|member|guest)$")
    guestDays: int | None = Field(default=None, ge=1, le=GUEST_MAX_DAYS)


class ServiceIdentityBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1)
    expiresInDays: int | None = Field(default=None, ge=1, le=365)


class LinkTeamBody(BaseModel):
    teamId: str


# ---------------------------------------------------------------- static routes first


@router.post("/invites/accept")
async def accept_invite(
    body: AcceptBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Redeem an invite. Bound to the invited mailbox: a mismatched caller sees plain 404."""
    async with request.app.state.session_factory() as db:
        inv = (
            await db.execute(
                text(
                    "SELECT id, org_id, email, role, guest_days, expires_at, redeemed_at,"
                    " revoked_at FROM org_invites WHERE token_hash = :h"
                ),
                {"h": hash_token(body.token.strip())},
            )
        ).first()
        email = (
            await db.execute(text("SELECT email FROM users WHERE id = :u"), {"u": principal.userId})
        ).scalar_one_or_none()
        if inv is None or email is None or email.lower() != inv[2]:
            raise _fail(404, "NOT_FOUND", "invite not found")
        inv_id, org_id, _, role, guest_days, exp, redeemed, revoked = inv
        if revoked is not None:
            raise _fail(410, "INVITE_REVOKED", "invite was revoked")
        if redeemed is not None:
            raise _fail(409, "INVITE_REDEEMED", "invite already used")
        if _now() >= _dt(exp):
            raise _fail(410, "INVITE_EXPIRED", "invite expired")
        org = (
            await db.execute(
                text("SELECT status, allowed_domains FROM organizations WHERE id = :o"),
                {"o": org_id},
            )
        ).one()
        if org[0] != "active":
            raise _not_found()
        if role != "guest" and not _domain_ok(email, _domains(org[1])):
            raise _fail(403, "DOMAIN_NOT_ALLOWED", "email domain is not allowed by org policy")
        await _lock_org(db, org_id)
        if await org_role(db, org_id, principal.userId) is not None:
            raise _fail(409, "ALREADY_MEMBER", "already a member of this organization")
        won = await db.execute(
            text(
                "UPDATE org_invites SET redeemed_at = :n WHERE id = :i AND redeemed_at IS NULL"
                " AND revoked_at IS NULL"
            ),
            {"n": _now(), "i": inv_id},
        )
        if won.rowcount != 1:
            raise _fail(409, "INVITE_REDEEMED", "invite already used")
        now = _now()
        expires = (
            now + timedelta(days=guest_days or GUEST_DEFAULT_DAYS) if role == "guest" else None
        )
        upd = await db.execute(
            text(
                "UPDATE org_members SET role = :r, expires_at = :e, created_at = :n,"
                " removed_at = NULL WHERE org_id = :o AND user_id = :u"
            ),
            {"r": role, "e": expires, "n": now, "o": org_id, "u": principal.userId},
        )
        if upd.rowcount == 0:
            await db.execute(
                text(
                    "INSERT INTO org_members (org_id, user_id, role, expires_at, created_at)"
                    " VALUES (:o, :u, :r, :e, :n)"
                ),
                {"o": org_id, "u": principal.userId, "r": role, "e": expires, "n": now},
            )
        await write_audit(
            db,
            "invite.redeemed",
            None,
            "human",
            "org",
            org_id,
            {"orgId": org_id, "actorUserId": principal.userId, "inviteId": inv_id},
        )
        await _audit(
            db,
            "org.guest_added" if role == "guest" else "org.member_added",
            org_id,
            principal.userId,
            {
                "memberUserId": principal.userId,
                "inviteId": inv_id,
                "before": None,
                "after": {"role": role, "expiresAt": _iso(expires)},
            },
        )
        return {"orgId": org_id, "role": role, "expiresAt": _iso(expires)}


@router.get("/service/whoami")
async def service_whoami(request: Request) -> dict:
    """Authenticate a service identity token (Bearer hsi_...). Updates last_used_at."""
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    async with request.app.state.session_factory() as db:
        r = (
            await db.execute(
                text(
                    "SELECT s.id, s.org_id, s.name, s.scopes, s.expires_at, s.revoked_at,"
                    " o.status FROM org_service_identities s JOIN organizations o"
                    " ON o.id = s.org_id WHERE s.token_hash = :h"
                ),
                {"h": hash_token(token)},
            )
        ).first()
        if (
            not token
            or r is None
            or r[5] is not None
            or r[6] != "active"
            or (r[4] is not None and _dt(r[4]) <= _now())
        ):
            raise _fail(401, "UNAUTHENTICATED", "invalid service identity token")
        await db.execute(
            text("UPDATE org_service_identities SET last_used_at = :n WHERE id = :i"),
            {"n": _now(), "i": r[0]},
        )
        await db.commit()
        return {"serviceIdentityId": r[0], "orgId": r[1], "name": r[2], "scopes": json.loads(r[3])}


@router.delete("/me/identity", status_code=204)
async def delete_my_identity(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> None:
    """Delete the caller's identity: leave every org, kill every session and the service
    identities they created. Refused (409) while they are the sole owner of any org."""
    async with request.app.state.session_factory() as db:
        mine = (
            await db.execute(
                text(
                    "SELECT org_id, role FROM org_members WHERE user_id = :u AND removed_at IS NULL"
                ),
                {"u": principal.userId},
            )
        ).all()
        blocking = []
        for org_id, role in mine:
            await _lock_org(db, org_id)
            if role == "owner" and await _owner_count(db, org_id) <= 1:
                blocking.append(org_id)
        if blocking:
            await db.rollback()
            raise _fail(409, "LAST_OWNER", f"sole owner of: {', '.join(blocking)}")
        now = _now()
        await db.execute(
            text(
                "UPDATE org_members SET removed_at = :n WHERE user_id = :u AND removed_at IS NULL"
            ),
            {"n": now, "u": principal.userId},
        )
        await _revoke_access(db, "", principal.userId, everywhere=True)
        await db.execute(
            text("UPDATE users SET is_active = :f WHERE id = :u"),
            {"f": False, "u": principal.userId},
        )
        if not mine:
            await db.commit()
        for org_id, role in mine:
            await _audit(
                db,
                "org.member_removed",
                org_id,
                principal.userId,
                {
                    "memberUserId": principal.userId,
                    "reason": "identity_deleted",
                    "before": {"role": role},
                    "after": {"role": None},
                },
            )


# ---------------------------------------------------------------- orgs


@router.post("", status_code=201)
async def create_org(
    body: OrgCreateBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    org_id, now = uid(), _now()
    async with request.app.state.session_factory() as db:
        try:
            await db.execute(
                text(
                    "INSERT INTO organizations (id, name, slug, created_at) VALUES (:i, :n, :s, :c)"
                ),
                {"i": org_id, "n": body.name.strip(), "s": body.slug, "c": now},
            )
        except IntegrityError:
            await db.rollback()
            raise _fail(409, "SLUG_TAKEN", "slug already in use") from None
        await db.execute(
            text(
                "INSERT INTO org_members (org_id, user_id, role, created_at)"
                " VALUES (:o, :u, 'owner', :c)"
            ),
            {"o": org_id, "u": principal.userId, "c": now},
        )
        await _audit(
            db,
            "org.created",
            org_id,
            principal.userId,
            {"before": None, "after": {"name": body.name.strip(), "slug": body.slug}},
        )
        return await _org_public(db, org_id)


@router.get("")
async def list_orgs(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    async with request.app.state.session_factory() as db:
        await _sweep(db, user_id=principal.userId)
        rows = (
            await db.execute(
                text(
                    "SELECT o.id, o.name, o.slug, m.role, m.expires_at FROM org_members m"
                    " JOIN organizations o ON o.id = m.org_id WHERE m.user_id = :u"
                    " AND m.removed_at IS NULL AND o.status = 'active' ORDER BY o.slug"
                ),
                {"u": principal.userId},
            )
        ).all()
    return {
        "items": [
            {"id": r[0], "name": r[1], "slug": r[2], "role": r[3], "expiresAt": _iso(r[4])}
            for r in rows
        ]
    }


@router.get("/{org_id}")
async def get_org(
    org_id: str, request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    async with request.app.state.session_factory() as db:
        role = await _authz(db, request, principal, org_id, enforce_assurance=False)
        return {**await _org_public(db, org_id), "role": role}


@router.patch("/{org_id}")
async def patch_org(
    org_id: str,
    body: OrgPatchBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Name and domain policy: owner or admin. require_sso / require_mfa: owner only, and the
    owner may always change them (break-glass) even from a session that fails the flags."""
    security = body.requireSso is not None or body.requireMfa is not None
    async with request.app.state.session_factory() as db:
        role = await _authz(
            db, request, principal, org_id, MANAGER_ROLES, enforce_assurance=not security
        )
        if security and role != "owner":
            raise _fail(403, "FORBIDDEN", "only an owner can change SSO/MFA requirements")
        before = await _org_public(db, org_id)
        sets, params = [], {"o": org_id}
        if body.name is not None:
            sets.append("name = :name")
            params["name"] = body.name.strip()
        domains = None
        if body.allowedDomains is not None:
            domains = sorted({d.strip().lower().lstrip("@") for d in body.allowedDomains})
            if any(not _DOMAIN.match(d) for d in domains):
                raise _fail(422, "INVALID_DOMAIN", "allowedDomains has an invalid domain")
            sets.append("allowed_domains = :dom")
            params["dom"] = json.dumps(domains)
        for field, col in (("requireSso", "require_sso"), ("requireMfa", "require_mfa")):
            if getattr(body, field) is not None:
                sets.append(f"{col} = :{col}")
                params[col] = getattr(body, field)
        if not sets:
            return before
        await db.execute(text(f"UPDATE organizations SET {', '.join(sets)} WHERE id = :o"), params)
        after_pending = {
            "name": params.get("name", before["name"]),
            "requireSso": params.get("require_sso", before["requireSso"]),
            "requireMfa": params.get("require_mfa", before["requireMfa"]),
        }
        if body.name is not None or security:
            await _audit(
                db,
                "org.updated",
                org_id,
                principal.userId,
                {"before": {k: before[k] for k in after_pending}, "after": after_pending},
            )
        if domains is not None:
            await _audit(
                db,
                "org.domain_policy_changed",
                org_id,
                principal.userId,
                {
                    "before": {"allowedDomains": before["allowedDomains"]},
                    "after": {"allowedDomains": domains},
                },
            )
        return await _org_public(db, org_id)


@router.delete("/{org_id}", status_code=204)
async def delete_org(
    org_id: str, request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> None:
    """Owner only. Marks the org deleted, removes all members, kills every session on its
    teams and revokes every service identity, immediately."""
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, ("owner",))
        now = _now()
        users = [
            r[0]
            for r in (
                await db.execute(
                    text(
                        "SELECT user_id FROM org_members WHERE org_id = :o AND removed_at IS NULL"
                    ),
                    {"o": org_id},
                )
            ).all()
        ]
        await db.execute(
            text("UPDATE organizations SET status = 'deleted' WHERE id = :o"), {"o": org_id}
        )
        await db.execute(
            text("UPDATE org_members SET removed_at = :n WHERE org_id = :o AND removed_at IS NULL"),
            {"n": now, "o": org_id},
        )
        await db.execute(
            text(
                "UPDATE org_service_identities SET revoked_at = :n"
                " WHERE org_id = :o AND revoked_at IS NULL"
            ),
            {"n": now, "o": org_id},
        )
        await db.execute(
            text(
                "UPDATE org_invites SET revoked_at = :n WHERE org_id = :o"
                " AND revoked_at IS NULL AND redeemed_at IS NULL"
            ),
            {"n": now, "o": org_id},
        )
        for u in users:
            await _revoke_access(db, org_id, u)
        await _audit(
            db,
            "org.updated",
            org_id,
            principal.userId,
            {
                "before": {"status": "active"},
                "after": {"status": "deleted"},
                "removedUserIds": users,
            },
        )


# ---------------------------------------------------------------- members


@router.get("/{org_id}/members")
async def list_members(
    org_id: str, request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, ("owner", "admin", "billing_admin", "member"))
        rows = (
            await db.execute(
                text(
                    "SELECT m.user_id, u.email, m.role, m.expires_at, m.created_at"
                    " FROM org_members m"
                    " JOIN users u ON u.id = m.user_id WHERE m.org_id = :o AND m.removed_at IS NULL"
                    " ORDER BY m.created_at"
                ),
                {"o": org_id},
            )
        ).all()
    return {
        "items": [
            {
                "userId": r[0],
                "email": r[1],
                "role": r[2],
                "expiresAt": _iso(r[3]),
                "createdAt": _iso(r[4]),
            }
            for r in rows
        ]
    }


@router.patch("/{org_id}/members/{user_id}")
async def change_role(
    org_id: str,
    user_id: str,
    body: RoleBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Invariants: only an owner touches owners (grant or demote), an admin can never create
    an owner, and an org always keeps at least one owner."""
    async with request.app.state.session_factory() as db:
        actor = await _authz(db, request, principal, org_id, MANAGER_ROLES)
        await _lock_org(db, org_id)
        cur = (
            await db.execute(
                text(
                    "SELECT role FROM org_members WHERE org_id = :o AND user_id = :u"
                    " AND removed_at IS NULL"
                ),
                {"o": org_id, "u": user_id},
            )
        ).first()
        if cur is None:
            raise _fail(404, "NOT_FOUND", "member not found")
        old = cur[0]
        if actor != "owner" and "owner" in (old, body.role):
            raise _fail(403, "FORBIDDEN", "only an owner can grant or change the owner role")
        if old == "owner" and body.role != "owner" and await _owner_count(db, org_id) <= 1:
            raise _fail(409, "LAST_OWNER", "an organization must keep at least one owner")
        expires = (
            _now() + timedelta(days=body.guestDays or GUEST_DEFAULT_DAYS)
            if body.role == "guest"
            else None
        )
        await db.execute(
            text(
                "UPDATE org_members SET role = :r, expires_at = :e"
                " WHERE org_id = :o AND user_id = :u"
            ),
            {"r": body.role, "e": expires, "o": org_id, "u": user_id},
        )
        await _audit(
            db,
            "org.member_role_changed",
            org_id,
            principal.userId,
            {
                "memberUserId": user_id,
                "before": {"role": old},
                "after": {"role": body.role, "expiresAt": _iso(expires)},
            },
        )
        return {"userId": user_id, "role": body.role, "expiresAt": _iso(expires)}


@router.delete("/{org_id}/members/{user_id}", status_code=204)
async def remove_member(
    org_id: str,
    user_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Admins remove non-owners, owners remove anyone, anyone may remove themselves (leave).
    Sessions, team seats and the member's service identities die in the same transaction."""
    async with request.app.state.session_factory() as db:
        actor = await _authz(db, request, principal, org_id)
        if user_id != principal.userId and actor not in MANAGER_ROLES:
            raise _fail(403, "FORBIDDEN", "insufficient organization role")
        await _lock_org(db, org_id)
        cur = (
            await db.execute(
                text(
                    "SELECT role FROM org_members WHERE org_id = :o AND user_id = :u"
                    " AND removed_at IS NULL"
                ),
                {"o": org_id, "u": user_id},
            )
        ).first()
        if cur is None:
            raise _fail(404, "NOT_FOUND", "member not found")
        old = cur[0]
        if old == "owner":
            if actor != "owner":
                raise _fail(403, "FORBIDDEN", "only an owner can remove an owner")
            if await _owner_count(db, org_id) <= 1:
                raise _fail(409, "LAST_OWNER", "an organization must keep at least one owner")
        await db.execute(
            text(
                "UPDATE org_members SET removed_at = :n WHERE org_id = :o AND user_id = :u"
                " AND removed_at IS NULL"
            ),
            {"n": _now(), "o": org_id, "u": user_id},
        )
        await _revoke_access(db, org_id, user_id)
        await _audit(
            db,
            "org.member_removed",
            org_id,
            principal.userId,
            {"memberUserId": user_id, "before": {"role": old}, "after": {"role": None}},
        )


# ---------------------------------------------------------------- invitations


@router.post("/{org_id}/invites", status_code=201)
async def create_invite(
    org_id: str,
    body: InviteBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, MANAGER_ROLES)
        email = body.email.lower().strip()
        allowed = _domains(
            (
                await db.execute(
                    text("SELECT allowed_domains FROM organizations WHERE id = :o"), {"o": org_id}
                )
            ).scalar_one()
        )
        if body.role != "guest" and not _domain_ok(email, allowed):
            raise _fail(403, "DOMAIN_NOT_ALLOWED", "email domain is not allowed by org policy")
        raw, inv_id, now = secrets.token_urlsafe(32), uid(), _now()
        exp = now + timedelta(days=INVITE_TTL_DAYS)
        await db.execute(
            text(
                "INSERT INTO org_invites (id, org_id, email, role, guest_days, token_hash,"
                " expires_at, created_by, created_at) VALUES (:i, :o, :e, :r, :g, :h, :x, :c, :n)"
            ),
            {
                "i": inv_id,
                "o": org_id,
                "e": email,
                "r": body.role,
                "g": body.guestDays,
                "h": hash_token(raw),
                "x": exp,
                "c": principal.userId,
                "n": now,
            },
        )
        await write_audit(
            db,
            "invite.minted",
            None,
            "human",
            "org",
            org_id,
            {
                "orgId": org_id,
                "actorUserId": principal.userId,
                "inviteId": inv_id,
                "email": email,
                "role": body.role,
            },
        )
        return {
            "id": inv_id,
            "email": email,
            "role": body.role,
            "expiresAt": _iso(exp),
            "status": "sent",
            "token": raw,
        }  # raw token appears exactly once


def _invite_status(redeemed: Any, revoked: Any, exp: Any) -> str:
    if revoked is not None:
        return "revoked"
    if redeemed is not None:
        return "redeemed"
    return "expired" if _now() >= _dt(exp) else "sent"


@router.get("/{org_id}/invites")
async def list_invites(
    org_id: str, request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, MANAGER_ROLES)
        rows = (
            await db.execute(
                text(
                    "SELECT id, email, role, expires_at, redeemed_at, revoked_at FROM org_invites"
                    " WHERE org_id = :o ORDER BY created_at DESC"
                ),
                {"o": org_id},
            )
        ).all()
    return {
        "items": [
            {
                "id": r[0],
                "email": r[1],
                "role": r[2],
                "expiresAt": _iso(r[3]),
                "status": _invite_status(r[4], r[5], r[3]),
            }
            for r in rows
        ]
    }


@router.delete("/{org_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    org_id: str,
    invite_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, MANAGER_ROLES)
        res = await db.execute(
            text(
                "UPDATE org_invites SET revoked_at = :n WHERE id = :i AND org_id = :o"
                " AND revoked_at IS NULL AND redeemed_at IS NULL"
            ),
            {"n": _now(), "i": invite_id, "o": org_id},
        )
        if res.rowcount != 1:
            await db.rollback()
            raise _fail(404, "NOT_FOUND", "pending invite not found")
        await write_audit(
            db,
            "invite.revoked",
            None,
            "human",
            "org",
            org_id,
            {"orgId": org_id, "actorUserId": principal.userId, "inviteId": invite_id},
        )


# ---------------------------------------------------------------- teams


@router.post("/{org_id}/teams", status_code=201)
async def link_team(
    org_id: str,
    body: LinkTeamBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Attach a team to the org. Needs org owner/admin AND admin of that team (the caller's
    own session team): you cannot claim a team you do not run. A team joins one org only."""
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, MANAGER_ROLES)
        if principal.teamId != body.teamId or principal.role.value != "admin":
            raise _fail(404, "NOT_FOUND", "team not found")
        res = await db.execute(
            text("UPDATE teams SET org_id = :o WHERE id = :t AND org_id IS NULL"),
            {"o": org_id, "t": body.teamId},
        )
        if res.rowcount != 1:
            await db.rollback()
            raise _fail(409, "TEAM_ALREADY_LINKED", "team already belongs to an organization")
        await _audit(
            db,
            "org.team_linked",
            org_id,
            principal.userId,
            {"teamId": body.teamId, "before": {"orgId": None}, "after": {"orgId": org_id}},
        )
        return {"orgId": org_id, "teamId": body.teamId}


@router.delete("/{org_id}/teams/{team_id}/members/{user_id}", status_code=204)
async def remove_from_team(
    org_id: str,
    team_id: str,
    user_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Team departure: drop the user's seat on one org team and kill their sessions for it."""
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, MANAGER_ROLES)
        params = {"t": team_id, "o": org_id, "u": user_id}
        owns = (
            await db.execute(text("SELECT 1 FROM teams WHERE id = :t AND org_id = :o"), params)
        ).first()
        if owns is None:
            raise _fail(404, "NOT_FOUND", "team not found")
        await db.execute(
            text("DELETE FROM auth_sessions WHERE user_id = :u AND team_id = :t"), params
        )
        await db.execute(
            text(
                "DELETE FROM team_members WHERE user_id = :u AND team_id = :t AND id NOT IN"
                " (SELECT actor_member_id FROM audit_log WHERE actor_member_id IS NOT NULL)"
            ),
            params,
        )
        await _audit(
            db,
            "org.member_removed",
            org_id,
            principal.userId,
            {
                "memberUserId": user_id,
                "teamId": team_id,
                "scope": "team",
                "before": {"teamSeat": True},
                "after": {"teamSeat": False},
            },
        )


# ---------------------------------------------------------------- history


@router.get("/{org_id}/history")
async def org_history(
    org_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    limit: int = 100,
) -> dict:
    """Membership and role changes (with before/after) read straight from the audit log."""
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, MANAGER_ROLES)
        await _sweep(db, org_id=org_id)
        rows = (
            (
                await db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.target_type == "org",
                        AuditLog.target_id == org_id,
                        AuditLog.action.in_(HISTORY_ACTIONS),
                    )
                    .order_by(AuditLog.seq.desc())
                    .limit(max(1, min(limit, 500)))
                )
            )
            .scalars()
            .all()
        )
    return {
        "items": [
            {
                "seq": r.seq,
                "action": r.action,
                "ts": _iso(r.ts),
                "actorKind": r.actor_kind,
                "actorUserId": (r.detail or {}).get("actorUserId"),
                "detail": r.detail,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------- service identities


@router.post("/{org_id}/service-identities", status_code=201)
async def create_service_identity(
    org_id: str,
    body: ServiceIdentityBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, MANAGER_ROLES)
        bad = sorted(set(body.scopes) - SERVICE_SCOPES)
        if bad:
            raise _fail(422, "INVALID_SCOPE", f"scopes not allowed for service identities: {bad}")
        scopes = sorted(set(body.scopes))
        raw, sid, now = "hsi_" + secrets.token_urlsafe(32), uid(), _now()
        exp = now + timedelta(days=body.expiresInDays) if body.expiresInDays else None
        await db.execute(
            text(
                "INSERT INTO org_service_identities (id, org_id, name, token_hash, scopes,"
                " created_by, created_at, expires_at) VALUES (:i, :o, :n, :h, :s, :c, :t, :x)"
            ),
            {
                "i": sid,
                "o": org_id,
                "n": body.name.strip(),
                "h": hash_token(raw),
                "s": json.dumps(scopes),
                "c": principal.userId,
                "t": now,
                "x": exp,
            },
        )
        await _audit(
            db,
            "org.service_identity_created",
            org_id,
            principal.userId,
            {
                "serviceIdentityId": sid,
                "name": body.name.strip(),
                "before": None,
                "after": {"scopes": scopes, "expiresAt": _iso(exp)},
            },
        )
        return {
            "id": sid,
            "name": body.name.strip(),
            "scopes": scopes,
            "expiresAt": _iso(exp),
            "token": raw,
        }  # shown once, only the hash is stored


@router.get("/{org_id}/service-identities")
async def list_service_identities(
    org_id: str, request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, MANAGER_ROLES)
        rows = (
            await db.execute(
                text(
                    "SELECT id, name, scopes, created_at, expires_at, revoked_at, last_used_at"
                    " FROM org_service_identities WHERE org_id = :o ORDER BY created_at"
                ),
                {"o": org_id},
            )
        ).all()
    return {
        "items": [
            {
                "id": r[0],
                "name": r[1],
                "scopes": json.loads(r[2]),
                "createdAt": _iso(r[3]),
                "expiresAt": _iso(r[4]),
                "revokedAt": _iso(r[5]),
                "lastUsedAt": _iso(r[6]),
            }
            for r in rows
        ]
    }


@router.delete("/{org_id}/service-identities/{sid}", status_code=204)
async def revoke_service_identity(
    org_id: str,
    sid: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    async with request.app.state.session_factory() as db:
        await _authz(db, request, principal, org_id, MANAGER_ROLES)
        res = await db.execute(
            text(
                "UPDATE org_service_identities SET revoked_at = :n WHERE id = :i AND org_id = :o"
                " AND revoked_at IS NULL"
            ),
            {"n": _now(), "i": sid, "o": org_id},
        )
        if res.rowcount != 1:
            await db.rollback()
            raise _fail(404, "NOT_FOUND", "service identity not found")
        await _audit(
            db,
            "org.service_identity_revoked",
            org_id,
            principal.userId,
            {"serviceIdentityId": sid, "before": {"revoked": False}, "after": {"revoked": True}},
        )
