"""A1-S1 — authenticated HAP/1 policy evaluator + capability grant issuance (F091-F096).

Namespace ``/api/v1/policy`` (new endpoints only). Identity and team ALWAYS come
from the bearer principal; the body carries no teamId. ``projectId`` must belong
to the caller's team (unknown == foreign == 404, no existence oracle).

- POST /api/v1/policy/evaluate     HAP/1 request -> HAP/1 response. Dry: never
  consumes capability use budgets and never mutates state except the denial
  audit row. DENY outcomes are recorded as ``policy.denied`` (ids/reason only,
  never content).
- POST /api/v1/policy/capabilities issue a short-lived grant (admin/engineer).
  The token is returned once and never stored or logged.

This evaluator is a DECISION POINT. Section 5 remains the execution-time
authority; nothing here proves SDK/browser/MCP callers consult it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, can, error_body
from harness_security_policy import CapabilitySigner, derive_capability_key
from harness_security_policy.hap import HapRequest, evaluate_hap_policy_request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit_emit import emit_policy_capability_issued, emit_policy_denied
from .auth import get_principal
from .models import Project

router = APIRouter(prefix="/api/v1/policy")

MAX_BODY_BYTES = 1024 * 1024


def _signer(request: Request) -> CapabilitySigner:
    return CapabilitySigner(derive_capability_key(request.app.state.settings.master_key))


async def _require_owned_project(request: Request, project_id: str, principal: Principal) -> None:
    async with request.app.state.session_factory() as db:
        team_id = (
            await db.execute(select(Project.team_id).where(Project.id == project_id))
        ).scalar_one_or_none()
    if team_id is None or team_id != principal.teamId:
        raise HTTPException(404, detail=error_body("NOT_FOUND", "project not found"))


def _check_size(request: Request) -> None:
    length = request.headers.get("content-length")
    if length is None or not length.isdigit() or int(length) > MAX_BODY_BYTES:
        raise HTTPException(413, detail=error_body("PAYLOAD_TOO_LARGE", "body too large"))


@router.post("/evaluate")
async def evaluate(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    _check_size(request)
    try:
        req = HapRequest.from_wire(await request.json())
    except ValueError as exc:
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", str(exc)[:200])) from exc
    await _require_owned_project(request, req.project_id, principal)
    resp, decision = evaluate_hap_policy_request(
        req, team_id=principal.teamId, signer=_signer(request)
    )
    if decision.outcome.value == "deny":
        async with request.app.state.session_factory() as db:
            await emit_policy_denied(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                target_id=req.run_id,
                detail={
                    "operation": req.operation,
                    "reason": decision.reason,
                    "requestId": req.request_id,
                    "projectId": req.project_id,
                },
                session_owner=True,
            )
    return resp.to_wire()


class CapabilityBody(BaseModel):
    project_id: str = Field(alias="projectId", min_length=1, max_length=64)
    run_id: str = Field(alias="runId", min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    audience: str = Field(min_length=1, max_length=128)
    arguments: Any = None
    ttl_seconds: int = Field(default=300, alias="ttlSeconds", ge=1, le=86_400)
    max_uses: int = Field(default=1, alias="maxUses", ge=1, le=1000)

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/capabilities", status_code=201)
async def issue_capability(
    body: CapabilityBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    if not can(principal, "write"):
        raise HTTPException(403, detail=error_body("FORBIDDEN", "role cannot issue grants"))
    await _require_owned_project(request, body.project_id, principal)
    cap = _signer(request).issue(
        team_id=principal.teamId,
        run_id=body.run_id,
        operation=body.operation,
        audience=body.audience,
        args=body.arguments,
        project_id=body.project_id,
        ttl_seconds=body.ttl_seconds,
        max_uses=body.max_uses,
        issuer=principal.userId,
    )
    async with request.app.state.session_factory() as db:
        await emit_policy_capability_issued(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=cap.token_id,
            detail={
                "runId": body.run_id,
                "operation": body.operation,
                "audience": body.audience,
                "expiresAt": cap.expires_at,
                "maxUses": cap.max_uses,
            },
            session_owner=True,
        )
    return {
        "capability": cap.token,
        "tokenId": cap.token_id,
        "expiresAt": cap.expires_at,
        "maxUses": cap.max_uses,
    }
