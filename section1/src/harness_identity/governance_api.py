"""A1-S4 — package trust, security correlation, remediation proposals, policy simulator, retention.

Namespace ``/api/v1/identity``. Admin-only for every mutation; reads are team-scoped
via the bearer principal. Nothing here executes or installs anything.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, can, error_body
from harness_security_policy.egress import EgressPolicy, evaluate_url
from harness_security_policy.hap import evaluate_outbound_payload
from harness_security_policy.model_policy import evaluate_model
from harness_security_policy.sensitive_files import evaluate_path
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from .audit_emit import emit_identity_event
from .auth import get_principal
from .model_policy_api import load_policy
from .models import (
    AgentEvent,
    AuditLog,
    PackageRevocation,
    PackageTrustIssuer,
    Project,
    RetentionPolicy,
)
from .package_trust import IssuerKey, sha256_hex, valid_public_key, verify_package
from .security_ops import correlate, propose_remediation

router = APIRouter(prefix="/api/v1/identity")

MAX_PACKAGE_BYTES = 8 * 1024 * 1024
RETENTION_BATCH = 10_000


def _admin(p: Principal) -> None:
    if p.role != Role.ADMIN:
        raise HTTPException(403, detail=error_body("FORBIDDEN", "admin role required"))


def _nf(msg: str = "not found") -> HTTPException:
    return HTTPException(404, detail=error_body("NOT_FOUND", msg))


# ------------------------------------------------------------------ package trust


class IssuerBody(BaseModel):
    key_id: str = Field(alias="keyId", min_length=1, max_length=120)
    issuer: str = Field(min_length=1, max_length=200)
    public_key: str = Field(alias="publicKey", min_length=1, max_length=64)

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/package-trust/issuers", status_code=201)
async def add_issuer(
    body: IssuerBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    _admin(p)
    if not valid_public_key(body.public_key):
        raise HTTPException(
            422, detail=error_body("VALIDATION_ERROR", "publicKey must be 32-byte base64")
        )
    async with request.app.state.session_factory() as db:
        db.add(
            PackageTrustIssuer(
                team_id=p.teamId,
                key_id=body.key_id,
                issuer=body.issuer,
                public_key=body.public_key,
                created_by=p.userId,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                409, detail=error_body("CONFLICT", "keyId already registered")
            ) from None
        await emit_identity_event(
            db,
            "package_trust.issuer_added",
            actor_user_id=p.userId,
            team_id=p.teamId,
            target_type="package_issuer",
            target_id=body.key_id,
            detail={"issuer": body.issuer},
        )
    return {"keyId": body.key_id, "issuer": body.issuer, "status": "active"}


@router.get("/package-trust/issuers")
async def list_issuers(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict:
    async with request.app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(PackageTrustIssuer).where(PackageTrustIssuer.team_id == p.teamId)
                )
            )
            .scalars()
            .all()
        )
    return {"items": [{"keyId": r.key_id, "issuer": r.issuer, "status": r.status} for r in rows]}


@router.post("/package-trust/issuers/{key_id}/revoke")
async def revoke_issuer(
    key_id: str, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    _admin(p)
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(PackageTrustIssuer).where(
                    PackageTrustIssuer.team_id == p.teamId, PackageTrustIssuer.key_id == key_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise _nf("issuer not found")
        if row.status == "active":
            row.status, row.revoked_at = "revoked", datetime.now(UTC)
            await db.commit()
            await emit_identity_event(
                db,
                "package_trust.issuer_revoked",
                actor_user_id=p.userId,
                team_id=p.teamId,
                target_type="package_issuer",
                target_id=key_id,
            )
    return {"keyId": key_id, "status": "revoked"}


class RevocationBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=100)
    sha256: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{64}$")
    reason: str = Field(default="", max_length=500)

    model_config = {"extra": "forbid"}


@router.post("/package-trust/revocations", status_code=201)
async def revoke_package(
    body: RevocationBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    _admin(p)
    if body.sha256 is None and not (body.name and body.version):
        raise HTTPException(
            422, detail=error_body("VALIDATION_ERROR", "need sha256 or name+version")
        )
    async with request.app.state.session_factory() as db:
        row = PackageRevocation(
            team_id=p.teamId,
            name=body.name,
            version=body.version,
            sha256=body.sha256.lower() if body.sha256 else None,
            reason=body.reason,
            created_by=p.userId,
        )
        db.add(row)
        await db.commit()
        await emit_identity_event(
            db,
            "package_trust.package_revoked",
            actor_user_id=p.userId,
            team_id=p.teamId,
            target_type="package",
            target_id=row.id,
            detail={"name": body.name, "version": body.version},
        )
    return {"id": row.id}


class VerifyBody(BaseModel):
    manifest: dict[str, Any]
    signature: str = Field(max_length=200)
    content_base64: str | None = Field(default=None, alias="contentBase64")
    content_sha256: str | None = Field(
        default=None, alias="contentSha256", pattern=r"^[A-Fa-f0-9]{64}$"
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/package-trust/verify")
async def verify(
    body: VerifyBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """``bytesBound`` is true only when the server hashed the supplied bytes itself.

    With only ``contentSha256`` the result proves the manifest is authentic and
    matches the digest the CALLER claims; the caller must hash the artifact it installs.
    """
    if (body.content_base64 is None) == (body.content_sha256 is None):
        raise HTTPException(
            422, detail=error_body("VALIDATION_ERROR", "send contentBase64 xor contentSha256")
        )
    if body.content_base64 is not None:
        if len(body.content_base64) > MAX_PACKAGE_BYTES * 4 // 3 + 8:
            raise HTTPException(413, detail=error_body("PAYLOAD_TOO_LARGE", "package too large"))
        try:
            digest = sha256_hex(base64.b64decode(body.content_base64, validate=True))
        except (binascii.Error, ValueError):
            raise HTTPException(
                422, detail=error_body("VALIDATION_ERROR", "contentBase64 invalid")
            ) from None
    else:
        digest = body.content_sha256 or ""
    async with request.app.state.session_factory() as db:
        issuers = {
            r.key_id: IssuerKey(r.issuer, r.public_key, r.status == "active")
            for r in (
                await db.execute(
                    select(PackageTrustIssuer).where(PackageTrustIssuer.team_id == p.teamId)
                )
            ).scalars()
        }
        revs = (
            (
                await db.execute(
                    select(PackageRevocation).where(PackageRevocation.team_id == p.teamId)
                )
            )
            .scalars()
            .all()
        )
    result = verify_package(
        body.manifest,
        body.signature,
        content_sha256=digest,
        issuers=issuers,
        revoked_name_versions={(r.name, r.version) for r in revs if r.name and r.version},
        revoked_digests={r.sha256 for r in revs if r.sha256},
    )
    return {**result, "bytesBound": body.content_base64 is not None}


# ------------------------------------------------------------------ correlation / remediation


@router.get("/security/correlations")
async def correlations(
    request: Request,
    p: Annotated[Principal, Depends(get_principal)],
    sinceHours: int = 24,  # noqa: N803
) -> dict:
    """Pattern findings over THIS team's audit rows (audit_log has no team column:
    scoped by ``detail.teamId``; bounded to the newest 5000 rows)."""
    if not 1 <= sinceHours <= 720:
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", "sinceHours 1..720"))
    since = datetime.now(UTC) - timedelta(hours=sinceHours)
    async with request.app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditLog)
                    .where(AuditLog.ts >= since)
                    .order_by(AuditLog.ts.desc())
                    .limit(5000)
                )
            )
            .scalars()
            .all()
        )
    events = [
        {
            "id": str(r.seq),
            "action": r.action,
            "actor": r.actor_member_id,
            "ts": r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=UTC),
        }
        for r in rows
        if (r.detail or {}).get("teamId") == p.teamId
    ]
    return {
        "windowHours": sinceHours,
        "eventsConsidered": len(events),
        "findings": correlate(events),
    }


class RemediationBody(BaseModel):
    installed: dict[str, str] = Field(max_length=2000)
    advisories: list[dict[str, Any]] = Field(max_length=5000)


@router.post("/dependency-remediation/propose")
async def remediation(
    body: RemediationBody, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """Proposals only: nothing is installed, merged or committed (autoApply is always false)."""
    if not can(p, "write"):
        raise HTTPException(403, detail=error_body("FORBIDDEN", "role cannot request proposals"))
    return {"proposals": propose_remediation(body.installed, body.advisories)}


# ------------------------------------------------------------------ policy simulator


class SimulateBody(BaseModel):
    role: str | None = None
    action: str | None = Field(default=None, pattern=r"^(read|write|approve|merge_gate)$")
    model: str | None = Field(default=None, max_length=120)
    text: str | None = Field(default=None, max_length=200_000)
    url: str | None = Field(default=None, max_length=2000)
    allowed_hosts: list[str] = Field(default_factory=list, alias="allowedHosts", max_length=50)
    path: str | None = Field(default=None, max_length=1000)
    file_operation: str = Field(default="read", alias="fileOperation")

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/policy/simulate")
async def simulate(
    body: SimulateBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """What-if across RBAC, model policy, outbound scan, egress and file policy.

    Read-only: writes nothing (no audit row, no capability use). DNS is NOT
    resolved, so a hostname that would resolve to a private address is reported
    with ``dnsChecked=false``. A check that was not requested is omitted.
    """
    checks: list[dict[str, Any]] = []
    if body.action:
        try:
            role = Role(body.role) if body.role else p.role
        except ValueError:
            raise HTTPException(
                422, detail=error_body("VALIDATION_ERROR", "unknown role")
            ) from None
        ok = can(Principal(userId=p.userId, teamId=p.teamId, role=role), body.action)  # type: ignore[arg-type]
        checks.append(
            {
                "check": "rbac",
                "outcome": "allow" if ok else "deny",
                "reason": f"role.{role.value}.{body.action}",
            }
        )
    if body.model:
        async with request.app.state.session_factory() as db:
            policy = await load_policy(db, p.teamId)
        known = {body.model}  # catalog membership is Section 3's call; here only the team policy
        d = evaluate_model(policy, body.model, known)
        checks.append({"check": "model_policy", "outcome": d.outcome.value, "reason": d.reason})
    if body.text is not None:
        d = evaluate_outbound_payload(body.text)
        checks.append(
            {
                "check": "outbound_content",
                "outcome": d.outcome.value,
                "reason": d.reason,
                "findings": [f.rule_id for f in d.findings],
            }
        )
    if body.url is not None:
        pol = EgressPolicy(
            allowed_hosts=tuple(body.allowed_hosts), allow_http=False, require_dns_check=False
        )
        d = evaluate_url(body.url, policy=pol)
        checks.append(
            {"check": "egress", "outcome": d.outcome.value, "reason": d.reason, "dnsChecked": False}
        )
    if body.path is not None:
        d = evaluate_path(body.path, operation=body.file_operation)
        checks.append({"check": "sensitive_file", "outcome": d.outcome.value, "reason": d.reason})
    order = ["allow", "redact", "quarantine", "deny"]
    worst = max((c["outcome"] for c in checks), key=order.index, default="allow")
    return {"overall": worst, "checks": checks, "mutated": False}


# ------------------------------------------------------------------ retention


class RetentionBody(BaseModel):
    project_id: str | None = Field(default=None, alias="projectId", max_length=36)
    resource: str = Field(default="agent_events", pattern=r"^agent_events$")
    retain_days: int = Field(alias="retainDays", ge=7, le=3650)
    legal_hold: bool | None = Field(default=None, alias="legalHold")
    hold_reason: str = Field(default="", alias="holdReason", max_length=500)

    model_config = {"populate_by_name": True, "extra": "forbid"}


def _ret_public(r: RetentionPolicy) -> dict:
    return {
        "id": r.id,
        "projectId": r.project_id,
        "resource": r.resource,
        "retainDays": r.retain_days,
        "legalHold": r.legal_hold,
        "holdReason": r.hold_reason,
    }


async def _own_project(db, team_id: str, project_id: str) -> None:
    owner = (
        await db.execute(select(Project.team_id).where(Project.id == project_id))
    ).scalar_one_or_none()
    if owner != team_id:
        raise _nf("project not found")


@router.put("/retention")
async def put_retention(
    body: RetentionBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """Upsert a policy. While a legal hold is active the period cannot be shortened and the
    hold can only be released by an explicit ``legalHold: false``."""
    _admin(p)
    async with request.app.state.session_factory() as db:
        if body.project_id:
            await _own_project(db, p.teamId, body.project_id)
        cond = [RetentionPolicy.team_id == p.teamId, RetentionPolicy.resource == body.resource]
        cond.append(
            RetentionPolicy.project_id == body.project_id
            if body.project_id
            else RetentionPolicy.project_id.is_(None)
        )
        row = (await db.execute(select(RetentionPolicy).where(*cond))).scalar_one_or_none()
        if row is None:
            row = RetentionPolicy(
                team_id=p.teamId,
                project_id=body.project_id,
                resource=body.resource,
                retain_days=body.retain_days,
            )
            db.add(row)
        elif row.legal_hold and body.retain_days < row.retain_days and body.legal_hold is not False:
            raise HTTPException(
                409, detail=error_body("LEGAL_HOLD", "cannot shorten retention under hold")
            )
        else:
            row.retain_days = body.retain_days
        if body.legal_hold is not None:
            row.legal_hold = body.legal_hold
            row.hold_reason = body.hold_reason if body.legal_hold else ""
        row.updated_by = p.userId
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, detail=error_body("CONFLICT", "concurrent update")) from None
        await db.refresh(row)
        await emit_identity_event(
            db,
            "retention.policy_saved",
            actor_user_id=p.userId,
            team_id=p.teamId,
            target_type="retention_policy",
            target_id=row.id,
            detail={
                "retainDays": row.retain_days,
                "legalHold": row.legal_hold,
                "projectId": row.project_id,
            },
        )
        return _ret_public(row)


@router.get("/retention")
async def get_retention(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict:
    async with request.app.state.session_factory() as db:
        rows = (
            (await db.execute(select(RetentionPolicy).where(RetentionPolicy.team_id == p.teamId)))
            .scalars()
            .all()
        )
    return {"items": [_ret_public(r) for r in rows]}


async def _plan(db, team_id: str, now: datetime) -> list[dict]:
    """Per project: effective policy (project > team), cutoff, matching rows, held?"""
    pols = (
        (
            await db.execute(
                select(RetentionPolicy).where(
                    RetentionPolicy.team_id == team_id, RetentionPolicy.resource == "agent_events"
                )
            )
        )
        .scalars()
        .all()
    )
    team_pol = next((r for r in pols if r.project_id is None), None)
    by_project = {r.project_id: r for r in pols if r.project_id}
    projects = (
        (await db.execute(select(Project.id).where(Project.team_id == team_id))).scalars().all()
    )
    out = []
    for pid in projects:
        pol = by_project.get(pid) or team_pol
        if pol is None:
            continue
        # a team-wide hold covers every project, whatever the project's own period
        held = pol.legal_hold or bool(team_pol and team_pol.legal_hold)
        cutoff = now - timedelta(days=pol.retain_days)
        count = (
            await db.execute(
                select(func.count())
                .select_from(AgentEvent)
                .where(AgentEvent.project_id == pid, AgentEvent.ts < cutoff)
            )
        ).scalar_one()
        out.append(
            {
                "projectId": pid,
                "retainDays": pol.retain_days,
                "cutoff": cutoff,
                "eligible": int(count),
                "held": held,
            }
        )
    return out


def _plan_public(plan: list[dict]) -> list[dict]:
    return [{**x, "cutoff": x["cutoff"].isoformat()} for x in plan]


@router.post("/retention/plan")
async def retention_plan(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict:
    """Dry run: what ``apply`` would delete. Deletes nothing."""
    _admin(p)
    async with request.app.state.session_factory() as db:
        plan = await _plan(db, p.teamId, datetime.now(UTC))
    return {
        "projects": _plan_public(plan),
        "willDelete": sum(x["eligible"] for x in plan if not x["held"]),
    }


class ApplyBody(BaseModel):
    confirm: bool
    expected_count: int = Field(alias="expectedCount", ge=0)

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/retention/apply")
async def retention_apply(
    body: ApplyBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """IRREVERSIBLE (no restore from this API; recovery = operator backup runbook).

    Requires ``confirm`` and an ``expectedCount`` equal to the current plan so a stale
    plan cannot silently delete more. Held projects are skipped and reported.
    Deletes at most 10,000 events per call.
    """
    _admin(p)
    if not body.confirm:
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", "confirm required"))
    async with request.app.state.session_factory() as db:
        plan = await _plan(db, p.teamId, datetime.now(UTC))
        will = sum(x["eligible"] for x in plan if not x["held"])
        if will != body.expected_count:
            raise HTTPException(409, detail=error_body("PLAN_CHANGED", f"plan now deletes {will}"))
        deleted = 0
        for item in plan:
            if item["held"] or item["eligible"] == 0 or deleted >= RETENTION_BATCH:
                continue
            ids = (
                (
                    await db.execute(
                        select(AgentEvent.seq)
                        .where(
                            AgentEvent.project_id == item["projectId"],
                            AgentEvent.ts < item["cutoff"],
                        )
                        .order_by(AgentEvent.seq)
                        .limit(RETENTION_BATCH - deleted)
                    )
                )
                .scalars()
                .all()
            )
            if ids:
                await db.execute(delete(AgentEvent).where(AgentEvent.seq.in_(ids)))
                deleted += len(ids)
        await db.commit()
        await emit_identity_event(
            db,
            "retention.applied",
            actor_user_id=p.userId,
            team_id=p.teamId,
            target_type="team",
            target_id=p.teamId,
            detail={"deleted": deleted, "skippedHeld": sum(1 for x in plan if x["held"])},
        )
    return {
        "deleted": deleted,
        "skippedHeld": [x["projectId"] for x in plan if x["held"]],
        "remaining": max(will - deleted, 0),
    }
