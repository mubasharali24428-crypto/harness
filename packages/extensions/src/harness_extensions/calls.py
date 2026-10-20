"""Call orchestration: registry checks → policy → scope → external execution.

The call path is deliberately linear and auditable:
   1. resolve the server team-scoped (no-oracle 404 for foreign ids);
   2. refuse disabled/revoked servers and rejected tools;
   3. enforce the team's tool allow/deny policy (deny wins, allowlist mode);
   4. authorize via scopes (deny-by-default grants, expiry, run/project binding);
   5. execute through ExternalMcpClient (URL policy re-check + timeout);
   6. bound the observation, stamp an observationId, then emit
      tool_call.succeeded / tool_call.failed so hooks can fire (fire-and-record;
      a hook failure never masks the call result).
"""

from __future__ import annotations

from collections.abc import Callable

from .hooks import dispatch_event
from .mcp_client import ExternalMcpClient, McpClientError
from .models import CallResult, CustomTool, ServerRecord
from .policy import check_tool_policy
from .scopes import ScopeDenied, authorize_tool_call
from .store import Store


class CallDenied(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def resolve_server(store: Store, *, team_id: str, server_id: str) -> ServerRecord:
    server = store.get_server(team_id, server_id)
    if server is None:
        raise CallDenied("SERVER_NOT_FOUND", "no such MCP server")
    return server


def _resolve_tool(store: Store, *, server: ServerRecord, tool_name: str):
    tool = next((t for t in store.list_tools(server.serverId) if t.name == tool_name), None)
    if tool is None:
        raise CallDenied("TOOL_NOT_FOUND", f"server does not advertise tool {tool_name!r}")
    if not tool.callable:
        raise CallDenied(
            "TOOL_REJECTED",
            f"tool {tool_name!r} was rejected at discovery: {tool.rejectionReason}",
        )
    return tool


def _enforce_policy(store: Store, *, team_id: str, tool_name: str) -> None:
    decision = check_tool_policy(store, team_id=team_id, tool_name=tool_name)
    if not decision.allowed:
        pattern = f" (matched {decision.pattern!r})" if decision.pattern else ""
        raise CallDenied(
            "TOOL_DENIED_BY_POLICY",
            f"tool {tool_name!r} is blocked by team policy: {decision.reason}{pattern}",
        )


async def execute_call(
    store: Store,
    client: ExternalMcpClient,
    *,
    team_id: str,
    server_id: str,
    tool_name: str,
    arguments: dict,
    run_id: str | None,
    project_id: str | None,
    project_owner_lookup: Callable[[str], str | None] | None,
) -> CallResult:
    server = resolve_server(store, team_id=team_id, server_id=server_id)
    _resolve_tool(store, server=server, tool_name=tool_name)
    _enforce_policy(store, team_id=team_id, tool_name=tool_name)
    try:
        authorize_tool_call(
            store,
            team_id=team_id,
            server=server,
            tool_name=tool_name,
            run_id=run_id,
            project_id=project_id,
            project_owner_lookup=project_owner_lookup,
        )
    except ScopeDenied as denied:
        raise CallDenied(denied.code, denied.message) from denied

    try:
        result = await client.call_tool(server.url, tool_name, arguments)
    except McpClientError as exc:
        if exc.code == "MCP_TIMEOUT":
            # A timed-out call MUST NOT flip server health: the server may be
            # fine and one tool slow; health checks own that verdict.
            pass
        await dispatch_event(
            store,
            client.settings,
            client,
            team_id=team_id,
            event_type="tool_call.failed",
            payload={
                "serverId": server.serverId,
                "toolName": tool_name,
                "errorCode": exc.code,
            },
        )
        raise CallDenied(exc.code, exc.message) from exc
    result.serverId = server.serverId
    await dispatch_event(
        store,
        client.settings,
        client,
        team_id=team_id,
        event_type="tool_call.failed" if result.isError else "tool_call.succeeded",
        payload={
            "serverId": server.serverId,
            "toolName": tool_name,
            "observationId": result.observationId,
            "truncated": result.truncated,
        },
    )
    return result


async def execute_custom_tool_call(
    store: Store,
    client: ExternalMcpClient,
    *,
    team_id: str,
    custom_tool: CustomTool,
    arguments: dict,
    run_id: str | None,
    project_id: str | None,
    project_owner_lookup: Callable[[str], str | None] | None,
) -> CallResult:
    if custom_tool.status != "active":
        raise CallDenied("CUSTOM_TOOL_DISABLED", "custom tool is disabled")
    server = resolve_server(store, team_id=team_id, server_id=custom_tool.serverId)
    _resolve_tool(store, server=server, tool_name=custom_tool.sourceTool)
    _enforce_policy(store, team_id=team_id, tool_name=custom_tool.name)
    _enforce_policy(store, team_id=team_id, tool_name=custom_tool.sourceTool)

    values = dict(arguments)
    for key, pinned in custom_tool.defaultArguments.items():
        if key in values and values[key] != pinned:
            raise CallDenied("ARG_PINNED", f"argument {key!r} is pinned by this custom tool")
        values[key] = pinned
    if custom_tool.allowedArguments is not None:
        allowed = set(custom_tool.allowedArguments)
        extra = sorted(set(values) - allowed)
        if extra:
            raise CallDenied(
                "ARG_NOT_ALLOWED",
                f"argument(s) not allowed by this custom tool: {', '.join(extra)}",
            )

    try:
        authorize_tool_call(
            store,
            team_id=team_id,
            server=server,
            tool_name=custom_tool.sourceTool,
            run_id=run_id,
            project_id=project_id,
            project_owner_lookup=project_owner_lookup,
        )
    except ScopeDenied as denied:
        raise CallDenied(denied.code, denied.message) from denied

    try:
        result = await client.call_tool(server.url, custom_tool.sourceTool, values)
    except McpClientError as exc:
        await dispatch_event(
            store,
            client.settings,
            client,
            team_id=team_id,
            event_type="tool_call.failed",
            payload={
                "serverId": server.serverId,
                "toolName": custom_tool.sourceTool,
                "customTool": custom_tool.name,
                "errorCode": exc.code,
            },
        )
        raise CallDenied(exc.code, exc.message) from exc
    result.serverId = server.serverId
    result.toolName = custom_tool.name
    await dispatch_event(
        store,
        client.settings,
        client,
        team_id=team_id,
        event_type="tool_call.failed" if result.isError else "tool_call.succeeded",
        payload={
            "serverId": server.serverId,
            "toolName": custom_tool.sourceTool,
            "customTool": custom_tool.name,
            "observationId": result.observationId,
            "truncated": result.truncated,
        },
    )
    return result
