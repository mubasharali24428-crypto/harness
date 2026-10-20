"""Sentry error-to-agent workflow (F126).

Inbound: Sentry issue webhooks — the real ``Sentry-Hook-Signature`` scheme
(HMAC-SHA256 hex of the raw body with the client secret) plus
``Sentry-Hook-Resource`` header checks. Only actionable issue events
(``created`` / ``unresolved``) enter intake.

The workflow is deliberately narrow: an error event proposes a plan whose goal
carries the error context as UNTRUSTED text, and the run waits for human plan
approval. There is no code path from an error to execution."""

from __future__ import annotations

import json

from .core import IntakeEvent, IntakeRejected, delivery_id_from

SENTRY_SCHEME_NAME = "sentry"

SENTRY_SIGNATURE = {
    "header": "Sentry-Hook-Signature",
    "timestamp_header": None,
    "prefix": "",
    "timestamped_body": "{body}",
    "tolerance_s": 0,
}

_ACTIONABLE = ("created", "unresolved")


def parse_sentry_issue(raw_body: bytes, headers: dict[str, str]) -> IntakeEvent:
    headers_l = {k.lower(): v for k, v in headers.items()}
    resource = headers_l.get("sentry-hook-resource", "")
    if resource != "issue":
        raise IntakeRejected("EVENT_IGNORED", f"resource {resource!r}", 400)

    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise IntakeRejected("PAYLOAD_INVALID", "not JSON", 400) from exc

    action = payload.get("action")
    if action not in _ACTIONABLE:
        raise IntakeRejected("EVENT_IGNORED", f"action {action!r}", 400)

    data = payload.get("data", {}).get("issue") or {}
    org = (payload.get("installation") or {}).get("uuid") or payload.get("organization", {}).get("slug")
    external_id = str(data.get("id") or "")
    delivery_id = delivery_id_from(
        "sentry", raw_body, [headers_l.get("request-id"), external_id and f"{external_id}:{action}"]
    )
    culprit = data.get("culprit") or ""
    body = "\n".join(
        part
        for part in (
            f"project: {data.get('project', {}).get('slug', '')}",
            f"culprit: {culprit}",
            f"count: {data.get('count', '')}",
            f"first_seen: {data.get('firstSeen', '')}",
        )
        if part.split(": ", 1)[-1]
    )
    return IntakeEvent(
        provider="sentry",
        deliveryId=delivery_id,
        externalTenantId=str(org or ""),
        externalId=external_id or delivery_id,
        eventType=f"issue_{action}",
        title=str(data.get("title") or "(sentry issue)"),
        body=body,
        url=data.get("web_url"),
    )
