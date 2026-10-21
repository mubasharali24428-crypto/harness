"""Versioned, team-scoped outbound audit webhooks (part 49).

Only identity fields from the append-only audit stream leave the service. The
destination must be HTTPS and match the operator's exact host allow-list.
Delivery is durable, signed, idempotent and retried with a bounded schedule.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, error_body
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .auth import get_principal
from .crypto import KeyVault
from .models import Base, JSONVariant, uid

logger = logging.getLogger(__name__)

# Matching the existing identity policy table keeps these routes inside its
# frozen classification contract: reads are read, every mutation administer.
router = APIRouter(prefix="/api/v1/teams/{team_id}/settings/integrations/v1/webhooks")
MAX_ATTEMPTS = 6
MAX_BATCH = 100
LEASE_SECONDS = 60
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 3600
WEBHOOK_VERSION = "1"
WEBHOOK_EVENT_TYPES = frozenset(
    {
        "run.paused",
        "run.resumed",
        "approval.requested",
        "approval.approved",
        "approval.rejected",
        "plan.approved",
        "plan.rejected",
        "org.member_added",
        "org.member_role_changed",
        "billing.plan_changed",
        "policy.saved",
        "emergency.granted",
        "workspace.archived",
        "workspace.restored",
        "artifact.uploaded",
        "export.completed",
        "deletion.executed",
    }
)


class IntegrationWebhook(Base):
    __tablename__ = "integration_webhooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    secret_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Part 49.003 (migration 0060): 'harness' signed JSON envelope or 'slack' chat line.
    payload_format: Mapped[str] = mapped_column(
        String(12), nullable=False, default="harness", server_default="harness"
    )
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookDelivery(Base):
    __tablename__ = "integration_webhook_deliveries"
    __table_args__ = (UniqueConstraint("webhook_id", "audit_seq", name="uq_webhook_audit_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    webhook_id: Mapped[str] = mapped_column(
        ForeignKey("integration_webhooks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    audit_seq: Mapped[int] = mapped_column(
        ForeignKey("audit_log.seq", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookCreateBody(BaseModel):
    endpointUrl: str = Field(min_length=12, max_length=2048)
    eventTypes: list[str] = Field(min_length=1, max_length=40)
    # Part 49.003: a chat integration is an explicit choice by a team admin, per webhook.
    payloadFormat: Literal["harness", "slack"] = "harness"


class WebhookPatchBody(BaseModel):
    active: bool
    expectedVersion: int = Field(ge=1)


class WebhookItem(BaseModel):
    id: str
    endpointUrl: str
    eventTypes: list[str]
    secretHint: str
    active: bool
    version: int
    createdAt: str
    payloadFormat: str = "harness"


class WebhookCreated(WebhookItem):
    signingSecret: str


def _now() -> datetime:
    return datetime.now(UTC)


def _team_admin(principal: Principal, team_id: str) -> None:
    if principal.teamId != team_id:
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "team not found"))
    role = getattr(principal.role, "value", principal.role)
    if role != "admin":
        raise HTTPException(status_code=403, detail=error_body("FORBIDDEN", "team admin required"))


def _allowed_hosts() -> set[str]:
    return {
        part.strip().lower().rstrip(".")
        for part in os.getenv("HARNESS_WEBHOOK_ALLOWED_HOSTS", "").split(",")
        if part.strip()
    }


def _validate_url(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=error_body("INVALID_WEBHOOK_URL", "invalid webhook URL"),
        ) from None
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        port = None
        host = ""
    if (
        parsed.scheme != "https"
        or not host
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or host not in _allowed_hosts()
    ):
        raise HTTPException(
            status_code=422,
            detail=error_body(
                "INVALID_WEBHOOK_URL", "HTTPS URL host must be operator-allow-listed"
            ),
        )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
            (".localhost", ".local")
        ):
            raise HTTPException(
                status_code=422,
                detail=error_body("INVALID_WEBHOOK_URL", "local destinations are forbidden"),
            ) from None
    else:
        if not ip.is_global:
            raise HTTPException(
                status_code=422,
                detail=error_body("INVALID_WEBHOOK_URL", "non-public destinations are forbidden"),
            )
    return url.strip()


def _host_is_public(host: str) -> bool:
    """Reject private DNS answers at delivery time; egress firewall remains required."""
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(answers) and all(ipaddress.ip_address(row[4][0]).is_global for row in answers)
    except (OSError, ValueError):
        return False


def _public(row: IntegrationWebhook) -> dict[str, Any]:
    return {
        "id": row.id,
        "endpointUrl": row.endpoint_url,
        "eventTypes": row.event_types,
        "secretHint": row.secret_hint,
        "active": row.active,
        "version": row.version,
        "createdAt": row.created_at.isoformat(),
        "payloadFormat": row.payload_format or "harness",
    }


def _chat_text(payload: dict[str, Any]) -> str:
    """Part 49.003: the one-line chat message for a 'slack' webhook. Built only from the closed
    id payload (_audit_id_payload), so it carries no more than the signed envelope does."""
    resource = payload.get("resource") or {}
    return (
        f"Harness: {payload['eventType']} on {resource.get('type')} {resource.get('id')}"
        f" (team {payload['teamId']}, {payload['occurredAt']})"
    )


def _audit_id_payload(row: Any, team_id: str) -> dict[str, Any]:
    """Closed payload projection. Never forwards audit detail/free text."""
    return {
        "schemaVersion": WEBHOOK_VERSION,
        "eventId": str(row.seq),
        "eventType": row.action,
        "teamId": team_id,
        "occurredAt": row.ts.isoformat(),
        "actorKind": row.actor_kind,
        "actorId": row.actor_member_id,
        "resource": {"type": row.target_type, "id": row.target_id},
    }


async def enqueue_audit_event(db: AsyncSession, row: Any) -> int:
    """Persist selected audit events for active team endpoints in the same DB."""
    detail = row.detail if isinstance(row.detail, dict) else {}
    team_id = detail.get("teamId")
    if not isinstance(team_id, str) or not team_id:
        return 0
    hooks = (
        (
            await db.execute(
                select(IntegrationWebhook).where(
                    IntegrationWebhook.team_id == team_id,
                    IntegrationWebhook.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    count = 0
    for hook in hooks:
        if row.action not in hook.event_types:
            continue
        db.add(
            WebhookDelivery(
                id=uid(),
                webhook_id=hook.id,
                team_id=team_id,
                audit_seq=row.seq,
                event_type=row.action,
                payload=_audit_id_payload(row, team_id),
                state="pending",
                attempts=0,
                next_attempt_at=_now(),
                created_at=_now(),
            )
        )
        count += 1
    if count:
        await db.flush()
    return count


@router.get("", response_model=dict)
async def list_webhooks(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    _team_admin(principal, team_id)
    async with request.app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(IntegrationWebhook)
                    .where(IntegrationWebhook.team_id == team_id)
                    .order_by(IntegrationWebhook.created_at, IntegrationWebhook.id)
                )
            )
            .scalars()
            .all()
        )
    return {"items": [_public(row) for row in rows]}


@router.get("/event-types")
async def list_event_types(
    team_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    _team_admin(principal, team_id)
    return {"items": sorted(WEBHOOK_EVENT_TYPES)}


@router.post("", status_code=201, response_model=WebhookCreated)
async def create_webhook(
    team_id: str,
    body: WebhookCreateBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    _team_admin(principal, team_id)
    url = _validate_url(body.endpointUrl)
    from .audit import APPEND_ONLY_ACTIONS

    registered = set(APPEND_ONLY_ACTIONS)
    if (
        len(set(body.eventTypes)) != len(body.eventTypes)
        or set(body.eventTypes) - WEBHOOK_EVENT_TYPES
        or WEBHOOK_EVENT_TYPES - registered
    ):
        raise HTTPException(
            status_code=422,
            detail=error_body(
                "INVALID_EVENT_TYPES", "eventTypes must be unique registered audit actions"
            ),
        )
    secret = secrets.token_urlsafe(32)
    now = _now()
    row = IntegrationWebhook(
        team_id=team_id,
        endpoint_url=url,
        event_types=body.eventTypes,
        secret_encrypted=request.app.state.vault.encrypt(secret),
        secret_hint=KeyVault.hint(secret),
        active=True,
        version=1,
        payload_format=body.payloadFormat,
        created_by=principal.userId,
        created_at=now,
        updated_at=now,
    )
    async with request.app.state.session_factory() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        from .audit_emit import _emit

        await _emit(
            db,
            "integration.webhook_created",
            user_id=principal.userId,
            team_id=team_id,
            target_type="integration_webhook",
            target_id=row.id,
            detail={"version": row.version, "eventCount": len(row.event_types),
                    "payloadFormat": row.payload_format},
        )
    return {**_public(row), "signingSecret": secret}


@router.patch("/{webhook_id}")
async def set_webhook_active(
    team_id: str,
    webhook_id: str,
    body: WebhookPatchBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    _team_admin(principal, team_id)
    async with request.app.state.session_factory() as db:
        row = await db.get(IntegrationWebhook, webhook_id)
        if row is None or row.team_id != team_id:
            raise HTTPException(
                status_code=404, detail=error_body("NOT_FOUND", "webhook not found")
            )
        if row.version != body.expectedVersion:
            raise HTTPException(
                status_code=409,
                detail=error_body("VERSION_CONFLICT", "webhook changed since it was read"),
            )
        row.active = body.active
        row.version += 1
        row.updated_at = _now()
        await db.commit()
        await db.refresh(row)
        from .audit_emit import _emit

        await _emit(
            db,
            "integration.webhook_updated",
            user_id=principal.userId,
            team_id=team_id,
            target_type="integration_webhook",
            target_id=row.id,
            detail={"active": row.active, "version": row.version},
        )
        return _public(row)


@router.get("/{webhook_id}/deliveries")
async def list_deliveries(
    team_id: str,
    webhook_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Bounded health view; response bodies and endpoint secrets are never stored."""
    _team_admin(principal, team_id)
    async with request.app.state.session_factory() as db:
        hook = await db.get(IntegrationWebhook, webhook_id)
        if hook is None or hook.team_id != team_id:
            raise HTTPException(
                status_code=404, detail=error_body("NOT_FOUND", "webhook not found")
            )
        rows = (
            (
                await db.execute(
                    select(WebhookDelivery)
                    .where(WebhookDelivery.webhook_id == webhook_id)
                    .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id)
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
    return {
        "items": [
            {
                "id": row.id,
                "eventId": str(row.audit_seq),
                "eventType": row.event_type,
                "state": row.state,
                "attempts": row.attempts,
                "nextAttemptAt": row.next_attempt_at.isoformat(),
                "lastStatusCode": row.last_status_code,
                "lastError": row.last_error,
                "createdAt": row.created_at.isoformat(),
                "deliveredAt": row.delivered_at.isoformat() if row.delivered_at else None,
            }
            for row in rows
        ]
    }


@router.post("/{webhook_id}/rotate-secret")
async def rotate_secret(
    team_id: str,
    webhook_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    _team_admin(principal, team_id)
    secret = secrets.token_urlsafe(32)
    async with request.app.state.session_factory() as db:
        row = await db.get(IntegrationWebhook, webhook_id)
        if row is None or row.team_id != team_id:
            raise HTTPException(
                status_code=404, detail=error_body("NOT_FOUND", "webhook not found")
            )
        row.secret_encrypted = request.app.state.vault.encrypt(secret)
        row.secret_hint = KeyVault.hint(secret)
        row.version += 1
        row.updated_at = _now()
        await db.commit()
        from .audit_emit import _emit

        await _emit(
            db,
            "integration.webhook_secret_rotated",
            user_id=principal.userId,
            team_id=team_id,
            target_type="integration_webhook",
            target_id=row.id,
            detail={"version": row.version},
        )
        return {**_public(row), "signingSecret": secret}


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    team_id: str,
    webhook_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    _team_admin(principal, team_id)
    async with request.app.state.session_factory() as db:
        row = await db.get(IntegrationWebhook, webhook_id)
        if row is None or row.team_id != team_id:
            return
        await db.delete(row)
        await db.commit()
        from .audit_emit import _emit

        await _emit(
            db,
            "integration.webhook_deleted",
            user_id=principal.userId,
            team_id=team_id,
            target_type="integration_webhook",
            target_id=webhook_id,
        )


async def _http_sender(url: str, body: bytes, headers: dict[str, str]) -> int:
    host = urlsplit(url).hostname or ""
    if host.lower() not in _allowed_hosts() or not _host_is_public(host):
        raise ValueError("destination_not_allowed")
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False) as client:
        response = await client.post(url, content=body, headers=headers)
        return response.status_code


def _signature(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


async def deliver_due(
    session_factory,
    *,
    vault: KeyVault,
    now: datetime | None = None,
    sender=_http_sender,
) -> dict[str, int]:
    """Claim and deliver a bounded batch. Safe to invoke from a scheduled worker."""
    at = now or _now()
    totals = {"delivered": 0, "retrying": 0, "failed": 0, "skipped": 0}
    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(WebhookDelivery)
                    .where(
                        (
                            (WebhookDelivery.state == "pending")
                            & (WebhookDelivery.next_attempt_at <= at)
                        )
                        | (
                            (WebhookDelivery.state == "sending")
                            & (WebhookDelivery.claimed_at <= at - timedelta(seconds=LEASE_SECONDS))
                        )
                    )
                    .order_by(WebhookDelivery.created_at, WebhookDelivery.id)
                    .limit(MAX_BATCH)
                )
            )
            .scalars()
            .all()
        )
        claimed: list[tuple[str, str, str, dict[str, Any], int, str]] = []
        for row in rows:
            attempt_no = row.attempts + 1
            result = await db.execute(
                update(WebhookDelivery)
                .where(
                    WebhookDelivery.id == row.id,
                    WebhookDelivery.state == row.state,
                    WebhookDelivery.attempts == row.attempts,
                )
                .values(state="sending", attempts=row.attempts + 1, claimed_at=at)
            )
            if result.rowcount:
                hook = await db.get(IntegrationWebhook, row.webhook_id)
                if hook is not None and hook.active:
                    claimed.append(
                        (
                            row.id,
                            hook.endpoint_url,
                            hook.secret_encrypted,
                            row.payload,
                            attempt_no,
                            hook.payload_format or "harness",
                        )
                    )
                else:
                    totals["skipped"] += 1
                    await db.execute(
                        update(WebhookDelivery)
                        .where(WebhookDelivery.id == row.id)
                        .values(state="failed", last_error="endpoint_disabled", claimed_at=None)
                    )
        await db.commit()

    for delivery_id, url, encrypted, payload, attempt, payload_format in claimed:
        message = {"text": _chat_text(payload)} if payload_format == "slack" else payload
        body = json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
        timestamp = int(at.timestamp())
        status_code: int | None = None
        error: str | None = None
        try:
            headers = {
                "content-type": "application/json",
                "X-Harness-Event": str(payload["eventType"]),
                "X-Harness-Delivery": delivery_id,
                "X-Harness-Webhook-Version": WEBHOOK_VERSION,
                "X-Harness-Signature": _signature(vault.decrypt(encrypted), timestamp, body),
                "Idempotency-Key": delivery_id,
            }
            status_code = await sender(url, body, headers)
            if not 200 <= status_code < 300:
                error = "http_error"
        except Exception as exc:  # bounded retry records only a closed error code
            error = (
                "destination_not_allowed"
                if str(exc) == "destination_not_allowed"
                else "transport_error"
            )
        async with session_factory() as db:
            row = await db.get(WebhookDelivery, delivery_id)
            if row is None:
                continue
            row.last_status_code = status_code
            row.claimed_at = None
            if error is None:
                row.state = "delivered"
                row.delivered_at = at
                row.last_error = None
                totals["delivered"] += 1
            elif attempt >= MAX_ATTEMPTS:
                row.state = "failed"
                row.last_error = error
                totals["failed"] += 1
            else:
                row.state = "pending"
                row.last_error = error
                delay = min(RETRY_BASE_SECONDS * (2 ** (attempt - 1)), RETRY_MAX_SECONDS)
                row.next_attempt_at = at + timedelta(seconds=delay)
                totals["retrying"] += 1
            await db.commit()
    return totals
