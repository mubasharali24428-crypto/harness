"""Linear issue integration (F123).

Inbound: issue webhooks (``Issue`` create/update) — Linear-Signature scheme:
HMAC-SHA256 of the raw body with the webhook signing secret, hex, no prefix;
Linear-Delivery header carries the delivery id (dedupe key).

Outbound: status updates as issue comments via the Linear GraphQL API; issue
status fetch for read-back verification.

Local fake provider mirrors the documented shapes; no live Linear workspace
is contacted (no OAuth token available here — recorded as a limit)."""

from __future__ import annotations

import json

from .core import IntakeEvent, IntakeRejected, delivery_id_from

LINEAR_SCHEME_NAME = "linear"

LINEAR_SIGNATURE = {
    "header": "Linear-Signature",
    "timestamp_header": None,
    "prefix": "",
    "timestamped_body": "{body}",
    "tolerance_s": 0,
}


def parse_linear_issue(raw_body: bytes, headers: dict[str, str]) -> IntakeEvent:
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise IntakeRejected("PAYLOAD_INVALID", "not JSON", 400) from exc

    if payload.get("type") not in ("Issue", "issue"):
        raise IntakeRejected("EVENT_IGNORED", f"type {payload.get('type')!r}", 400)

    action = payload.get("action")
    if action not in ("create", None):
        raise IntakeRejected("EVENT_IGNORED", f"action {action!r}", 400)

    data = payload.get("data") or {}
    org_id = str(payload.get("organizationId") or payload.get("organization_id") or "")
    identifier = str(data.get("identifier") or data.get("id") or "")
    headers_l = {k.lower(): v for k, v in headers.items()}
    delivery_id = delivery_id_from(
        "linear", raw_body, [headers_l.get("linear-delivery"), data.get("id")]
    )
    return IntakeEvent(
        provider="linear",
        deliveryId=delivery_id,
        externalTenantId=org_id,
        externalId=identifier or delivery_id,
        eventType="issue_intake",
        title=str(data.get("title") or identifier or "(untitled linear issue)"),
        body=str(data.get("description") or ""),
        url=data.get("url"),
    )


class LinearNotifier:
    """Outbound issue comments over the Linear GraphQL API."""

    def __init__(self, base_url: str, *, api_key: str, client) -> None:
        self._url = f"{base_url.rstrip('/')}/graphql"
        self._headers = {"Authorization": api_key}
        self._client = client

    async def post_comment(self, *, issue_id: str, body: str) -> dict:
        query = (
            "mutation CommentCreate($input: CommentCreateInput!) {"
            " commentCreate(input: $input) { success comment { id url } } }"
        )
        res = await self._client.post(
            self._url,
            json={"query": query, "variables": {"input": {"issueId": issue_id, "body": body}}},
            headers=self._headers,
        )
        data = res.json() if res.status_code < 500 else {}
        created = (((data.get("data") or {}).get("commentCreate")) or {})
        if res.status_code >= 400 or not created.get("success"):
            raise IntakeRejected(
                "LINEAR_COMMENT_FAILED",
                f"commentCreate: HTTP {res.status_code} {data.get('errors', '')}",
                502,
            )
        return created["comment"]
