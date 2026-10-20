"""Slack task intake + status updates (F121).

Inbound: Events API webhook — Slack signing (v0 scheme: HMAC of
``v0:{timestamp}:{body}``, 5-minute replay window) and ``event_id`` dedupe.
Outbound: ``chat.postMessage`` for status notifications (bot token).

Adapter note: the message payload shapes below follow Slack's documented
Events API and Web API contract; they are validated against a local fake
provider, not a live workspace (no Slack app authorization in this
environment — recorded as an integration limit, not hidden)."""

from __future__ import annotations

import json

from .core import IntakeEvent, IntakeRejected, delivery_id_from

SLACK_SCHEME_NAME = "slack-v0"

SLACK_SIGNATURE = {
    "header": "X-Slack-Signature",
    "timestamp_header": "X-Slack-Request-Timestamp",
    "prefix": "v0=",
    "timestamped_body": "v0:{ts}:{body}",
    "tolerance_s": 300,
}


def parse_slack_events(raw_body: bytes, headers: dict[str, str]) -> IntakeEvent:
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise IntakeRejected("PAYLOAD_INVALID", "not JSON", 400) from exc

    if payload.get("type") == "url_verification":
        raise IntakeRejected("URL_VERIFICATION", "challenge handshake", 400)

    if payload.get("type") != "event_callback":
        raise IntakeRejected("PAYLOAD_INVALID", f"unexpected type {payload.get('type')!r}", 400)

    event = payload.get("event") or {}
    if event.get("type") != "message" or event.get("subtype"):
        raise IntakeRejected("EVENT_IGNORED", f"event type {event.get('type')!r}", 400)

    delivery_id = delivery_id_from(
        "slack", raw_body, [payload.get("event_id"), event.get("client_msg_id")]
    )
    text = str(event.get("text") or "")
    title = text.splitlines()[0][:200] if text else "(empty slack message)"
    return IntakeEvent(
        provider="slack",
        deliveryId=delivery_id,
        externalTenantId=str(payload.get("team_id") or ""),
        externalId=str(event.get("ts") or delivery_id),
        eventType="task_intake",
        title=title,
        body=text,
        url=None,
    )


class SlackNotifier:
    """Outbound status updates via chat.postMessage (bot token)."""

    def __init__(self, base_url: str, *, bot_token: str, client) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {bot_token}"}
        self._client = client

    async def post_status(self, *, channel: str, text: str, thread_ts: str | None = None) -> dict:
        body: dict = {"channel": channel, "text": text}
        if thread_ts:
            body["thread_ts"] = thread_ts
        res = await self._client.post(
            f"{self._base}/api/chat.postMessage", json=body, headers=self._headers
        )
        data = res.json()
        if res.status_code >= 400 or not data.get("ok", False):
            raise IntakeRejected(
                "SLACK_POST_FAILED", f"chat.postMessage: {data.get('error', res.status_code)}", 502
            )
        return data
