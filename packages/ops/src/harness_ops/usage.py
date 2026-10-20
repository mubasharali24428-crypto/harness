"""F120 — Usage APIs and webhooks (finance/security-facing).

Usage snapshots reuse the telemetry metric semantics (accepted change =
PR + independent review pass). Webhooks are registered per team, signed with
HMAC-SHA256 over ``{timestamp}.{body}`` (same construction as the rest of the
harness), retried with a bounded backoff on 5xx/429/network errors, and carry
a stable ``deliveryId`` so receivers can dedupe replays — ``DeliveryDeduper``
is a working reference receiver-side helper, exercised in tests.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from harness_telemetry import RunRecord, cost_metrics, quality_metrics

from .store import OpsStore, WebhookRow

KNOWN_EVENTS = {"alert.opened", "alert.reminder", "alert.resolved", "usage.report"}
SIGNATURE_HEADER = "X-Harness-Signature"
TIMESTAMP_HEADER = "X-Harness-Timestamp"
DELIVERY_HEADER = "X-Harness-Delivery"


class UsageWebhookError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


def validate_webhook_url(url: str, *, allow_private: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        if not (allow_private and parsed.scheme == "http"):
            raise UsageWebhookError("URL_INSECURE", "webhook URL must be https")
    host = parsed.hostname or ""
    if not host:
        raise UsageWebhookError("URL_INVALID", "webhook URL has no host")
    if allow_private and host in ("localhost", "127.0.0.1", "::1"):
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return  # DNS name — resolution policy is the caller's egress concern
    if address.is_private or address.is_loopback or address.is_link_local:
        raise UsageWebhookError(
            "URL_PRIVATE", "webhook URL resolves to a private address"
        )


def register_webhook(
    store: OpsStore,
    *,
    url: str,
    events: list[str],
    team_id: str | None,
    created_by: str,
    now: datetime,
    allow_private: bool = False,
    secret: str | None = None,
) -> tuple[WebhookRow, str]:
    """Returns (row, secret) — the plaintext secret is shown exactly once."""
    validate_webhook_url(url, allow_private=allow_private)
    unknown = set(events) - KNOWN_EVENTS
    if unknown:
        raise UsageWebhookError("EVENT_UNKNOWN", f"unknown events: {sorted(unknown)}")
    if not events:
        raise UsageWebhookError("EVENT_REQUIRED", "subscribe to at least one event")
    row = WebhookRow(
        webhookId=f"wh-{uuid.uuid4().hex[:12]}",
        url=url,
        events=sorted(events),
        secret=secret or f"whsec_{secrets.token_hex(24)}",
        teamId=team_id,
        active=True,
        createdBy=created_by,
        createdAt=now.astimezone(UTC).isoformat(),
    )
    store.insert_webhook(row)
    return row, row.secret


def delivery_signature(secret: str, body: bytes, timestamp: str) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def verify_delivery_signature(
    secret: str,
    body: bytes,
    timestamp: str,
    provided: str,
    *,
    tolerance_s: int = 300,
    now: float | None = None,
) -> bool:
    if not timestamp.isdigit():
        return False
    skew = abs((now if now is not None else time.time()) - int(timestamp))
    if skew > tolerance_s:
        return False
    return hmac.compare_digest(delivery_signature(secret, body, timestamp), provided)


@dataclass
class DeliveryResult:
    deliveryId: str
    webhookId: str
    eventType: str
    attempts: int
    status: str  # delivered | failed | skipped
    responseCode: int | None = None
    error: str | None = None


async def deliver_event(
    *,
    store: OpsStore,
    webhook: WebhookRow,
    event_type: str,
    payload: dict,
    http: httpx.AsyncClient,
    now: datetime,
    sleeper: Callable[[float], object] | None = None,
    max_attempts: int = 3,
    backoff_s: tuple[float, ...] = (0.0, 2.0, 5.0),
    delivery_id: str | None = None,
    timestamp: str | None = None,
) -> DeliveryResult:
    if event_type not in webhook.events:
        return DeliveryResult(
            deliveryId=delivery_id or "-", webhookId=webhook.webhookId,
            eventType=event_type, attempts=0, status="skipped",
        )
    if sleeper is None:
        import asyncio

        async def sleeper(seconds: float) -> None:
            if seconds:
                await asyncio.sleep(seconds)

    delivery_id = delivery_id or f"dlv-{uuid.uuid4().hex[:12]}"
    ts = timestamp or str(int(now.timestamp()))
    body = json.dumps(
        {"deliveryId": delivery_id, "event": event_type,
         "sentAt": now.astimezone(UTC).isoformat(), "payload": payload},
        sort_keys=True,
    ).encode()
    headers = {
        SIGNATURE_HEADER: delivery_signature(webhook.secret, body, ts),
        TIMESTAMP_HEADER: ts,
        DELIVERY_HEADER: delivery_id,
        "Content-Type": "application/json",
    }

    response_code: int | None = None
    error: str | None = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            await sleeper(backoff_s[min(attempt - 1, len(backoff_s) - 1)])
        try:
            res = await http.post(webhook.url, content=body, headers=headers)
            response_code = res.status_code
            error = None
            if 200 <= res.status_code < 300:
                store.record_delivery(
                    delivery_id=delivery_id, webhook_id=webhook.webhookId,
                    event_type=event_type, attempt=attempt, status="delivered",
                    response_code=response_code, error=None, at=now,
                )
                return DeliveryResult(delivery_id, webhook.webhookId, event_type,
                                      attempt, "delivered", response_code)
            if res.status_code < 500 and res.status_code != 429:
                store.record_delivery(
                    delivery_id=delivery_id, webhook_id=webhook.webhookId,
                    event_type=event_type, attempt=attempt, status="failed",
                    response_code=response_code, error=f"HTTP {res.status_code}", at=now,
                )
                return DeliveryResult(delivery_id, webhook.webhookId, event_type,
                                      attempt, "failed", response_code,
                                      f"HTTP {res.status_code}")
        except httpx.HTTPError as exc:
            response_code = None
            error = type(exc).__name__
        store.record_delivery(
            delivery_id=delivery_id, webhook_id=webhook.webhookId,
            event_type=event_type, attempt=attempt, status="retrying",
            response_code=response_code, error=error, at=now,
        )

    store.record_delivery(
        delivery_id=delivery_id, webhook_id=webhook.webhookId, event_type=event_type,
        attempt=max_attempts, status="failed", response_code=response_code,
        error=error or f"HTTP {response_code}", at=now,
    )
    return DeliveryResult(delivery_id, webhook.webhookId, event_type, max_attempts,
                          "failed", response_code, error or f"HTTP {response_code}")


async def dispatch_event(
    *,
    store: OpsStore,
    event_type: str,
    payload: dict,
    http: httpx.AsyncClient,
    now: datetime,
    team_id: str | None = None,
    sleeper: Callable[[float], object] | None = None,
) -> list[DeliveryResult]:
    """Fan out to every active webhook subscribed to ``event_type`` (and team,
    when the webhook is team-scoped)."""
    results = []
    for webhook in store.webhooks(active_only=True):
        if event_type not in webhook.events:
            continue
        if webhook.teamId is not None and team_id is not None \
                and webhook.teamId != team_id:
            continue
        results.append(
            await deliver_event(
                store=store, webhook=webhook, event_type=event_type, payload=payload,
                http=http, now=now, sleeper=sleeper,
            )
        )
    return results


class DeliveryDeduper:
    """Reference receiver-side idempotency helper: first deliveryId wins."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def first_seen(self, delivery_id: str) -> bool:
        if delivery_id in self._seen:
            return False
        self._seen.add(delivery_id)
        return True


def usage_snapshot(
    records: list[RunRecord],
    *,
    team: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    as_of: datetime,
) -> dict:
    costs = cost_metrics(records, team=team, from_=from_, to=to)
    quality = quality_metrics(records, as_of=as_of, team=team, from_=from_, to=to)
    return {
        "scope": {"team": team or "all", "from": _iso(from_), "to": _iso(to)},
        "tasks": costs["plannedTasks"],
        "acceptedChanges": costs["acceptedChanges"],
        "claimedCompletions": costs["claimedCompletions"],
        "totalCostUsd": costs["totalCost"],
        "costPerTask": costs["costPerTask"],
        "costPerClaimedCompletion": costs["costPerClaimedCompletion"],
        "costPerAcceptedChange": costs["costPerAcceptedChange"],
        "reworkRate": quality["reworkRate"],
        "abandonmentRate": quality["abandonmentRate"],
        "definitions": costs["definitions"] | quality["definitions"],
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
