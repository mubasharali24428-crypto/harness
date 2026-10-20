"""Datadog / Grafana incident-to-agent workflow (F127).

Inbound: alerting webhooks in the Alertmanager-compatible shape that BOTH
Grafana alerting and Datadog (as receivers) can deliver: ``{"alerts": [...],
"status": "firing|resolved", ...}`` signed with a shared-secret HMAC header
(``X-Alert-Secret``) — a documented adapter hardening; Grafana itself also
supports basic auth, Datadog arbitrary headers.

Only ``firing`` alerts enter intake. A firing alert proposes a plan carrying
the alert context as UNTRUSTED text; a human approves before anything runs."""

from __future__ import annotations

import hashlib
import json

from .core import IntakeEvent, IntakeRejected, delivery_id_from

ALERT_SCHEME_NAME = "alert-hmac"

ALERT_SIGNATURE = {
    "header": "X-Alert-Secret",
    "timestamp_header": None,
    "prefix": "",
    "timestamped_body": "{body}",
    "tolerance_s": 0,
}


def parse_alert_payload(raw_body: bytes, headers: dict[str, str]) -> IntakeEvent:
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise IntakeRejected("PAYLOAD_INVALID", "not JSON", 400) from exc

    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        raise IntakeRejected("PAYLOAD_INVALID", "no alerts array", 400)

    firing = [a for a in alerts if (a.get("status") or payload.get("status")) == "firing"]
    if not firing:
        raise IntakeRejected("EVENT_IGNORED", "no firing alerts", 400)

    first = firing[0]
    labels = first.get("labels") or {}
    annotations = first.get("annotations") or {}
    provider = str(payload.get("source") or "grafana")
    tenant = str(
        labels.get("org_id")
        or labels.get("datadog_org")
        or payload.get("orgId")
        or ""
    )
    fingerprint = str(first.get("fingerprint") or "")
    starts_at = str(first.get("startsAt") or "")
    delivery_id = delivery_id_from(
        "alerts",
        raw_body,
        [fingerprint and f"{fingerprint}:{starts_at}"],
    )
    title = str(annotations.get("summary") or labels.get("alertname") or "(firing alert)")
    body_lines = [
        f"alertname: {labels.get('alertname', '')}",
        f"severity: {labels.get('severity', '')}",
        str(annotations.get("description") or ""),
        f"source: {provider}",
        f"fingerprint: {fingerprint[:16]}" if fingerprint else "",
    ]
    return IntakeEvent(
        provider="alerts",
        deliveryId=delivery_id,
        externalTenantId=tenant,
        externalId=fingerprint or delivery_id,
        eventType="alert_firing",
        title=title,
        body="\n".join(line for line in body_lines if line.strip()),
        url=first.get("generatorURL") or first.get("dashboardURL"),
    )


def alert_dedupe_key(first_alert: dict) -> str:
    """Stable key for a firing episode (fingerprint + startsAt)."""
    return hashlib.sha256(
        f"{first_alert.get('fingerprint', '')}:{first_alert.get('startsAt', '')}".encode()
    ).hexdigest()
