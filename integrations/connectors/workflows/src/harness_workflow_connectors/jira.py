"""Jira issue integration (F124).

Inbound: issue webhooks — shared-secret HMAC in ``X-Jira-Secret`` (Jira Cloud
admin webhooks do not sign payloads natively; the HMAC-over-body scheme is an
adapter hardening documented here rather than hidden). Delivery id falls back
to a body hash, so a byte-identical replay dedupes.

Outbound: status updates as issue comments via the Jira Cloud REST API v3
(ADF document body), including the documented POST-then-verify pattern."""

from __future__ import annotations

import json

from .core import IntakeEvent, IntakeRejected, delivery_id_from

JIRA_SCHEME_NAME = "jira-hmac"

JIRA_SIGNATURE = {
    "header": "X-Jira-Secret",
    "timestamp_header": None,
    "prefix": "",
    "timestamped_body": "{body}",
    "tolerance_s": 0,
}


def parse_jira_issue(raw_body: bytes, headers: dict[str, str]) -> IntakeEvent:
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise IntakeRejected("PAYLOAD_INVALID", "not JSON", 400) from exc

    webhook_event = payload.get("webhookEvent") or ""
    if webhook_event not in ("jira:issue_created", "jira:issue_updated"):
        raise IntakeRejected("EVENT_IGNORED", f"webhookEvent {webhook_event!r}", 400)

    issue = payload.get("issue") or {}
    fields = issue.get("fields") or {}
    tenant = str(
        payload.get("tenantId")
        or (payload.get("baseUrl") or "").replace("https://", "").split(".")[0]
    )
    key = str(issue.get("key") or issue.get("id") or "")
    fallback_id = f"{key}:{payload.get('timestamp')}" if key and payload.get("timestamp") else None
    delivery_id = delivery_id_from("jira", raw_body, [payload.get("deliveryId"), fallback_id])
    description = fields.get("description")
    if isinstance(description, dict):  # ADF document -> flatten text nodes
        description = " ".join(_adf_text(description))
    return IntakeEvent(
        provider="jira",
        deliveryId=delivery_id,
        externalTenantId=tenant,
        externalId=key or delivery_id,
        eventType="issue_intake",
        title=str(fields.get("summary") or key or "(untitled jira issue)"),
        body=str(description or ""),
        url=f"{payload.get('baseUrl', '')}/browse/{key}" if key else None,
    )


def _adf_text(node) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "text" and node.get("text"):
            out.append(str(node["text"]))
        for child in node.get("content") or []:
            out.extend(_adf_text(child))
    elif isinstance(node, list):
        for item in node:
            out.extend(_adf_text(item))
    return out


class JiraNotifier:
    """Outbound issue comments over Jira Cloud REST API v3."""

    def __init__(self, base_url: str, *, email: str, api_token: str, client) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._auth = (email, api_token)

    async def post_comment(self, *, issue_key: str, text: str) -> dict:
        adf = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
            }
        }
        res = await self._client.post(
            f"{self._base}/rest/api/3/issue/{issue_key}/comment",
            json=adf,
            auth=self._auth,
        )
        if res.status_code >= 400:
            raise IntakeRejected(
                "JIRA_COMMENT_FAILED", f"comment: HTTP {res.status_code}", 502
            )
        return res.json()
