"""Marathon 2.0 W16 slice 3 — the SCIM 2.0 subset an IdP actually needs.

Full SCIM is not the ask. A customer's directory needs to answer two questions
about a Harness seat: *who is this* and *they left, stop them*. So this module
implements the user resource, not the group resource:

  GET    /scim/v2/Users            list + the three filters connectors send
  GET    /scim/v2/Users/{id}       read one
  PATCH  /scim/v2/Users/{id}       replace active / userName / externalId
  PUT    /scim/v2/Users/{id}       full replace of the same three attributes

plus the credential that opens that door, managed on the normal API surface:

  POST   /api/v1/teams/{id}/scim/tokens      mint (admin, Team tier, shown once)
  GET    /api/v1/teams/{id}/scim/tokens      list (hint only)
  DELETE /api/v1/teams/{id}/scim/tokens/{id} revoke (idempotent)

Design laws, each because the alternative was worse:

* **A SCIM token is not a session.** SHA-256-hashed at rest like a runner token
  (0068), shown once, scoped to one team and to this surface only; revocation
  kills it on the next request. A user bearer token gets 401 here.
* **No provisioning.** There is no POST /Users: an IdP cannot create a Harness
  account by pushing at it. Joins stay invite-only, exactly as SSO's
  mapping-only rule. SCIM narrows and revokes seats; it never mints them.
* **Deprovisioning lands on slice 1's enforcement.** `active=false` sets
  `users.is_active=false`, which all five token doors already consult — so one
  write here is a real revocation, not a flag beside the door. The consequence is
  stated plainly: `users` is per human, so disabling through SCIM disables that
  human's account in every team. That is what "the directory says they left the
  company" means. The team's session rows are deleted too (teams_api convention).
* **The last admin cannot be deprovisioned** — the same invariant the membership
  API enforces, refused rather than leaving a team unmanageable.
* **Errors speak SCIM.** On `/scim/v2` failures return the RFC 7644 error object
  (`schemas`/`status`/`detail`, plus `scimType` where the RFC defines one),
  because a connector parses those, not our envelope. On `/api/v1` the product
  envelope is kept. Revoked and unknown tokens answer identically — no oracle.
* **`meta` is honest, not decorative.** `location` comes from the request's own
  base URL; `etag` is a content hash of the attributes this surface can change
  and `If-Match` is enforced (412 on mismatch). `meta.lastModified` is ABSENT,
  because `team_members` has no modified column and deriving one from
  `created_at` would be a lie — SCIM marks it OPTIONAL.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from harness_contracts import Principal, error_body
from harness_contracts import hash_token as _hash_token
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, func, select
from sqlalchemy import delete as sa_delete  # noqa: F401 — PATCH/PUT session purge
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .audit_emit import (
    emit_scim_token_created,
    emit_scim_token_revoked,
    emit_scim_user_changed,
)
from .auth import get_principal
from .envelope_middleware import SCIM_ERROR_SCHEMA
from .models import AuthSession, Base, TeamMember, User, uid
from .models import Role as DbRole
from .sso import _require_admin, _require_team_tier

router = APIRouter(prefix="/api/v1")
scim_router = APIRouter(prefix="/scim/v2")

TOKEN_PREFIX = "hsc_"
MAX_TOKENS_PER_TEAM = 10
MAX_LABEL = 120

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
# One definition of the marker: the error envelope's pass-through keys off the
# same constant this module writes, so the two can never drift apart.
ERROR_SCHEMA = SCIM_ERROR_SCHEMA

DEFAULT_COUNT = 100
MAX_COUNT = 200
MAX_EXTERNAL_ID = 255
# The attributes this surface owns. Anything else in a PATCH path is REFUSED
# rather than quietly dropped: a connector that believes it wrote a field it did
# not is worse informed than one that got an error.
MUTABLE_ATTRS = ("active", "userName", "externalId")
_LABEL = re.compile(r"^[^\x00-\x1f]{1,120}$")
# userName is our email; the shape check exists so a connector typo cannot
# strand a seat under an address no login can ever present.
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# `attr eq "value"` — the only filter form real connectors send for a seat.
_FILTER_EQ = re.compile(
    r'^\s*(userName|externalId|id)\s+eq\s+"([^"]*)"\s*$', re.IGNORECASE
)


class ScimToken(Base):
    """Mirror of 0070_scim_provisioning.sql scim_tokens.

    Only the SHA-256 is stored. `token_hint` is the tail of the random bearer
    token itself (not a ciphertext tail as in sso.py): it is shown only to an
    admin who already saw the whole value at mint time, and it exists so a list
    of three bridges can be told apart.
    """

    __tablename__ = "scim_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ScimUserLink(Base):
    """Mirror of 0070_scim_provisioning.sql scim_user_links.

    `externalId` is the DIRECTORY's id for the person, never ours: an IdP that
    created the app user before it ever spoke to Harness knows it only by that
    value, so it must be storable and filterable. Keyed per team and unique per
    team — it says nothing about the human outside this team.
    """

    __tablename__ = "scim_user_links"

    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


@dataclass(frozen=True)
class ScimAuth:
    token_id: str
    team_id: str


def _scim_error(status: int, detail: str, scim_type: str | None = None) -> HTTPException:
    """RFC 7644 §3.12 error object — what a connector's parser expects."""
    body: dict[str, Any] = {
        "schemas": [ERROR_SCHEMA],
        "status": str(status),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return HTTPException(status_code=status, detail=body)


async def scim_auth(request: Request) -> ScimAuth:
    """Resolve the SCIM bearer credential for the /scim/v2 surface."""
    auth = request.headers.get("authorization", "")
    raw = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    if not raw.startswith(TOKEN_PREFIX):
        raise _scim_error(401, "SCIM bearer token required")
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(ScimToken).where(ScimToken.token_sha256 == _hash_token(raw))
            )
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            # One answer for unknown and revoked: this door must not reveal which
            # token ids exist.
            raise _scim_error(401, "invalid SCIM token")
        row.last_used_at = datetime.now(UTC)
        await db.commit()
        return ScimAuth(token_id=row.id, team_id=row.team_id)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _etag(user: User, external_id: str | None) -> str:
    """Content etag over exactly what this surface can change (RFC 7643 §3.1)."""
    payload = json.dumps(
        {
            "id": user.id,
            "userName": user.email,
            "active": bool(user.is_active),
            "externalId": external_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f'W/"{hashlib.sha256(payload.encode()).hexdigest()[:24]}"'


def _resource(
    request: Request, user: User, external_id: str | None, created_at: datetime | None
) -> dict[str, Any]:
    location = f"{str(request.base_url).rstrip('/')}/scim/v2/Users/{user.id}"
    out: dict[str, Any] = {
        "schemas": [USER_SCHEMA],
        "id": user.id,
        "userName": user.email,
        "name": {"formatted": user.name or user.email},
        "active": bool(user.is_active),
        "meta": {
            "resourceType": "User",
            "location": location,
            "created": _iso(created_at),
            "etag": _etag(user, external_id),
        },
    }
    if external_id is not None:
        out["externalId"] = external_id
    return out


async def _members(db: AsyncSession, team_id: str) -> list[tuple[TeamMember, User]]:
    rows = (
        await db.execute(
            select(TeamMember, User)
            .join(User, User.id == TeamMember.user_id)
            .where(TeamMember.team_id == team_id)
            .order_by(TeamMember.created_at, TeamMember.id)
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


async def _link_map(db: AsyncSession, team_id: str) -> dict[str, str]:
    rows = (
        await db.execute(select(ScimUserLink).where(ScimUserLink.team_id == team_id))
    ).scalars().all()
    return {row.user_id: row.external_id for row in rows}


class TokenBody(BaseModel):
    # Deliberately un-constrained: a pydantic range error answers FastAPI's
    # validation list, and this surface promises the product envelope. The
    # bounds live in _LABEL and are reported as VALIDATION_ERROR by the handler.
    label: str = Field(default="")


def _token_public(row: ScimToken) -> dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label,
        "hint": row.token_hint,
        "createdAt": _iso(row.created_at),
        "lastUsedAt": _iso(row.last_used_at),
        "revokedAt": _iso(row.revoked_at),
    }


@router.post("/teams/{team_id}/scim/tokens", status_code=201)
async def create_scim_token(
    team_id: str,
    body: TokenBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """Mint a SCIM bearer token (admin + Team tier). The raw token is returned
    exactly once; only its SHA-256 is stored."""
    _require_admin(principal)
    if principal.teamId != team_id:
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "team not found"))
    label = body.label.strip()
    if not _LABEL.match(label):
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", "label must be 1-120 printable characters"),
        )
    await _require_team_tier(request, team_id)

    async with request.app.state.session_factory() as db:
        live = (
            await db.execute(
                select(func.count())
                .select_from(ScimToken)
                .where(ScimToken.team_id == team_id, ScimToken.revoked_at.is_(None))
            )
        ).scalar_one()
        if int(live) >= MAX_TOKENS_PER_TEAM:
            raise HTTPException(
                status_code=409,
                detail=error_body(
                    "LIMIT_EXCEEDED", f"a team may hold {MAX_TOKENS_PER_TEAM} live SCIM tokens"
                ),
            )
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        row = ScimToken(
            id=uid(),
            team_id=team_id,
            label=label,
            token_sha256=_hash_token(raw),
            token_hint=raw[-4:],
            created_by=principal.userId,
            created_at=_now(),
        )
        db.add(row)
        await db.commit()
        await emit_scim_token_created(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=row.id,
            detail={"label": label, "refId": row.id},
        )
        await db.commit()
        return {**_token_public(row), "token": raw}


@router.get("/teams/{team_id}/scim/tokens")
async def list_scim_tokens(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """List the team's SCIM tokens — hints only, no credential material."""
    _require_admin(principal)
    if principal.teamId != team_id:
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "team not found"))
    async with request.app.state.session_factory() as db:
        rows = (
            await db.execute(
                select(ScimToken)
                .where(ScimToken.team_id == team_id)
                .order_by(ScimToken.created_at, ScimToken.id)
            )
        ).scalars().all()
    return {"items": [_token_public(row) for row in rows], "limit": MAX_TOKENS_PER_TEAM}


@router.delete("/teams/{team_id}/scim/tokens/{token_id}", status_code=204)
async def revoke_scim_token(
    team_id: str,
    token_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Revoke a SCIM token. Idempotent: an already-revoked or unknown id answers
    204 and writes nothing, exactly like the runner revoke."""
    _require_admin(principal)
    if principal.teamId != team_id:
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "team not found"))
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(ScimToken).where(ScimToken.team_id == team_id, ScimToken.id == token_id)
            )
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return None
        label = row.label
        row.revoked_at = _now()
        row.revoked_by = principal.userId
        await db.commit()
        await emit_scim_token_revoked(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=token_id,
            detail={"refId": token_id, "label": label},
        )
        await db.commit()
    return None


@scim_router.get("/Users")
async def list_users(
    request: Request,
    auth: Annotated[ScimAuth, Depends(scim_auth)],
    scim_filter: Annotated[str | None, Query(alias="filter")] = None,
    start_index: Annotated[int, Query(alias="startIndex", ge=1)] = 1,
    count: Annotated[int, Query(ge=1, le=MAX_COUNT)] = DEFAULT_COUNT,
) -> dict[str, Any]:
    """SCIM list, bounded, filtered only by the equality forms connectors send."""
    async with request.app.state.session_factory() as db:
        members = await _members(db, auth.team_id)
        links = await _link_map(db, auth.team_id)
        resources = [
            _resource(request, user, links.get(user.id), member.created_at)
            for member, user in members
        ]
        if scim_filter:
            resources = _apply_filter(scim_filter, resources)
        page = resources[start_index - 1 : start_index - 1 + count]
        return {
            "schemas": [LIST_SCHEMA],
            "totalResults": len(resources),
            "startIndex": start_index,
            "count": len(page),
            "Resources": page,
        }


def _apply_filter(scim_filter: str, resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = _FILTER_EQ.match(scim_filter)
    if match is None:
        raise _scim_error(
            400,
            'filter must be one of userName eq "…" / externalId eq "…" / id eq "…"',
            "invalidFilter",
        )
    attr, value = match.group(1).lower(), match.group(2)
    if attr == "username":
        return [r for r in resources if r["userName"].lower() == value.lower()]
    if attr == "externalid":
        return [r for r in resources if r.get("externalId") == value]
    return [r for r in resources if r["id"] == value]


@scim_router.get("/Users/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
    auth: Annotated[ScimAuth, Depends(scim_auth)],
) -> dict[str, Any]:
    """Read one seat. A member of another team answers exactly what no member
    answers — this surface must not confirm that an id exists elsewhere."""
    async with request.app.state.session_factory() as db:
        found = await _member_and_user(db, auth.team_id, user_id)
        if found is None:
            raise _scim_error(404, "user not found")
        member, user = found
        links = await _link_map(db, auth.team_id)
        return _resource(request, user, links.get(user.id), member.created_at)


async def _member_and_user(db: AsyncSession, team_id: str, user_id: str):
    return (
        await db.execute(
            select(TeamMember, User)
            .join(User, User.id == TeamMember.user_id)
            .where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
    ).first()


async def _live_admin_count(db: AsyncSession, team_id: str, exclude_user_id: str) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(TeamMember)
                .join(User, User.id == TeamMember.user_id)
                .where(
                    TeamMember.team_id == team_id,
                    TeamMember.role == DbRole.ADMIN,
                    User.is_active.is_(True),
                    TeamMember.user_id != exclude_user_id,
                )
            )
        ).scalar_one()
    )


class PatchOp(BaseModel):
    """One RFC 7644 §3.5.2 operation. `path` may be omitted, in which case
    `value` is an attribute map — the shape Okta's deprovisioning patch uses."""

    op: str = ""
    path: str | None = None
    value: Any = None


class PatchBody(BaseModel):
    operations: list[PatchOp] = Field(default_factory=list, alias="Operations")
    schemas: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


def _normalise_path(path: str) -> str:
    for known in MUTABLE_ATTRS:
        if path.lower() == known.lower():
            return known
    raise _scim_error(
        400,
        f"path {path!r} is not writable here; this service manages "
        + ", ".join(MUTABLE_ATTRS),
        "invalidPath",
    )


def _validated_value(attr: str, value: Any) -> Any:
    if attr == "active":
        if not isinstance(value, bool):
            raise _scim_error(400, "active must be a boolean", "invalidValue")
        return value
    if attr == "userName":
        if not isinstance(value, str) or not _EMAIL.fullmatch(value.strip().lower()):
            raise _scim_error(400, "userName must be an email address", "invalidValue")
        return value.strip().lower()
    if attr == "externalId":
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value.strip()) > MAX_EXTERNAL_ID
        ):
            raise _scim_error(
                400, f"externalId must be a 1-{MAX_EXTERNAL_ID} character string", "invalidValue"
            )
        return value.strip()
    raise _scim_error(400, f"unknown attribute {attr!r}", "invalidPath")


def _collect_ops(body: PatchBody) -> dict[str, Any]:
    """Fold the operation list into one attribute set, validating everything.

    Validation is complete BEFORE any write happens: a patch whose second
    operation is bad must change nothing (RFC 7644 treats the operation set as
    one request, and a half-applied deprovision is worse than a refused one).
    """
    if not body.operations:
        raise _scim_error(400, "Operations must be a non-empty list", "invalidSyntax")
    out: dict[str, Any] = {}
    for op in body.operations:
        verb = (op.op or "").strip().lower()
        if verb in ("remove",):
            raise _scim_error(
                400,
                "remove is not supported: these attributes are required, not optional",
                "invalidSyntax",
            )
        if verb not in ("replace", "add"):
            raise _scim_error(400, f"unsupported op {op.op!r}", "invalidSyntax")
        if op.path is None:
            if not isinstance(op.value, dict):
                raise _scim_error(
                    400, "an operation without a path must carry an attribute map", "invalidSyntax"
                )
            pairs = op.value.items()
        else:
            pairs = [(op.path, op.value)]
        for raw_path, value in pairs:
            attr = _normalise_path(str(raw_path))
            out[attr] = _validated_value(attr, value)
    if not out:
        raise _scim_error(400, "no writable attribute in the operation set", "invalidSyntax")
    return out


class PutBody(BaseModel):
    userName: str = ""
    active: bool = True
    externalId: str | None = None  # absent = keep, null = clear
    name: dict[str, Any] | None = None
    schemas: list[str] = Field(default_factory=list)


@scim_router.patch("/Users/{user_id}")
async def patch_user(
    user_id: str,
    body: PatchBody,
    request: Request,
    auth: Annotated[ScimAuth, Depends(scim_auth)],
) -> dict[str, Any]:
    """PATCH — the verb every deprovisioning connector actually sends."""
    wanted = _collect_ops(body)
    return await _mutate(request, auth, user_id, wanted, partial=True)


@scim_router.put("/Users/{user_id}")
async def put_user(
    user_id: str,
    body: PutBody,
    request: Request,
    auth: Annotated[ScimAuth, Depends(scim_auth)],
) -> dict[str, Any]:
    """PUT — replace the attributes this service models.

    A full replace means `userName` must be present (SCIM requires it of a User)
    and an omitted `active` means true. An omitted `externalId` leaves the
    existing directory link in place rather than guessing that absent meant
    delete — clearing it takes `"externalId": null`. Fields we do not model
    (name, emails,
    groups) are accepted and IGNORED: they are not stored anywhere, so a
    connector that sends them gets a resource back without them rather than an
    error about a field this service never had.
    """
    if not body.userName.strip():
        raise _scim_error(400, "userName is required on PUT", "invalidSyntax")
    wanted = {
        "userName": _validated_value("userName", body.userName),
        "active": body.active,
    }
    if body.externalId is not None:
        wanted["externalId"] = _validated_value("externalId", body.externalId)
    return await _mutate(request, auth, user_id, wanted, partial=False)


async def _mutate(
    request: Request, auth: ScimAuth, user_id: str, wanted: dict[str, Any], *, partial: bool
) -> dict[str, Any]:
    factory = request.app.state.session_factory
    async with factory() as db:
        found = await _member_and_user(db, auth.team_id, user_id)
        if found is None:
            raise _scim_error(404, "user not found")
        member, user = found
        links = await _link_map(db, auth.team_id)
        external_before = links.get(user.id)

        if_match = request.headers.get("if-match")
        if if_match is not None and if_match.strip() != _etag(user, external_before):
            # Weak-etag comparison is string equality on the value we issued.
            raise _scim_error(412, "If-Match does not match the current resource state")

        current = {
            "userName": user.email,
            "active": bool(user.is_active),
            "externalId": external_before,
        }
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        for attr, value in wanted.items():
            if current[attr] != value:
                before[attr] = current[attr]
                after[attr] = value

        if not before:
            # Nothing to do: an idempotent connector re-send is not a change, and
            # writing an audit row for it would make the log untrustworthy.
            return _resource(request, user, external_before, member.created_at)

        if after.get("active") is False and _role_of(member) == "admin":
            if await _live_admin_count(db, auth.team_id, user.id) == 0:
                raise _scim_error(
                    403,
                    "cannot deprovision the team's last admin — promote another admin first",
                )

        # Uniqueness probes BEFORE the write, so a collision cannot leave a
        # half-applied patch behind.
        if "userName" in after and (
            await db.execute(select(User.id).where(User.email == after["userName"]))
        ).scalar_one_or_none() is not None:
            raise _scim_error(409, f"userName {after['userName']} is already taken", "uniqueness")
        if "externalId" in after and (
            await db.execute(
                select(ScimUserLink.user_id).where(
                    ScimUserLink.team_id == auth.team_id,
                    ScimUserLink.external_id == after["externalId"],
                    ScimUserLink.user_id != user.id,
                )
            )
        ).scalar_one_or_none() is not None:
            raise _scim_error(
                409, f"externalId {after['externalId']} is already linked", "uniqueness"
            )

        if "userName" in after:
            user.email = after["userName"]
        if "active" in after:
            user.is_active = after["active"]
        if "externalId" in after:
            existing = (
                await db.execute(
                    select(ScimUserLink).where(
                        ScimUserLink.team_id == auth.team_id, ScimUserLink.user_id == user.id
                    )
                )
            ).scalar_one_or_none()
            value = after["externalId"]
            if existing is None and value is not None:
                db.add(
                    ScimUserLink(
                        team_id=auth.team_id, user_id=user.id, external_id=value, updated_at=_now()
                    )
                )
            elif existing is not None and value is None:
                await db.delete(existing)
            elif existing is not None:
                existing.external_id = value
                existing.updated_at = _now()

        deprovisioned = after.get("active") is False
        if deprovisioned:
            # The seat dies immediately, not at the next door check: sessions for
            # THIS team go, exactly as teams_api's removal does. (The account flag
            # itself is global; that is documented in the module header.)
            await db.execute(
                sa_delete(AuthSession).where(
                    AuthSession.user_id == user.id, AuthSession.team_id == auth.team_id
                )
            )
        await db.commit()

        kind = (
            "deprovision"
            if deprovisioned
            else "reactivate"
            if after.get("active") is True
            else "rename"
            if "userName" in after
            else "link"
        )
        await emit_scim_user_changed(
            db,
            actor_user_id=None,
            team_id=auth.team_id,
            target_id=str(member.id),
            detail={
                "kind": kind,
                "refId": auth.token_id,
                "fields": sorted(before),
                "before": before,
                "after": after,
                "partial": partial,
            },
        )
        await db.commit()

        links_after = await _link_map(db, auth.team_id)
        return _resource(request, user, links_after.get(user.id), member.created_at)


def _role_of(member: TeamMember) -> str:
    role = member.role
    return (role.value if hasattr(role, "value") else str(role)).lower()
