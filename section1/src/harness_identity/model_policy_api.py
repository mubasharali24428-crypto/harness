"""A1-S3 — org model allowlist (approval workflow) + team model policy.

``/api/v1/identity/model-allowlist`` : members request, ONLY admins decide.
``/api/v1/identity/model-policy``    : admin-authored routing / opt-in fallback /
cache policy, validated by ``harness_security_policy.model_policy`` and stored
as JSON in ``team_settings['model_policy']``.

Enforcement lives where models are called (Section 3 reads these rows). Nothing
here substitutes a model; fallback exists only if an admin enabled it and named
approved equivalents. Unknown/foreign ids answer an identical 404.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, can, error_body
from harness_security_policy.model_policy import TeamModelPolicy
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .audit_emit import emit_identity_event
from .auth import get_principal
from .models import ModelAllowlistEntry, TeamSetting

router = APIRouter(prefix="/api/v1/identity")

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
POLICY_KEY = "model_policy"
MAX_POLICY_BYTES = 32 * 1024


def _admin(principal: Principal) -> None:
    if principal.role != Role.ADMIN:
        raise HTTPException(403, detail=error_body("FORBIDDEN", "admin role required"))


def _public(e: ModelAllowlistEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "model": e.model,
        "status": e.status,
        "note": e.note,
        "requestedBy": e.requested_by,
        "decidedBy": e.decided_by,
        "decidedAt": e.decided_at.isoformat() if e.decided_at else None,
    }


class RequestBody(BaseModel):
    model: str = Field(pattern=MODEL_RE.pattern)
    note: str = Field(default="", max_length=500)

    model_config = {"extra": "forbid"}


@router.post("/model-allowlist", status_code=201)
async def request_model(
    body: RequestBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    if not can(principal, "write"):
        raise HTTPException(403, detail=error_body("FORBIDDEN", "role cannot request models"))
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(ModelAllowlistEntry).where(
                    ModelAllowlistEntry.team_id == principal.teamId,
                    ModelAllowlistEntry.model == body.model,
                )
            )
        ).scalar_one_or_none()
        if row is not None and row.status in ("pending", "approved"):
            return _public(row)  # idempotent: request for a live entry is a no-op
        if row is None:
            row = ModelAllowlistEntry(
                team_id=principal.teamId,
                model=body.model,
                note=body.note,
                requested_by=principal.userId,
            )
            db.add(row)
        else:  # denied/revoked -> re-request
            row.status, row.note, row.requested_by = "pending", body.note, principal.userId
            row.decided_by = row.decided_at = None
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, detail=error_body("CONFLICT", "concurrent request")) from None
        await db.refresh(row)
        await emit_identity_event(
            db,
            "model_allowlist.requested",
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_type="model_allowlist",
            target_id=row.id,
            detail={"model": row.model},
        )
        return _public(row)


@router.get("/model-allowlist")
async def list_allowlist(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    status: str | None = None,
) -> dict:
    if status is not None and status not in ("pending", "approved", "denied", "revoked"):
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", "status invalid"))
    stmt = select(ModelAllowlistEntry).where(ModelAllowlistEntry.team_id == principal.teamId)
    if status:
        stmt = stmt.where(ModelAllowlistEntry.status == status)
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(stmt.order_by(ModelAllowlistEntry.model))).scalars().all()
    return {"items": [_public(r) for r in rows]}


_TRANSITIONS = {
    "approve": (("pending",), "approved", "model_allowlist.approved"),
    "deny": (("pending",), "denied", "model_allowlist.denied"),
    "revoke": (("approved",), "revoked", "model_allowlist.revoked"),
}


async def _decide(request, principal, entry_id: str, verb: str) -> dict:
    _admin(principal)
    frm, to, action = _TRANSITIONS[verb]
    async with request.app.state.session_factory() as db:
        res = await db.execute(
            update(ModelAllowlistEntry)
            .where(
                ModelAllowlistEntry.id == entry_id,
                ModelAllowlistEntry.team_id == principal.teamId,
                ModelAllowlistEntry.status.in_(frm),
            )
            .values(status=to, decided_by=principal.userId, decided_at=datetime.now(UTC))
        )
        await db.commit()
        row = (
            await db.execute(
                select(ModelAllowlistEntry).where(
                    ModelAllowlistEntry.id == entry_id,
                    ModelAllowlistEntry.team_id == principal.teamId,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail=error_body("NOT_FOUND", "entry not found"))
        if res.rowcount != 1:
            raise HTTPException(409, detail=error_body("CONFLICT", f"entry is {row.status}"))
        await emit_identity_event(
            db,
            action,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_type="model_allowlist",
            target_id=row.id,
            detail={"model": row.model},
        )
        return _public(row)


for _verb in _TRANSITIONS:

    def _make(verb: str):
        async def handler(
            entry_id: str,
            request: Request,
            principal: Annotated[Principal, Depends(get_principal)],
        ) -> dict:
            return await _decide(request, principal, entry_id, verb)

        handler.__name__ = f"{verb}_entry"
        return handler

    router.add_api_route(f"/model-allowlist/{{entry_id}}/{_verb}", _make(_verb), methods=["POST"])


async def load_policy(db, team_id: str) -> TeamModelPolicy:
    """Stored policy + approved set; malformed stored JSON fails CLOSED."""
    approved = (
        (
            await db.execute(
                select(ModelAllowlistEntry.model).where(
                    ModelAllowlistEntry.team_id == team_id,
                    ModelAllowlistEntry.status == "approved",
                )
            )
        )
        .scalars()
        .all()
    )
    raw = (
        await db.execute(
            select(TeamSetting.value).where(
                TeamSetting.team_id == team_id, TeamSetting.key == POLICY_KEY
            )
        )
    ).scalar_one_or_none()
    if raw is None:
        return TeamModelPolicy(approved_models=frozenset(approved))
    try:
        return TeamModelPolicy.from_json(json.loads(raw), approved=approved)
    except (ValueError, TypeError):
        return TeamModelPolicy(allowlist_enforced=True)


@router.get("/model-policy")
async def get_policy(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    async with request.app.state.session_factory() as db:
        pol = await load_policy(db, principal.teamId)
    return {**pol.to_json(), "approvedModels": sorted(pol.approved_models)}


@router.put("/model-policy")
async def put_policy(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    _admin(principal)
    length = request.headers.get("content-length", "")
    if not length.isdigit() or int(length) > MAX_POLICY_BYTES:
        raise HTTPException(413, detail=error_body("PAYLOAD_TOO_LARGE", "policy too large"))
    try:
        doc = await request.json()
        if not isinstance(doc, dict):
            raise ValueError("policy must be an object")
        TeamModelPolicy.from_json(doc)
    except ValueError as exc:
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", str(exc)[:200])) from exc
    canonical = TeamModelPolicy.from_json(doc).to_json()
    async with request.app.state.session_factory() as db:
        row = await db.get(TeamSetting, (principal.teamId, POLICY_KEY))
        if row is None:
            db.add(
                TeamSetting(
                    team_id=principal.teamId,
                    key=POLICY_KEY,
                    value=json.dumps(canonical),
                    updated_by=principal.userId,
                )
            )
        else:
            row.value, row.updated_by = json.dumps(canonical), principal.userId
        await db.commit()
        await emit_identity_event(
            db,
            "model_policy.saved",
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_type="team",
            target_id=principal.teamId,
            detail={
                "allowlistEnforced": canonical["allowlistEnforced"],
                "fallbackEnabled": canonical["fallbackEnabled"],
                "cacheEnabled": canonical["cacheEnabled"],
            },
        )
        pol = await load_policy(db, principal.teamId)
    return {**pol.to_json(), "approvedModels": sorted(pol.approved_models)}
