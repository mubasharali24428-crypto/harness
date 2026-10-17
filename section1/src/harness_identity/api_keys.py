"""Mission 10 node A1 — BYO-keys CRUD over the frozen api_keys table.

Own router module so nothing else has to touch app.py this mission: expose
`router` (same prefix convention as invites.py) and ATLAS registers it at
merge with `app.include_router(router)`.

Contract M10-C1:
- POST /api/v1/keys {label, provider, key} -> 201
  {id, label, provider, keyHint, createdAt}
- GET /api/v1/keys?teamId= -> {items: [{id, label, provider, keyHint,
  createdAt, revokedAt}]}
- DELETE /api/v1/keys/{id} -> 204 (soft-revoke sets revoked_at; idempotent)

Security posture:
- The raw key is encrypted with the existing KeyVault Fernet vault; it is
  NEVER returned by any endpoint and never logged. Only a last-4 hint
  (KeyVault.hint convention) ever leaves the server.
- key_encrypted/key_hint are excluded from every projection; there is no
  decrypt/read endpoint here (crypto.read_api_key stays internal to S1).
- Team scoping: list is always the caller's own team via get_principal;
  an explicit ?teamId= for a team the caller does not belong to answers
  404 (M13-G5a no-oracle law — never a team-existence signal).
- Revocation of another team's key answers 404 (no existence oracle across
  tenants). Double revoke stays 204 — soft-revoke is idempotent.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, can, error_body
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit_emit import emit_apikey_added, emit_apikey_removed
from .auth import get_principal
from .crypto import KeyVault
from .models import ApiKey

router = APIRouter(prefix="/api/v1")

PROVIDER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$"


class KeyCreatedResponse(BaseModel):
    """M13-G5a typing island: the 201 mint view — exactly the M10-C1 fields."""

    id: str
    label: str
    provider: str
    keyHint: str | None
    createdAt: datetime


class KeyItemResponse(KeyCreatedResponse):
    revokedAt: datetime | None = None


class KeyListResponse(BaseModel):
    items: list[KeyItemResponse]


def _key_public(row: ApiKey, *, include_revoked: bool = True) -> dict:
    """camelCase projection per api-conventions.md / contract M10-C1.

    Never includes key_encrypted; the hint is the only trace of the secret.
    The mint (201) view is exactly the contract's five fields — no revokedAt.
    """
    out: dict[str, str | None] = {
        "id": row.id,
        "label": row.label,
        "provider": row.provider,
        "keyHint": row.key_hint,
        "createdAt": row.created_at.isoformat(),
    }
    if include_revoked:
        out["revokedAt"] = row.revoked_at.isoformat() if row.revoked_at else None
    return out


class KeyCreateBody(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    provider: str = Field(pattern=PROVIDER_PATTERN)
    # Raw secret: bounded so absurd payloads die at validation, never logged.
    key: str = Field(min_length=4, max_length=4096)


@router.post(
    "/keys",
    status_code=201,
    response_model=KeyCreatedResponse,
    responses={
        401: {"description": "bad or missing token"},
        403: {"description": "viewer/guest roles cannot manage keys"},
        422: {"description": "validation error"},
    },
)
async def mint_key(
    body: KeyCreateBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Store a BYO provider key for the caller's team, encrypted at rest."""
    if not can(principal, "write"):
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "viewer/guest roles cannot manage keys"),
        )
    label = body.label.strip()
    if not label:
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", "label required"),
        )
    vault: KeyVault = request.app.state.vault
    async with request.app.state.session_factory() as db:
        row = ApiKey(
            team_id=principal.teamId,
            provider=body.provider,
            label=label,
            key_encrypted=vault.encrypt(body.key),
            key_hint=KeyVault.hint(body.key),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        # C-002 audit trail: mint is an allowlisted sensitive action. The emit
        # helper reuses this committed session and never raises into the path.
        await emit_apikey_added(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=row.id,
            detail={"label": row.label, "provider": row.provider},
        )
        return _key_public(row, include_revoked=False)


@router.get(
    "/keys",
    response_model=KeyListResponse,
    responses={
        401: {"description": "bad or missing token"},
        404: {"description": "teamId does not match the caller's membership (no-oracle)"},
    },
)
async def list_keys(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    teamId: str | None = None,
) -> dict:
    """List the caller's team keys (hint only, never the raw key).

    ?teamId= is honored only when it matches the caller's membership —
    anything else answers 404 NOT_FOUND, identical to an unknown team id
    (M13-G5a no-oracle law; was 403 FORBIDDEN).
    """
    if teamId is not None and teamId != principal.teamId:
        raise HTTPException(
            status_code=404,
            detail=error_body("NOT_FOUND", "team not found"),
        )
    async with request.app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(ApiKey)
                    .where(ApiKey.team_id == principal.teamId, ApiKey.owner_user_id.is_(None))
                    .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
                )
            )
            .scalars()
            .all()
        )
        return {"items": [_key_public(r) for r in rows]}


@router.delete(
    "/keys/{key_id}",
    status_code=204,
    responses={
        401: {"description": "bad or missing token"},
        403: {"description": "viewer/guest roles cannot manage keys"},
        404: {"description": "unknown id OR another team's key (no-oracle)"},
    },
)
async def revoke_key(
    key_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Soft-revoke (sets revoked_at). Idempotent; foreign-team keys 404."""
    if not can(principal, "write"):
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "viewer/guest roles cannot manage keys"),
        )
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(ApiKey).where(
                    ApiKey.id == key_id,
                    ApiKey.team_id == principal.teamId,
                    ApiKey.owner_user_id.is_(None),  # personal keys: owner-only surface
                )
            )
        ).scalar_one_or_none()
        if row is None:
            # Unknown id OR another team's id: identical answer, no oracle.
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "key not found"),
            )
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            await db.commit()
            # C-002 audit: only the transition to revoked is an event; the
            # idempotent double-revoke path stays silent (still 204 either way).
            await emit_apikey_removed(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                target_id=row.id,
                detail={"label": row.label, "provider": row.provider},
            )
    # Already-revoked falls through: 204 either way (idempotent soft-revoke).
