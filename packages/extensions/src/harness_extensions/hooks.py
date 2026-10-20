"""Event hooks: team subscriptions that fire on tool-call events.

Two target kinds, both deny-by-default:
  * ``webhook`` — JSON POST of the event envelope; URL passes ``url_policy``
    at creation AND again at dispatch (the second check is the last line of
    defense inside the DNS-rebinding window);
  * ``mcp_tool`` — invoke a grant-covered tool with the event attached as
    ``_event``; missing/revoked grants record a ``refused`` delivery.

Delivery is honest: one bounded attempt, result recorded (delivered / failed /
refused) with duration — no fake retries, no silent drops. Dispatch is
fire-and-record and never raises into the caller's call path.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import UTC, datetime

import httpx

from .config import Settings
from .mcp_client import ExternalMcpClient, McpClientError
from .models import HookDefinition, HookDelivery
from .scopes import ScopeDenied, authorize_tool_call
from .store import Store
from .url_policy import UrlPolicyError, validate_server_url

KNOWN_EVENT_TYPES = ("tool_call.succeeded", "tool_call.failed")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class HookError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def validate_event_type(event_type: str) -> None:
    if event_type != "*" and event_type not in KNOWN_EVENT_TYPES:
        raise HookError(
            "HOOK_EVENT_INVALID",
            f"eventType must be one of {', '.join(KNOWN_EVENT_TYPES)} or '*'",
        )


def validate_hook_target(settings: Settings, target: dict) -> dict:
    if not isinstance(target, dict):
        raise HookError("HOOK_TARGET_INVALID", "target must be an object")
    kind = target.get("kind")
    if kind == "webhook":
        url = target.get("url")
        if not isinstance(url, str) or not url:
            raise HookError("HOOK_TARGET_INVALID", "webhook target requires a url")
        try:
            validate_server_url(url, allow_private_hosts=settings.allow_private_hosts)
        except UrlPolicyError as exc:
            raise HookError("HOOK_TARGET_REFUSED", f"url refused by policy: {exc}") from exc
        return {"kind": "webhook", "url": url}
    if kind == "mcp_tool":
        server_id = target.get("serverId")
        tool_name = target.get("toolName")
        if not isinstance(server_id, str) or not server_id:
            raise HookError("HOOK_TARGET_INVALID", "mcp_tool target requires serverId")
        if not isinstance(tool_name, str) or not tool_name:
            raise HookError("HOOK_TARGET_INVALID", "mcp_tool target requires toolName")
        arguments = target.get("arguments", {})
        if not isinstance(arguments, dict):
            raise HookError("HOOK_TARGET_INVALID", "mcp_tool target arguments must be an object")
        return {
            "kind": "mcp_tool",
            "serverId": server_id,
            "toolName": tool_name,
            "arguments": arguments,
        }
    raise HookError("HOOK_TARGET_INVALID", "target.kind must be 'webhook' or 'mcp_tool'")


def create_hook(
    store: Store,
    settings: Settings,
    *,
    team_id: str,
    name: str,
    event_type: str,
    target: dict,
    created_by: str,
) -> HookDefinition:
    if not _NAME_RE.match(name):
        raise HookError("HOOK_NAME_INVALID", "name must be lowercase alnum/._- (max 64)")
    validate_event_type(event_type)
    normalized = validate_hook_target(settings, target)
    if store.find_hook_by_name(team_id, name) is not None:
        raise HookError("HOOK_NAME_CONFLICT", f"hook {name!r} already exists")
    hook = HookDefinition(
        hookId=str(uuid.uuid4()),
        teamId=team_id,
        name=name,
        eventType=event_type,
        target=normalized,
        status="active",
        createdBy=created_by,
        createdAt=_now_iso(),
    )
    return store.create_hook(hook)


async def dispatch_event(
    store: Store,
    settings: Settings,
    client: ExternalMcpClient,
    *,
    team_id: str,
    event_type: str,
    payload: dict,
) -> list[HookDelivery]:
    """Deliver one event to every matching active hook. Never raises."""
    deliveries: list[HookDelivery] = []
    for hook in store.list_hooks(team_id):
        if hook.eventType not in (event_type, "*"):
            continue
        deliveries.append(
            await _deliver(
                store, settings, client, hook=hook, event_type=event_type, payload=payload
            )
        )
    return deliveries


async def _deliver(
    store: Store,
    settings: Settings,
    client: ExternalMcpClient,
    *,
    hook: HookDefinition,
    event_type: str,
    payload: dict,
) -> HookDelivery:
    started = time.perf_counter()
    try:
        if hook.target.get("kind") == "webhook":
            status, detail = await _deliver_webhook(settings, hook, event_type, payload)
        else:
            status, detail = await _deliver_mcp_tool(store, client, hook, event_type, payload)
    except (TimeoutError, httpx.HTTPError, McpClientError) as exc:
        status, detail = "failed", f"{type(exc).__name__}: {exc}"
    duration_ms = (time.perf_counter() - started) * 1000.0
    delivery = HookDelivery(
        deliveryId=str(uuid.uuid4()),
        hookId=hook.hookId,
        teamId=hook.teamId,
        eventType=event_type,
        status=status,
        detail=detail,
        durationMs=round(duration_ms, 3),
        createdAt=_now_iso(),
    )
    store.add_delivery(delivery)
    return delivery


async def _deliver_webhook(
    settings: Settings, hook: HookDefinition, event_type: str, payload: dict
) -> tuple[str, str | None]:
    url = hook.target["url"]
    try:
        validate_server_url(url, allow_private_hosts=settings.allow_private_hosts)
    except UrlPolicyError as exc:
        return "refused", f"url refused by policy at dispatch: {exc}"
    envelope = {"eventType": event_type, "payload": payload, "hookId": hook.hookId}
    async with asyncio.timeout(settings.hook_timeout_s):
        async with httpx.AsyncClient() as http:
            res = await http.post(url, json=envelope)
    if 200 <= res.status_code < 300:
        return "delivered", None
    return "failed", f"http {res.status_code}"


async def _deliver_mcp_tool(
    store: Store,
    client: ExternalMcpClient,
    hook: HookDefinition,
    event_type: str,
    payload: dict,
) -> tuple[str, str | None]:
    server = store.get_server(hook.teamId, hook.target["serverId"])
    if server is None or server.status != "enabled":
        return "refused", "target server is missing or disabled"
    try:
        authorize_tool_call(
            store,
            team_id=hook.teamId,
            server=server,
            tool_name=hook.target["toolName"],
        )
    except ScopeDenied as denied:
        return "refused", f"scope check failed: {denied.code}"
    arguments = dict(hook.target.get("arguments") or {})
    arguments["_event"] = {"eventType": event_type, "payload": payload}
    result = await client.call_tool(server.url, hook.target["toolName"], arguments)
    if result.isError:
        return "failed", "target tool returned isError"
    return "delivered", None
