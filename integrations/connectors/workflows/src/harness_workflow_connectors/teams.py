"""Microsoft Teams task intake + status updates (F122).

Inbound: outgoing-webhook deliveries signed with a shared secret presented as
``Authorization: Bearer <secret>`` (an adapter simplification of Teams'
per-tenant signing story, documented rather than hidden; the HMAC framework in
``core`` is scheme-parameterized so a stricter Teams scheme can drop in).

Outbound: message cards POSTed to a channel webhook URL (the standard Teams
incoming-webhook shape used for status updates)."""

from __future__ import annotations

import json

from .core import IntakeEvent, IntakeRejected, delivery_id_from

TEAMS_SCHEME_NAME = "teams-bearer"

TEAMS_SIGNATURE = {
    "header": "Authorization",
    "timestamp_header": None,
    "prefix": "Bearer ",
    "timestamped_body": "{body}",  # body is signed directly
    "tolerance_s": 0,
}


def parse_teams_message(raw_body: bytes, headers: dict[str, str]) -> IntakeEvent:
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise IntakeRejected("PAYLOAD_INVALID", "not JSON", 400) from exc

    if payload.get("type") != "message":
        raise IntakeRejected("EVENT_IGNORED", f"type {payload.get('type')!r}", 400)

    channel = payload.get("channelData") or {}
    tenant = channel.get("tenant") or {}
    text = str(payload.get("text") or "")
    delivery_id = delivery_id_from(
        "teams", raw_body, [payload.get("id"), (payload.get("channelData") or {}).get("messageId")]
    )
    return IntakeEvent(
        provider="teams",
        deliveryId=delivery_id,
        externalTenantId=str(tenant.get("id") or ""),
        externalId=str(payload.get("id") or delivery_id),
        eventType="task_intake",
        title=(text.splitlines()[0][:200] if text else "(empty teams message)"),
        body=text,
        url=(payload.get("conversation") or {}).get("id"),
    )


class TeamsNotifier:
    """Outbound status updates into a channel via incoming-webhook message card."""

    def __init__(self, webhook_url: str, *, client) -> None:
        self._url = webhook_url
        self._client = client

    async def post_status(self, *, text: str, title: str = "Harness status") -> dict:
        res = await self._client.post(
            self._url,
            json={"@type": "MessageCard", "@context": "https://schema.org/extensions",
                  "summary": title, "title": title, "text": text},
        )
        if res.status_code >= 400:
            raise IntakeRejected("TEAMS_POST_FAILED", f"webhook: HTTP {res.status_code}", 502)
        return {"status": res.status_code}
