"""A1-S5 — self-serve workspace lifecycle, trial-to-paid, org admin overview, compliance evidence.

Namespace ``/api/v1/identity``. "Organization" here is the team/workspace — a multi-team
org hierarchy is NOT modeled; the admin overview is the team-admin console data API.
Mutations are admin-only; unknown/foreign ids never reveal existence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, error_body
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from .audit_emit import emit_identity_event
from .auth import get_principal
from .billing import BillingPlan, _billing_mode
from .model_policy_api import load_policy
from .models import (
    ApiKey,
    AuditLog,
    AuthSession,
    KeyShare,
    ModelAllowlistEntry,
    PackageRevocation,
    PackageTrustIssuer,
    RetentionPolicy,
    ScimToken,
    Team,
    TeamInvite,
    TeamMember,
    TeamTrial,
)
from .sso import TeamSsoConfig

router = APIRouter(prefix="/api/v1/identity")

MAX_WORKSPACES_PER_USER = 5
TRIAL_PLAN = "pro"
EVIDENCE_DOMAIN = b"harness-compliance-evidence-v1|"


def _admin(p: Principal) -> None:
    if p.role != Role.ADMIN:
        raise HTTPException(403, detail=error_body("FORBIDDEN", "admin role required"))


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def trial_days() -> int:
    try:
        return max(1, min(int(os.environ.get("HARNESS_TRIAL_DAYS", "14")), 60))
    except ValueError:
        return 14


# ------------------------------------------------------------------ workspaces


class WorkspaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    model_config = {"extra": "forbid"}


@router.post("/workspaces", status_code=201)
async def create_workspace(
    body: WorkspaceBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """Create ANOTHER workspace owned (admin) by the caller; sign in with its ``teamId``."""
    name = body.name.strip()
    if not name:
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", "name required"))
    async with request.app.state.session_factory() as db:
        owned = (
            await db.execute(
                select(func.count())
                .select_from(TeamMember)
                .join(Team, Team.id == TeamMember.team_id)
                .where(TeamMember.user_id == p.userId, Team.closed_at.is_(None))
            )
        ).scalar_one()
        if owned >= MAX_WORKSPACES_PER_USER:
            raise HTTPException(
                409, detail=error_body("WORKSPACE_LIMIT", "workspace limit reached")
            )
        team = Team(name=name)
        db.add(team)
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=p.userId, display_name=name, role="admin"))
        await db.commit()
        await emit_identity_event(
            db,
            "workspace.created",
            actor_user_id=p.userId,
            team_id=team.id,
            target_type="team",
            target_id=team.id,
        )
    return {"teamId": team.id, "name": name, "role": "admin"}


@router.patch("/workspace")
async def rename_workspace(
    body: WorkspaceBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    _admin(p)
    async with request.app.state.session_factory() as db:
        await db.execute(update(Team).where(Team.id == p.teamId).values(name=body.name.strip()))
        await db.commit()
        await emit_identity_event(
            db,
            "workspace.renamed",
            actor_user_id=p.userId,
            team_id=p.teamId,
            target_type="team",
            target_id=p.teamId,
        )
    return {"teamId": p.teamId, "name": body.name.strip()}


class CloseBody(BaseModel):
    confirm_name: str = Field(alias="confirmName", max_length=200)

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/workspace/close")
async def close_workspace(
    body: CloseBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """Close the caller's workspace: nobody can sign in, keys/SCIM/shares are revoked.

    Data is NOT deleted (audit history is RESTRICT-protected; deletion is an operator
    task). Refused while a paid plan is active — cancel the subscription first.
    """
    _admin(p)
    async with request.app.state.session_factory() as db:
        team = await db.get(Team, p.teamId)
        if team is None or team.name != body.confirm_name:
            raise HTTPException(
                422,
                detail=error_body("VALIDATION_ERROR", "confirmName must equal the workspace name"),
            )
        plan = (
            await db.execute(select(BillingPlan.plan).where(BillingPlan.team_id == p.teamId))
        ).scalar_one_or_none()
        if plan not in (None, "free"):
            raise HTTPException(
                409, detail=error_body("PAID_PLAN_ACTIVE", "cancel the paid plan first")
            )
        now = datetime.now(UTC)
        team.closed_at = now
        await db.execute(delete(AuthSession).where(AuthSession.team_id == p.teamId))
        await db.execute(
            update(ApiKey)
            .where(ApiKey.team_id == p.teamId, ApiKey.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.execute(
            update(KeyShare)
            .where(KeyShare.team_id == p.teamId, KeyShare.status.in_(("pending", "active")))
            .values(status="revoked", revoked_at=now)
        )
        await db.execute(
            update(ScimToken)
            .where(ScimToken.team_id == p.teamId, ScimToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.commit()
        await emit_identity_event(
            db,
            "workspace.closed",
            actor_user_id=p.userId,
            team_id=p.teamId,
            target_type="team",
            target_id=p.teamId,
        )
    return {"teamId": p.teamId, "closed": True}


# ------------------------------------------------------------------ trial


async def lazy_expire_trial(db, team_id: str, now: datetime | None = None) -> TeamTrial | None:
    """Expire this team's trial if due; reverts the plan to free ONLY if still the trial plan."""
    now = now or datetime.now(UTC)
    trial = await db.get(TeamTrial, team_id)
    if (
        trial
        and trial.converted_at is None
        and trial.expired_at is None
        and _aware(trial.ends_at) <= now
    ):
        trial.expired_at = now
        plan_row = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
        ).scalar_one_or_none()
        if plan_row is not None and plan_row.plan == trial.plan:
            plan_row.plan = "free"
            plan_row.updated_at = now
        await db.commit()
        await emit_identity_event(
            db,
            "trial.expired",
            actor_user_id="",
            team_id=team_id,
            target_type="team",
            target_id=team_id,
        )
    return trial


async def expire_due_trials(session_factory, now: datetime | None = None) -> int:
    """Operator sweep (cron): expire every due trial. Returns how many."""
    now = now or datetime.now(UTC)
    async with session_factory() as db:
        ids = (
            (
                await db.execute(
                    select(TeamTrial.team_id).where(
                        TeamTrial.converted_at.is_(None),
                        TeamTrial.expired_at.is_(None),
                        TeamTrial.ends_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
    for tid in ids:
        async with session_factory() as db:
            await lazy_expire_trial(db, tid, now)
    return len(ids)


def _trial_public(trial: TeamTrial | None, now: datetime) -> dict:
    if trial is None:
        return {"status": "none", "trialDays": trial_days()}
    status = "converted" if trial.converted_at else "expired" if trial.expired_at else "active"
    ends = _aware(trial.ends_at)
    return {
        "status": status,
        "plan": trial.plan,
        "endsAt": ends.isoformat(),
        "daysLeft": max(0, (ends - now).days) if status == "active" else 0,
    }


@router.post("/trial/start", status_code=201)
async def start_trial(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict:
    """One trial per team, ever; only from the free tier; refused while a provider owns billing."""
    _admin(p)
    if _billing_mode() == "stripe":
        raise HTTPException(
            409, detail=error_body("BILLING_MANAGED_BY_PROVIDER", "start a trial via checkout")
        )
    now = datetime.now(UTC)
    async with request.app.state.session_factory() as db:
        if await db.get(TeamTrial, p.teamId) is not None:
            raise HTTPException(
                409, detail=error_body("TRIAL_USED", "this workspace already had a trial")
            )
        plan_row = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == p.teamId))
        ).scalar_one_or_none()
        if plan_row is not None and plan_row.plan != "free":
            raise HTTPException(
                409, detail=error_body("ALREADY_PAID", "workspace is not on the free tier")
            )
        trial = TeamTrial(
            team_id=p.teamId,
            plan=TRIAL_PLAN,
            started_at=now,
            ends_at=now + timedelta(days=trial_days()),
        )
        db.add(trial)
        if plan_row is None:
            from decimal import Decimal

            db.add(BillingPlan(team_id=p.teamId, plan=TRIAL_PLAN, monthly_budget_usd=Decimal("0")))
        else:
            plan_row.plan, plan_row.updated_at = TRIAL_PLAN, now
        await db.commit()
        await emit_identity_event(
            db,
            "trial.started",
            actor_user_id=p.userId,
            team_id=p.teamId,
            target_type="team",
            target_id=p.teamId,
            detail={"plan": TRIAL_PLAN},
        )
    return _trial_public(trial, now)


@router.get("/trial")
async def trial_state(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict:
    async with request.app.state.session_factory() as db:
        trial = await lazy_expire_trial(db, p.teamId)
        plan = (
            await db.execute(select(BillingPlan.plan).where(BillingPlan.team_id == p.teamId))
        ).scalar_one_or_none()
    return {**_trial_public(trial, datetime.now(UTC)), "currentPlan": plan or "free"}


# ------------------------------------------------------------------ org overview


async def _count(db, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one())


@router.get("/org/overview")
async def org_overview(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict:
    """Admin-console data: governance posture of THIS workspace (counts and flags only)."""
    _admin(p)
    t = p.teamId
    async with request.app.state.session_factory() as db:
        trial = await lazy_expire_trial(db, t)
        roles = Counter()
        deactivated = 0
        for role, off in (
            await db.execute(
                select(TeamMember.role, TeamMember.deactivated_at).where(TeamMember.team_id == t)
            )
        ).all():
            if off is None:
                roles[str(getattr(role, "value", role)).lower()] += 1
            else:
                deactivated += 1
        now = datetime.now(UTC)
        pending_invites = [
            e
            for e in (
                await db.execute(
                    select(TeamInvite.expires_at).where(
                        TeamInvite.team_id == t,
                        TeamInvite.redeemed_at.is_(None),
                        TeamInvite.revoked_at.is_(None),
                    )
                )
            ).scalars()
            if _aware(e) > now
        ]
        policy = await load_policy(db, t)
        plan = (
            await db.execute(select(BillingPlan.plan).where(BillingPlan.team_id == t))
        ).scalar_one_or_none()
        sso = (
            await db.execute(select(TeamSsoConfig.enabled).where(TeamSsoConfig.team_id == t))
        ).scalar_one_or_none()
        holds = await _count(
            db,
            select(func.count())
            .select_from(RetentionPolicy)
            .where(RetentionPolicy.team_id == t, RetentionPolicy.legal_hold.is_(True)),
        )
        out = {
            "teamId": t,
            "members": {
                "byRole": dict(roles),
                "deactivated": deactivated,
                "pendingInvites": len(pending_invites),
            },
            "billing": {
                "plan": plan or "free",
                "mode": _billing_mode() or "off",
                "trial": _trial_public(trial, now),
            },
            "identity": {
                "ssoConfigured": sso is not None,
                "ssoEnabled": bool(sso),
                "scimActiveTokens": await _count(
                    db,
                    select(func.count())
                    .select_from(ScimToken)
                    .where(ScimToken.team_id == t, ScimToken.revoked_at.is_(None)),
                ),
            },
            "keys": {
                "teamKeys": await _count(
                    db,
                    select(func.count())
                    .select_from(ApiKey)
                    .where(
                        ApiKey.team_id == t,
                        ApiKey.owner_user_id.is_(None),
                        ApiKey.revoked_at.is_(None),
                    ),
                ),
                "personalKeys": await _count(
                    db,
                    select(func.count())
                    .select_from(ApiKey)
                    .where(
                        ApiKey.team_id == t,
                        ApiKey.owner_user_id.is_not(None),
                        ApiKey.revoked_at.is_(None),
                    ),
                ),
                "activeShares": await _count(
                    db,
                    select(func.count())
                    .select_from(KeyShare)
                    .where(KeyShare.team_id == t, KeyShare.status == "active"),
                ),
            },
            "models": {
                "allowlistEnforced": policy.allowlist_enforced,
                "approved": len(policy.approved_models),
                "pendingRequests": await _count(
                    db,
                    select(func.count())
                    .select_from(ModelAllowlistEntry)
                    .where(
                        ModelAllowlistEntry.team_id == t, ModelAllowlistEntry.status == "pending"
                    ),
                ),
                "fallbackEnabled": policy.fallback_enabled,
                "cacheEnabled": policy.cache_enabled,
            },
            "residency": {
                "enforced": policy.residency_enforced,
                "allowedRegions": sorted(policy.allowed_regions),
            },
            "supplyChain": {
                "activeIssuers": await _count(
                    db,
                    select(func.count())
                    .select_from(PackageTrustIssuer)
                    .where(PackageTrustIssuer.team_id == t, PackageTrustIssuer.status == "active"),
                ),
                "revocations": await _count(
                    db,
                    select(func.count())
                    .select_from(PackageRevocation)
                    .where(PackageRevocation.team_id == t),
                ),
            },
            "retention": {
                "policies": await _count(
                    db,
                    select(func.count())
                    .select_from(RetentionPolicy)
                    .where(RetentionPolicy.team_id == t),
                ),
                "legalHolds": holds,
            },
        }
    try:
        from .personal_keys_api import _cap_and_used
        from .spend import budget_alert

        cap, used = await _cap_and_used(request, t)
        out["spend"] = budget_alert(cap, used, 0)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 — console degrades honestly instead of failing
        out["spend"] = {"level": "unknown", "degraded": True}
    return out


# ------------------------------------------------------------------ compliance evidence


def _evidence_key(request: Request) -> bytes:
    master = request.app.state.settings.master_key
    return hmac.new(master, b"harness-evidence-key-v1", hashlib.sha256).digest()


def _canon(doc: Any) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode()


def _sign(key: bytes, body: dict) -> dict:
    raw = _canon(body)
    return {
        "alg": "HMAC-SHA256",
        "digest": hashlib.sha256(raw).hexdigest(),
        "signature": hmac.new(key, EVIDENCE_DOMAIN + raw, hashlib.sha256).hexdigest(),
    }


@router.get("/compliance/evidence")
async def compliance_evidence(
    request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """Redacted, machine-verifiable evidence (counts/flags/ids; no emails, keys, content)."""
    _admin(p)
    overview = await org_overview(request, p)
    since = datetime.now(UTC) - timedelta(days=90)
    async with request.app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditLog).where(AuditLog.ts >= since).order_by(AuditLog.seq).limit(20000)
                )
            )
            .scalars()
            .all()
        )
    mine = [r for r in rows if (r.detail or {}).get("teamId") == p.teamId]
    chain = hashlib.sha256()
    for r in mine:
        chain.update(f"{r.seq}|{r.action}|{_aware(r.ts).isoformat()}\n".encode())
    by_action = Counter(r.action for r in mine)
    body = {
        "schemaVersion": "evidence/1",
        "teamId": p.teamId,
        "generatedAt": datetime.now(UTC).isoformat(),
        "controls": [
            {"id": "access_control", "evidence": {**overview["members"], **overview["identity"]}},
            {
                "id": "plan_approval_gate",
                "evidence": {
                    "approvals": by_action.get("plan.approved", 0),
                    "rejections": by_action.get("plan.rejected", 0),
                    "note": "gate + expiry live in Section 4; this counts recorded decisions",
                },
            },
            {
                "id": "audit_trail",
                "evidence": {
                    "windowDays": 90,
                    "events": len(mine),
                    "byAction": dict(sorted(by_action.items())),
                    "chainSha256": chain.hexdigest(),
                    "firstSeq": mine[0].seq if mine else None,
                    "lastSeq": mine[-1].seq if mine else None,
                },
            },
            {"id": "data_retention", "evidence": overview["retention"]},
            {"id": "data_residency", "evidence": overview["residency"]},
            {"id": "model_governance", "evidence": overview["models"]},
            {"id": "supply_chain", "evidence": overview["supplyChain"]},
            {"id": "key_management", "evidence": overview["keys"]},
        ],
        "limits": [
            "Counts and configuration flags only; this is not an audit opinion or certification.",
            "Residency reflects team-declared provider regions, not verified provider behavior.",
            "Audit chain covers this team's rows from the last 90 days (max 20000 scanned).",
        ],
    }
    async with request.app.state.session_factory() as db:
        await emit_identity_event(
            db,
            "compliance.evidence_exported",
            actor_user_id=p.userId,
            team_id=p.teamId,
            target_type="team",
            target_id=p.teamId,
            detail={"events": len(mine)},
        )
    return {**body, "integrity": _sign(_evidence_key(request), body)}


class VerifyEvidenceBody(BaseModel):
    document: dict[str, Any]

    model_config = {"extra": "forbid"}


@router.post("/compliance/evidence/verify")
async def verify_evidence(
    body: VerifyEvidenceBody, request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """True only if THIS deployment issued exactly this document, for THIS team."""
    _admin(p)
    doc = dict(body.document)
    integrity = doc.pop("integrity", None)
    if not isinstance(integrity, dict):
        return {"valid": False, "reason": "evidence.no_integrity"}
    if doc.get("teamId") != p.teamId:
        return {"valid": False, "reason": "evidence.wrong_team"}
    good = _sign(_evidence_key(request), doc)
    ok = hmac.compare_digest(str(integrity.get("signature", "")), good["signature"])
    return {"valid": ok, "reason": "evidence.verified" if ok else "evidence.tampered"}
