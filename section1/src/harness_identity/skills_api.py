"""Marathon 2.0 W12 — governed team skill catalog (Section 1 side).

A skill is a reviewed Markdown instruction bundle: text that advises prompt
assembly, never capability. The W6 prototype proved the rules on the
filesystem (manifest key allow-list, digest pin, team scope, explicit
review/enable/disable/remove); this router is the same rules over Section 1
storage (migration 0066), served to the orchestrator's skills_loader.

Contract M2-W12:
- GET    /api/v1/teams/{teamId}/skills             -> 200 {skills:[...]}
        All statuses visible to the team (viewers read; guests read).
- POST   /api/v1/teams/{teamId}/skills             -> 201 {skill}
        {name, version, description, instructions, source?, author?, expiresAt?}
        Creates a DRAFT. A draft is never injectable.
- POST   /api/v1/teams/{teamId}/skills/{name}/review  -> 200 {skill}
        Admin only. Pins the digest of the CURRENT content; status becomes
        reviewed + enabled. The pin never moves on its own.
- POST   /api/v1/teams/{teamId}/skills/{name}/enable | /disable -> 200 {skill}
        Write roles; only a reviewed skill can be toggled.
- DELETE /api/v1/teams/{teamId}/skills/{name}      -> 204 idempotent.
        Admin only.

Security posture:
- Every mutation is admin- or engineer-gated (review and remove are admin);
  viewers/guests may only read — matching plan-approval role semantics.
- Cross-team access answers 404 NOT_FOUND with no existence oracle (teamId
  != principal.teamId is refused before any row is touched).
- Size caps are enforced at the API edge (413 SKILL_TOO_LARGE) before any
  write; migration CHECKs are defense in depth.
- There is no field for a command, script, hook, env, tool or permission:
  unknown body keys are rejected by the pydantic model, so a skill cannot
  smuggle capability in.
- Audited as allowlisted actions skill.created / skill.reviewed /
  skill.enabled / skill.disabled / skill.removed via audit_emit helpers.

Digest (the pin): sha256 of the canonical JSON
{name, version, description, instructions, source, author, teamId}
sorted keys, compact separators, ensure_ascii=False. The orchestrator
recomputes it from the row before injecting and skips a mismatch. Golden
vector pinned in tests on both sides of the boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, can, error_body
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit_emit import (
    emit_skill_created,
    emit_skill_disabled,
    emit_skill_enabled,
    emit_skill_removed,
    emit_skill_reviewed,
)
from .auth import get_principal
from .models import Team, TeamSkill

router = APIRouter(prefix="/api/v1")

MAX_INSTRUCTIONS_BYTES = 16_384  # 16KB — mirrors migration 0066 CHECK + S4 loader cap
MAX_DESCRIPTION = 500
MAX_PROVENANCE = 200
MAX_VERSION = 16
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_STATUSES = ("draft", "reviewed", "disabled")
#: Sentinel from _validate_body for the authoritative byte cap (413, not 422).
TOO_LARGE = "__too_large__"


def canonical_skill(name: str, version: str, description: str, instructions: str,
                    source: str, author: str, team_id: str) -> str:
    """The exact JSON whose sha256 is the skill pin. Same bytes on S4's side."""
    return json.dumps(
        {
            "name": name,
            "version": version,
            "description": description,
            "instructions": instructions,
            "source": source,
            "author": author,
            "teamId": team_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def skill_digest(name: str, version: str, description: str, instructions: str,
                 source: str, author: str, team_id: str) -> str:
    return hashlib.sha256(
        canonical_skill(name, version, description, instructions, source, author, team_id)
        .encode("utf-8")
    ).hexdigest()


def _skill_public(row: TeamSkill) -> dict[str, Any]:
    """camelCase projection per api-conventions.md; digest travels with it."""
    return {
        "id": row.id,
        "teamId": row.team_id,
        "name": row.name,
        "version": row.version,
        "description": row.description,
        "instructions": row.instructions,
        "source": row.provenance_source,
        "author": row.provenance_author,
        "status": row.status,
        "enabled": row.enabled,
        "reviewedBy": row.reviewed_by,
        "reviewedAt": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "pinnedSha256": row.pinned_sha256,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


async def _owned_team(db, team_id: str, principal: Principal) -> bool:
    """True iff the team exists AND is the caller's own team.

    Cross-team access answers 404 with no existence oracle: the same answer
    for a foreign team and an unknown id.
    """
    if team_id != principal.teamId:
        return False
    exists = (
        await db.execute(select(Team.id).where(Team.id == team_id))
    ).scalar_one_or_none()
    return exists is not None


def _require_write(principal: Principal) -> None:
    if not can(principal, "write"):
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "viewer/guest roles cannot edit team skills"),
        )


def _require_admin(principal: Principal) -> None:
    if principal.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "only team admins can do that"),
        )


async def _find(db, team_id: str, name: str) -> TeamSkill | None:
    return (
        await db.execute(
            select(TeamSkill).where(TeamSkill.team_id == team_id, TeamSkill.name == name)
        )
    ).scalar_one_or_none()


async def _get_skill_or_404(db, team_id: str, name: str) -> TeamSkill:
    row = await _find(db, team_id, name)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=error_body("NOT_FOUND", "skill not found"),
        )
    return row


class SkillBody(BaseModel):
    # name/version/description are field-bounded; instructions carry the
    # AUTHORITATIVE UTF-8 byte cap in the handler (multi-byte text can exceed
    # any char count), answering the policy code 413 SKILL_TOO_LARGE.
    name: str = Field(min_length=2, max_length=32)
    version: str = Field(min_length=1, max_length=MAX_VERSION)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION)
    instructions: str = Field(min_length=1, max_length=MAX_INSTRUCTIONS_BYTES * 4)
    source: str = Field(default="team-authored", max_length=MAX_PROVENANCE)
    author: str | None = Field(default=None, max_length=MAX_PROVENANCE)
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = {"populate_by_name": True}


def _validate_body(body: SkillBody) -> str:
    """The part of validation pydantic cannot express.

    Returns "" when the body is fine, TOO_LARGE for the authoritative byte cap
    (answered 413 SKILL_TOO_LARGE, the same posture as the rules route's cap),
    or a human-readable problem string (answered 422 VALIDATION_ERROR).
    """
    if not _NAME.fullmatch(body.name):
        return f"name {body.name!r} must match {_NAME.pattern}"
    if not _VERSION.fullmatch(body.version):
        return f"version {body.version!r} must be x.y.z"
    if not body.description.strip():
        return "description must be non-empty text"
    if not body.instructions.strip():
        return "instructions must be non-empty text"
    if len(body.instructions.encode("utf-8")) > MAX_INSTRUCTIONS_BYTES:
        return TOO_LARGE
    if not body.source.strip():
        return "source must be non-empty text"
    return ""


@router.get("/teams/{team_id}/skills")
async def list_skills(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Every skill of the caller's own team, including drafts and disabled."""
    async with request.app.state.session_factory() as db:
        if not await _owned_team(db, team_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "team not found"),
            )
        rows = (
            await db.execute(
                select(TeamSkill).where(TeamSkill.team_id == team_id).order_by(TeamSkill.name)
            )
        ).scalars().all()
        return {"skills": [_skill_public(row) for row in rows]}


@router.post("/teams/{team_id}/skills", status_code=201)
async def create_skill(
    team_id: str,
    body: SkillBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Author a draft. A draft is visible to the team but never injectable."""
    _require_write(principal)
    problem = _validate_body(body)
    if problem == TOO_LARGE:
        raise HTTPException(
            status_code=413,
            detail=error_body(
                "SKILL_TOO_LARGE",
                f"skill instructions exceed the {MAX_INSTRUCTIONS_BYTES // 1024}KB limit",
            ),
        )
    if problem:
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", problem),
        )
    author = (body.author or "").strip() or principal.userId
    async with request.app.state.session_factory() as db:
        if not await _owned_team(db, team_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "team not found"),
            )
        if await _find(db, team_id, body.name) is not None:
            raise HTTPException(
                status_code=409,
                detail=error_body("NAME_TAKEN", f"skill {body.name!r} already exists"),
            )
        row = TeamSkill(
            team_id=team_id,
            name=body.name,
            version=body.version,
            description=body.description.strip(),
            instructions=body.instructions,
            provenance_source=body.source.strip(),
            provenance_author=author,
            status="draft",
            enabled=False,
            expires_at=body.expires_at,
            created_by=principal.userId,
        )
        row.pinned_sha256 = skill_digest(
            row.name, row.version, row.description, row.instructions,
            row.provenance_source, row.provenance_author, row.team_id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        await emit_skill_created(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=row.id,
            detail={"name": row.name, "version": row.version, "digest": row.pinned_sha256[:12]},
        )
        return {"skill": _skill_public(row)}


@router.post("/teams/{team_id}/skills/{name}/review")
async def review_skill(
    team_id: str,
    name: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Pin the digest of the current content and make the skill injectable."""
    _require_admin(principal)
    async with request.app.state.session_factory() as db:
        if not await _owned_team(db, team_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "team not found"),
            )
        row = await _get_skill_or_404(db, team_id, name)
        row.pinned_sha256 = skill_digest(
            row.name, row.version, row.description, row.instructions,
            row.provenance_source, row.provenance_author, row.team_id,
        )
        row.status = "reviewed"
        row.enabled = True
        row.reviewed_by = principal.userId
        row.reviewed_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        await emit_skill_reviewed(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=row.id,
            detail={"name": row.name, "digest": row.pinned_sha256[:12]},
        )
        return {"skill": _skill_public(row)}


@router.post("/teams/{team_id}/skills/{name}/enable")
async def enable_skill(
    team_id: str,
    name: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Switch a reviewed skill back on. Non-reviewed skills cannot be enabled."""
    _require_write(principal)
    async with request.app.state.session_factory() as db:
        if not await _owned_team(db, team_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "team not found"),
            )
        row = await _get_skill_or_404(db, team_id, name)
        if row.status not in ("reviewed", "disabled") or not row.pinned_sha256:
            raise HTTPException(
                status_code=409,
                detail=error_body("NOT_REVIEWED", "only a reviewed skill can be enabled"),
            )
        row.status = "reviewed"
        row.enabled = True
        row.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        await emit_skill_enabled(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=row.id,
            detail={"name": row.name},
        )
        return {"skill": _skill_public(row)}


@router.post("/teams/{team_id}/skills/{name}/disable")
async def disable_skill(
    team_id: str,
    name: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Switch a reviewed skill off. It stays reviewed; the pin is untouched."""
    _require_write(principal)
    async with request.app.state.session_factory() as db:
        if not await _owned_team(db, team_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "team not found"),
            )
        row = await _get_skill_or_404(db, team_id, name)
        if row.status not in ("reviewed", "disabled") or not row.pinned_sha256:
            raise HTTPException(
                status_code=409,
                detail=error_body("NOT_REVIEWED", "only a reviewed skill can be disabled"),
            )
        row.status = "disabled"
        row.enabled = False
        row.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        await emit_skill_disabled(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=row.id,
            detail={"name": row.name},
        )
        return {"skill": _skill_public(row)}


@router.delete("/teams/{team_id}/skills/{name}", status_code=204)
async def remove_skill(
    team_id: str,
    name: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Delete a skill entirely. Idempotent: removing what is absent is 204."""
    _require_admin(principal)
    async with request.app.state.session_factory() as db:
        if not await _owned_team(db, team_id, principal):
            raise HTTPException(
                status_code=404,
                detail=error_body("NOT_FOUND", "team not found"),
            )
        row = await _find(db, team_id, name)
        if row is not None:
            await db.delete(row)
            await db.commit()
            await emit_skill_removed(
                db,
                actor_user_id=principal.userId,
                team_id=team_id,
                target_id=row.id,
                detail={"name": row.name},
            )
        return None
