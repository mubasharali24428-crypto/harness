"""A1-S2 — personal BYO-key profiles, consent-based sharing, bounded share spend.

Namespace ``/api/v1/identity`` (new endpoints only). Law:
- A personal key belongs to ONE user (``api_keys.owner_user_id``) and is invisible
  to team key surfaces. Nobody else can use it without an ACTIVE share that the
  owner offered AND the recipient accepted; either side can revoke at any time.
- The owner is the PAYER for share spend. ``committed_usd`` (reserved holds +
  settled actuals) is enforced against the owner-set cap with ONE conditional
  UPDATE, so concurrent reservations can never jointly exceed it.
- Provider usage that is unknown stays ``unsettled`` and keeps its hold; usage
  that arrives after release/revocation is still recorded (``late``) against
  the payer — money that was spent is never forgotten.
- Foreign/unknown ids answer one identical 404 (no existence oracle). Raw keys
  never leave the vault: HTTP exposes hints only; ``resolve_shared_key`` is an
  in-process API for Sections 3/4.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, can, error_body
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from .audit_emit import emit_identity_event
from .auth import get_principal
from .crypto import KeyVault
from .models import ApiKey, KeyShare, KeyShareSpend, TeamMember

router = APIRouter(prefix="/api/v1/identity")

MONEY_Q = Decimal("0.000001")
MAX_SHARE_DAYS = 365


def _nf(what: str = "not found") -> HTTPException:
    return HTTPException(404, detail=error_body("NOT_FOUND", what))


def _money(value: Any, field: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise HTTPException(
            422, detail=error_body("VALIDATION_ERROR", f"{field} invalid")
        ) from None
    if not d.is_finite() or d < 0 or d > Decimal("1000000"):
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", f"{field} out of range"))
    return d.quantize(MONEY_Q)


def _now() -> datetime:
    return datetime.now(UTC)


def _share_public(s: KeyShare) -> dict:
    cap = s.spend_cap_usd
    return {
        "id": s.id,
        "apiKeyId": s.api_key_id,
        "ownerUserId": s.owner_user_id,
        "recipientUserId": s.recipient_user_id,
        "status": s.status,
        "spendCapUsd": None if cap is None else str(cap),
        "committedUsd": str(s.committed_usd),
        "expiresAt": s.expires_at.isoformat() if s.expires_at else None,
        "consentedAt": s.consented_at.isoformat() if s.consented_at else None,
        "revokedAt": s.revoked_at.isoformat() if s.revoked_at else None,
    }


def _key_public(k: ApiKey) -> dict:
    return {
        "id": k.id,
        "label": k.label,
        "provider": k.provider,
        "keyHint": k.key_hint,
        "createdAt": k.created_at.isoformat() if k.created_at else None,
        "revokedAt": k.revoked_at.isoformat() if k.revoked_at else None,
    }


def _require_write(principal: Principal) -> None:
    if not can(principal, "write"):
        raise HTTPException(403, detail=error_body("FORBIDDEN", "role cannot manage keys"))


# ----------------------------------------------------------------- personal keys


class PersonalKeyBody(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    provider: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
    key: str = Field(min_length=4, max_length=4096)


@router.post("/personal-keys", status_code=201)
async def add_personal_key(
    body: PersonalKeyBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    _require_write(principal)
    vault: KeyVault = request.app.state.vault
    async with request.app.state.session_factory() as db:
        row = ApiKey(
            team_id=principal.teamId,
            owner_user_id=principal.userId,
            provider=body.provider,
            label=body.label.strip() or "personal key",
            key_encrypted=vault.encrypt(body.key),
            key_hint=KeyVault.hint(body.key),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        await emit_identity_event(
            db,
            "personal_key.added",
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_type="api_key",
            target_id=row.id,
            detail={"provider": row.provider},
        )
        return _key_public(row)


@router.get("/personal-keys")
async def list_personal_keys(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    async with request.app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(ApiKey)
                    .where(ApiKey.owner_user_id == principal.userId)
                    .order_by(ApiKey.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return {"items": [_key_public(r) for r in rows]}


async def _revoke_share_rows(db, where_clause) -> None:
    await db.execute(
        update(KeyShare)
        .where(where_clause, KeyShare.status.in_(("pending", "active")))
        .values(status="revoked", revoked_at=_now())
    )


@router.delete("/personal-keys/{key_id}", status_code=204)
async def remove_personal_key(
    key_id: str, request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> None:
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(ApiKey).where(ApiKey.id == key_id, ApiKey.owner_user_id == principal.userId)
            )
        ).scalar_one_or_none()
        if row is None:
            raise _nf("key not found")
        if row.revoked_at is None:
            row.revoked_at = _now()
            await _revoke_share_rows(db, KeyShare.api_key_id == row.id)
            await db.commit()
            await emit_identity_event(
                db,
                "personal_key.removed",
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                target_type="api_key",
                target_id=row.id,
            )


# ----------------------------------------------------------------- shares


class ShareBody(BaseModel):
    recipient_user_id: str = Field(alias="recipientUserId", min_length=1, max_length=36)
    spend_cap_usd: str | float | int | None = Field(default=None, alias="spendCapUsd")
    expires_in_days: int | None = Field(default=30, alias="expiresInDays", ge=1, le=MAX_SHARE_DAYS)

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/personal-keys/{key_id}/shares", status_code=201)
async def offer_share(
    key_id: str,
    body: ShareBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    _require_write(principal)
    if body.recipient_user_id == principal.userId:
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", "cannot share with self"))
    cap = None if body.spend_cap_usd is None else _money(body.spend_cap_usd, "spendCapUsd")
    async with request.app.state.session_factory() as db:
        key = (
            await db.execute(
                select(ApiKey).where(
                    ApiKey.id == key_id,
                    ApiKey.owner_user_id == principal.userId,
                    ApiKey.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        member = (
            await db.execute(
                select(TeamMember.id).where(
                    TeamMember.team_id == principal.teamId,
                    TeamMember.user_id == body.recipient_user_id,
                )
            )
        ).scalar_one_or_none()
        if key is None or member is None:  # same answer: no membership oracle
            raise _nf()
        share = KeyShare(
            api_key_id=key.id,
            owner_user_id=principal.userId,
            recipient_user_id=body.recipient_user_id,
            team_id=principal.teamId,
            spend_cap_usd=cap,
            expires_at=_now() + timedelta(days=body.expires_in_days)
            if body.expires_in_days
            else None,
        )
        db.add(share)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                409, detail=error_body("CONFLICT", "a live share already exists")
            ) from None
        await db.refresh(share)
        await emit_identity_event(
            db,
            "key_share.offered",
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_type="key_share",
            target_id=share.id,
            detail={"recipientUserId": share.recipient_user_id, "capUsd": str(cap)},
        )
        return _share_public(share)


@router.get("/shares")
async def list_shares(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    role: str = "received",
) -> dict:
    if role not in ("given", "received"):
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", "role invalid"))
    col = KeyShare.owner_user_id if role == "given" else KeyShare.recipient_user_id
    async with request.app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(KeyShare)
                    .where(col == principal.userId)
                    .order_by(KeyShare.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return {"items": [_share_public(r) for r in rows]}


async def _transition(request, principal, share_id, *, who, frm, to, action):
    async with request.app.state.session_factory() as db:
        col = KeyShare.recipient_user_id if who == "recipient" else KeyShare.owner_user_id
        values: dict[str, Any] = {"status": to}
        if to == "active":
            values["consented_at"] = _now()
        res = await db.execute(
            update(KeyShare)
            .where(KeyShare.id == share_id, col == principal.userId, KeyShare.status.in_(frm))
            .values(**values)
        )
        await db.commit()
        share = (
            await db.execute(
                select(KeyShare).where(KeyShare.id == share_id, col == principal.userId)
            )
        ).scalar_one_or_none()
        if share is None:
            raise _nf()
        if res.rowcount != 1:
            raise HTTPException(409, detail=error_body("CONFLICT", f"share is {share.status}"))
        await emit_identity_event(
            db,
            action,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_type="key_share",
            target_id=share.id,
        )
        return _share_public(share)


@router.post("/shares/{share_id}/accept")
async def accept_share(
    share_id: str, request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    return await _transition(
        request,
        principal,
        share_id,
        who="recipient",
        frm=("pending",),
        to="active",
        action="key_share.accepted",
    )


@router.post("/shares/{share_id}/decline")
async def decline_share(
    share_id: str, request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    return await _transition(
        request,
        principal,
        share_id,
        who="recipient",
        frm=("pending",),
        to="declined",
        action="key_share.declined",
    )


@router.delete("/shares/{share_id}")
async def revoke_share(
    share_id: str, request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """Owner OR recipient may revoke at any time (consent is revocable by both)."""
    async with request.app.state.session_factory() as db:
        share = (
            await db.execute(
                select(KeyShare).where(
                    KeyShare.id == share_id,
                    or_(
                        KeyShare.owner_user_id == principal.userId,
                        KeyShare.recipient_user_id == principal.userId,
                    ),
                )
            )
        ).scalar_one_or_none()
        if share is None:
            raise _nf()
        if share.status in ("pending", "active"):
            await _revoke_share_rows(db, KeyShare.id == share.id)
            await db.commit()
            await db.refresh(share)
            await emit_identity_event(
                db,
                "key_share.revoked",
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                target_type="key_share",
                target_id=share.id,
            )
        return _share_public(share)


# ----------------------------------------------------------------- bounded spend


class ReserveBody(BaseModel):
    amount_usd: str | float | int = Field(alias="amountUsd")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=128)

    model_config = {"populate_by_name": True, "extra": "forbid"}


def _res_public(r: KeyShareSpend, share: KeyShare, **extra: Any) -> dict:
    return {
        "id": r.id,
        "shareId": r.share_id,
        "status": r.status,
        "reservedUsd": str(r.reserved_usd),
        "actualUsd": None if r.actual_usd is None else str(r.actual_usd),
        "shareCommittedUsd": str(share.committed_usd),
        **extra,
    }


async def _recipient_share(db, share_id: str, user_id: str) -> KeyShare:
    share = (
        await db.execute(
            select(KeyShare).where(KeyShare.id == share_id, KeyShare.recipient_user_id == user_id)
        )
    ).scalar_one_or_none()
    if share is None:
        raise _nf()
    return share


async def _existing(db, share_id: str, idem: str) -> KeyShareSpend | None:
    return (
        await db.execute(
            select(KeyShareSpend).where(
                KeyShareSpend.share_id == share_id, KeyShareSpend.idempotency_key == idem
            )
        )
    ).scalar_one_or_none()


def _usable_clause(user_id: str, now: datetime):
    live_key = select(ApiKey.id).where(ApiKey.revoked_at.is_(None)).scalar_subquery()
    return (
        KeyShare.recipient_user_id == user_id,
        KeyShare.status == "active",
        or_(KeyShare.expires_at.is_(None), KeyShare.expires_at > now),
        KeyShare.api_key_id.in_(live_key),
    )


@router.post("/shares/{share_id}/reservations", status_code=201)
async def reserve(
    share_id: str,
    body: ReserveBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    amount = _money(body.amount_usd, "amountUsd")
    async with request.app.state.session_factory() as db:
        share = await _recipient_share(db, share_id, principal.userId)
        prior = await _existing(db, share_id, body.idempotency_key)
        if prior is not None:
            if prior.reserved_usd != amount:
                raise HTTPException(409, detail=error_body("CONFLICT", "idempotency key reused"))
            return _res_public(prior, share, replayed=True)
        # ONE conditional UPDATE = the check and the hold. Concurrent callers
        # serialize on the row; the cap can never be jointly exceeded.
        res = await db.execute(
            update(KeyShare)
            .where(
                KeyShare.id == share_id,
                *_usable_clause(principal.userId, _now()),
                or_(
                    KeyShare.spend_cap_usd.is_(None),
                    KeyShare.committed_usd + amount <= KeyShare.spend_cap_usd,
                ),
            )
            .values(committed_usd=KeyShare.committed_usd + amount)
        )
        if res.rowcount != 1:
            await db.rollback()
            share = await _recipient_share(db, share_id, principal.userId)
            reason = "share_not_usable" if share.status != "active" else "cap_or_expiry_or_key"
            raise HTTPException(403, detail=error_body("SHARE_DENIED", reason))
        row = KeyShareSpend(
            share_id=share_id, idempotency_key=body.idempotency_key, reserved_usd=amount
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:  # racing retry of the same key: hold rolled back with it
            await db.rollback()
            prior = await _existing(db, share_id, body.idempotency_key)
            share = await _recipient_share(db, share_id, principal.userId)
            if prior is None or prior.reserved_usd != amount:
                raise HTTPException(
                    409, detail=error_body("CONFLICT", "reservation conflict")
                ) from None
            return _res_public(prior, share, replayed=True)
        await db.refresh(row)
        await db.refresh(share)
        return _res_public(row, share)


class SettleBody(BaseModel):
    actual_usd: str | float | int | None = Field(default=None, alias="actualUsd")

    model_config = {"populate_by_name": True, "extra": "forbid"}


async def _load_res(db, share_id: str, rid: str) -> KeyShareSpend:
    row = (
        await db.execute(
            select(KeyShareSpend).where(KeyShareSpend.id == rid, KeyShareSpend.share_id == share_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise _nf()
    return row


@router.post("/shares/{share_id}/reservations/{rid}/settle")
async def settle(
    share_id: str,
    rid: str,
    body: SettleBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Record provider usage. ``actualUsd`` omitted/null = usage UNKNOWN: the hold stays.

    Allowed even after the share was revoked or the hold released: the money was
    spent, so it is attributed to the payer (``late: true`` when it re-opens a release).
    """
    actual = None if body.actual_usd is None else _money(body.actual_usd, "actualUsd")
    async with request.app.state.session_factory() as db:
        share = await _recipient_share(db, share_id, principal.userId)
        row = await _load_res(db, share_id, rid)
        if row.status == "settled":
            if actual is not None and actual != row.actual_usd:
                raise HTTPException(409, detail=error_body("CONFLICT", "already settled"))
            return _res_public(row, share, replayed=True)
        prev = row.status
        if actual is None:
            if prev == "released":
                raise HTTPException(409, detail=error_body("CONFLICT", "released; actual required"))
            claim = await db.execute(
                update(KeyShareSpend)
                .where(KeyShareSpend.id == rid, KeyShareSpend.status == "reserved")
                .values(status="unsettled")
            )
            await db.commit()
            await db.refresh(row)
            await db.refresh(share)
            return _res_public(row, share, unsettledTransition=claim.rowcount == 1)
        # Claim the transition atomically; only the winner adjusts the share total.
        claim = await db.execute(
            update(KeyShareSpend)
            .where(KeyShareSpend.id == rid, KeyShareSpend.status == prev)
            .values(status="settled", actual_usd=actual, settled_at=_now())
        )
        if claim.rowcount == 1:
            held = Decimal("0") if prev == "released" else row.reserved_usd
            await db.execute(
                update(KeyShare)
                .where(KeyShare.id == share_id)
                .values(committed_usd=KeyShare.committed_usd + (actual - held))
            )
        await db.commit()
        await db.refresh(row)
        await db.refresh(share)
        over = share.spend_cap_usd is not None and share.committed_usd > share.spend_cap_usd
        return _res_public(row, share, late=prev == "released", overCap=over)


@router.post("/shares/{share_id}/reservations/{rid}/release")
async def release(
    share_id: str,
    rid: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Return an unused hold (call never reached the provider). Idempotent."""
    async with request.app.state.session_factory() as db:
        share = await _recipient_share(db, share_id, principal.userId)
        row = await _load_res(db, share_id, rid)
        claim = await db.execute(
            update(KeyShareSpend)
            .where(KeyShareSpend.id == rid, KeyShareSpend.status == "reserved")
            .values(status="released")
        )
        if claim.rowcount == 1:
            await db.execute(
                update(KeyShare)
                .where(KeyShare.id == share_id)
                .values(committed_usd=KeyShare.committed_usd - row.reserved_usd)
            )
        await db.commit()
        await db.refresh(row)
        await db.refresh(share)
        if row.status not in ("released",):
            raise HTTPException(409, detail=error_body("CONFLICT", f"reservation is {row.status}"))
        return _res_public(row, share)


# ----------------------------------------------------------------- in-process resolver


async def resolve_shared_key(
    db, vault: KeyVault, *, share_id: str, user_id: str
) -> tuple[str, str]:
    """(plaintext_key, payer_user_id) iff the share is usable by ``user_id`` NOW.

    In-process only (Section 3/4 call this at provider-call time, so revocation
    between queueing and execution takes effect). Raises PermissionError otherwise.
    """
    row = (
        await db.execute(
            select(KeyShare, ApiKey)
            .join(ApiKey, ApiKey.id == KeyShare.api_key_id)
            .where(KeyShare.id == share_id, *_usable_clause(user_id, _now()))
        )
    ).first()
    if row is None:
        raise PermissionError("share not usable")
    share, key = row
    return vault.decrypt(key.key_encrypted), share.owner_user_id


# ----------------------------------------------------------------- spend intelligence
# F087 forecast / F088 anomalies / F143 usage statement / F144 alerts. Read-only,
# team-scoped through the principal; math lives in spend.py.

from sqlalchemy import text  # noqa: E402

from .billing import BillingPlan, _used_this_month_stmt  # noqa: E402
from .models import AgentRun, CostLedger, Project  # noqa: E402
from .spend import (  # noqa: E402
    Sample,
    budget_alert,
    detect_spend_anomaly,
    predict_tokens,
    reconcile_usage,
)

_HISTORY_LIMIT = 500


def _team_ledger(team_id: str):
    return (
        select(CostLedger)
        .join(AgentRun, AgentRun.id == CostLedger.run_id)
        .join(Project, Project.id == AgentRun.project_id)
        .where(Project.team_id == team_id)
    )


@router.get("/spend/forecast")
async def spend_forecast(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    promptTokens: int = 0,  # noqa: N803 — camelCase query per api-conventions
    model: str | None = None,
) -> dict:
    if not 0 <= promptTokens <= 10_000_000:
        raise HTTPException(422, detail=error_body("VALIDATION_ERROR", "promptTokens out of range"))
    stmt = _team_ledger(principal.teamId)
    if model:
        stmt = stmt.where(CostLedger.model == model)
    async with request.app.state.session_factory() as db:
        rows = (
            (await db.execute(stmt.order_by(CostLedger.created_at.desc()).limit(_HISTORY_LIMIT)))
            .scalars()
            .all()
        )
    samples = [
        Sample(r.input_tokens, r.output_tokens, Decimal(str(r.cost_usd)), r.created_at)
        for r in rows
    ]
    return predict_tokens(samples, promptTokens)


@router.get("/spend/anomalies")
async def spend_anomalies(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    today = _now().date()
    since = _now() - timedelta(days=31)
    async with request.app.state.session_factory() as db:
        rows = (
            (await db.execute(_team_ledger(principal.teamId).where(CostLedger.created_at >= since)))
            .scalars()
            .all()
        )
    daily = {(today - timedelta(days=i)).isoformat(): Decimal("0") for i in range(31)}
    first = min((r.created_at for r in rows), default=None)
    for r in rows:
        key = r.created_at.date().isoformat()
        if key in daily:
            daily[key] += Decimal(str(r.cost_usd))
    if first is not None:  # days before the first ledger row are unknown, not zero
        daily = {d: v for d, v in daily.items() if d >= first.date().isoformat()}
    else:
        daily = {}
    return detect_spend_anomaly(daily, today.isoformat())


async def _cap_and_used(request, team_id: str) -> tuple[Decimal | None, Decimal]:
    from harness_contracts.budget import resolve_cap

    async with request.app.state.session_factory() as db:
        # teams.budget_usd (migration 0007) has no ORM mirror: raw SQL, as billing does.
        team_budget = (
            await db.execute(text("SELECT budget_usd FROM teams WHERE id = :t"), {"t": team_id})
        ).scalar()
        plan = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
        ).scalar_one_or_none()
        used = (await db.execute(_used_this_month_stmt(team_id))).scalar_one()
    cap = resolve_cap(team_budget, plan.monthly_budget_usd if plan else None).cap_usd
    return cap, Decimal(str(used))


@router.get("/spend/alerts")
async def spend_alerts(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict:
    """Monthly ceiling state. The hard ceiling itself is enforced by Section 3's
    atomic reservation; this reports it. Delivery (email/UI) is not done here."""
    cap, used = await _cap_and_used(request, principal.teamId)
    return budget_alert(cap, used, Decimal("0"))


@router.get("/usage-statement")
async def usage_statement(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    providerUsd: str | None = None,  # noqa: N803
) -> dict:
    """This month's team usage by model, reconciled against provider-reported usage.

    ``providerUsd`` omitted => reconciliation is ``unsettled`` (unknown, not zero).
    """
    provider = None if providerUsd is None else _money(providerUsd, "providerUsd")
    async with request.app.state.session_factory() as db:
        by_model = (
            await db.execute(
                text(
                    "SELECT l.model AS model, COALESCE(SUM(l.cost_usd), 0) AS usd, "
                    "COALESCE(SUM(l.input_tokens), 0) AS tin, "
                    "COALESCE(SUM(l.output_tokens), 0) AS tout "
                    "FROM cost_ledger l JOIN agent_runs r ON r.id = l.run_id "
                    "JOIN projects p ON p.id = r.project_id "
                    "WHERE p.team_id = :t AND l.created_at >= date_trunc('month', now()) "
                    "GROUP BY l.model ORDER BY l.model"
                ),
                {"t": principal.teamId},
            )
        ).all()
        total = (await db.execute(_used_this_month_stmt(principal.teamId))).scalar_one()
        holds = (
            await db.execute(
                select(KeyShare.owner_user_id, KeyShare.committed_usd).where(
                    KeyShare.team_id == principal.teamId
                )
            )
        ).all()
    payers: dict[str, Decimal] = {}
    for owner, committed in holds:
        payers[owner] = payers.get(owner, Decimal("0")) + Decimal(str(committed))
    total_dec = Decimal(str(total))
    return {
        "byModel": [
            {
                "model": m,
                "usd": str(Decimal(str(u)).quantize(MONEY_Q)),
                "inputTokens": int(i),
                "outputTokens": int(o),
            }
            for m, u, i, o in by_model
        ],
        "totalUsd": str(total_dec.quantize(MONEY_Q)),
        "sharePayers": [{"userId": k, "committedUsd": str(v)} for k, v in sorted(payers.items())],
        "reconciliation": reconcile_usage(total_dec, provider),
    }
