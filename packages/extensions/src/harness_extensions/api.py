"""Extensions-plane HTTP service: /api/v1/extensions (§ A5-S1).

Follows the frozen conventions: bearer identity resolved by shared middleware,
camelCase bodies, the `{"error": {code, message}}` envelope on every non-2xx,
plural kebab-free nouns, `POST /resource/{id}/{action}` for non-CRUD actions.
The service derives team/principal ONLY from the authenticated context — body
teamId/userId fields are never accepted.

Local team roles gate writes: admin/engineer may register, grant, curate and
install; viewer/guest read only; revoking a plugin installation is admin-only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

import harness_contracts as contracts
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.requests import Request as FastAPIRequest
from fastapi.responses import JSONResponse
from harness_contracts.roles import Principal, Role, can
from pydantic import BaseModel, Field

from .calls import CallDenied, execute_call, execute_custom_tool_call
from .commands import (
    CommandError,
    create_command,
    invoke_command,
    rendered_digest,
)
from .config import Settings
from .hooks import (
    HookError,
    create_hook,
    dispatch_event,
)
from .marketplace import (
    Catalog,
    MarketplaceError,
    install_plugin,
    load_catalog,
    revoke_plugin,
    uninstall_plugin,
)
from .mcp_client import ExternalMcpClient, McpClientError
from .models import CustomTool, ServerRecord, ToolPolicyRule
from .policy import check_tool_policy
from .skills import (
    SkillError,
    add_skill_version,
    create_skill,
    get_skill_version,
)
from .store import Store
from .url_policy import UrlPolicyError, validate_server_url

router = APIRouter(prefix=f"{contracts.API_PREFIX}/extensions")

_SERVER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_ARGUMENTS_BYTES = 256 * 1024

_HEALTHY = "healthy"


def http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


async def get_principal(request: Request) -> Principal:
    """Auth resolution via app.state.get_principal; fail closed (D6/P0.6)."""
    dep = getattr(request.app.state, "get_principal", None)
    if dep is None:
        raise http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AUTH_NOT_CONFIGURED",
            "auth not wired",
        )
    return await dep(request)


RequirePrincipal = Annotated[Principal, Depends(get_principal)]


def _require_write(principal: Principal) -> None:
    if not can(principal, "write"):
        raise http_error(status.HTTP_403_FORBIDDEN, "ROLE_DENIED", "role may not modify extensions")


def _require_admin(principal: Principal) -> None:
    if principal.role != Role.ADMIN:
        raise http_error(status.HTTP_403_FORBIDDEN, "ROLE_DENIED", "admin role required")


def _store(request: Request) -> Store:
    return request.app.state.extension_store


def _client(request: Request) -> ExternalMcpClient:
    return request.app.state.mcp_client


def _catalog(request: Request) -> Catalog:
    return request.app.state.catalog


def _lookup(request: Request) -> Callable[[str], str | None] | None:
    return getattr(request.app.state, "project_owner_lookup", None)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _check_project(request: Request, team_id: str, project_id: str | None) -> None:
    if project_id is None:
        return
    lookup = _lookup(request)
    if lookup is None:
        raise http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PROJECT_CHECK_UNAVAILABLE",
            "project ownership verification is not configured on this service",
        )
    owner = lookup(project_id)
    if owner != team_id:
        raise http_error(status.HTTP_404_NOT_FOUND, "PROJECT_NOT_FOUND", "no such project")


# ============================================================== request DTOs


class RegisterServerRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=1, max_length=2048)
    projectId: str | None = None


class GrantRequest(BaseModel):
    serverId: str
    toolName: str = Field(..., min_length=1, max_length=128)
    projectId: str | None = None
    runId: str | None = None
    expiresAt: str | None = None


class CustomToolRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=1024)
    serverId: str
    sourceTool: str = Field(..., min_length=1, max_length=128)
    allowedArguments: list[str] | None = None
    defaultArguments: dict[str, Any] = Field(default_factory=dict)


class CallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    runId: str | None = None
    projectId: str | None = None


class RawCallRequest(CallRequest):
    serverId: str
    toolName: str = Field(..., min_length=1, max_length=128)


class SkillCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., min_length=1)


class SkillVersionRequest(BaseModel):
    content: str = Field(..., min_length=1)


class CommandRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    template: str = Field(..., min_length=1)
    arguments: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=1024)


class CommandInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class HookRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    eventType: str = Field(..., min_length=1, max_length=64)
    target: dict[str, Any]


class EventRequest(BaseModel):
    eventType: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class PolicyRuleRequest(BaseModel):
    mode: str = Field(..., pattern="^(allow|deny)$")
    pattern: str = Field(..., min_length=1, max_length=128)


def _check_arguments_size(arguments: dict) -> None:
    import json

    size = len(json.dumps(arguments, separators=(",", ":")).encode("utf-8"))
    if size > _MAX_ARGUMENTS_BYTES:
        raise http_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "ARGUMENTS_TOO_LARGE",
            f"arguments exceed {_MAX_ARGUMENTS_BYTES} bytes",
        )


# ================================================================== servers


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def register_server(
    body: RegisterServerRequest, request: Request, principal: RequirePrincipal
) -> ServerRecord:
    _require_write(principal)
    if not _SERVER_NAME_RE.match(body.name):
        raise http_error(
            status.HTTP_400_BAD_REQUEST,
            "SERVER_NAME_INVALID",
            "name must be lowercase [a-z0-9._-], start alphanumeric, max 64 chars",
        )
    _check_project(request, principal.teamId, body.projectId)
    store = _store(request)
    if store.find_server_by_name(principal.teamId, body.name) is not None:
        raise http_error(
            status.HTTP_409_CONFLICT, "SERVER_NAME_CONFLICT", "server name already exists in team"
        )
    try:
        validate_server_url(
            body.url, allow_private_hosts=request.app.state.settings.allow_private_hosts
        )
    except UrlPolicyError as exc:
        raise http_error(status.HTTP_400_BAD_REQUEST, exc.code, str(exc)) from exc

    import uuid as _uuid

    record = ServerRecord(
        serverId=str(_uuid.uuid4()),
        teamId=principal.teamId,
        projectId=body.projectId,
        name=body.name,
        url=body.url,
        createdBy=principal.userId,
        createdAt=_now_iso(),
    )
    store.create_server(record)
    # Best-effort initial probe: a dead server registers as unhealthy, honestly.
    try:
        discovery = await _client(request).discover(body.url)
        rejected = sum(1 for t in discovery.tools if not t.callable)
        store.record_health(
            record.serverId,
            health="degraded" if rejected else _HEALTHY,
            checked_at=_now_iso(),
            latency_ms=round(discovery.latency_ms, 3),
            error=None,
            tools=discovery.tools,
        )
    except McpClientError as exc:
        store.record_health(
            record.serverId,
            health="unhealthy",
            checked_at=_now_iso(),
            latency_ms=None,
            error=exc.message,
            tools=[],
        )
    return store.get_server(principal.teamId, record.serverId) or record


@router.get("/servers")
async def list_servers(request: Request, principal: RequirePrincipal) -> dict:
    return {"items": [s.model_dump() for s in _store(request).list_servers(principal.teamId)]}


@router.get("/servers/{server_id}")
async def get_server_route(
    server_id: str, request: Request, principal: RequirePrincipal
) -> ServerRecord:
    server = _store(request).get_server(principal.teamId, server_id)
    if server is None:
        raise http_error(status.HTTP_404_NOT_FOUND, "SERVER_NOT_FOUND", "no such MCP server")
    return server


@router.delete("/servers/{server_id}")
async def revoke_server(
    server_id: str, request: Request, principal: RequirePrincipal
) -> ServerRecord:
    _require_write(principal)
    store = _store(request)
    if not store.set_server_status(principal.teamId, server_id, "revoked"):
        raise http_error(status.HTTP_404_NOT_FOUND, "SERVER_NOT_FOUND", "no such MCP server")
    server = store.get_server(principal.teamId, server_id)
    assert server is not None
    return server


@router.post("/servers/{server_id}/health")
async def probe_server(
    server_id: str, request: Request, principal: RequirePrincipal
) -> ServerRecord:
    _require_write(principal)
    store = _store(request)
    server = resolve_or_404(store, principal.teamId, server_id)
    if server.status != "enabled":
        raise http_error(status.HTTP_409_CONFLICT, "SERVER_DISABLED", f"server is {server.status}")
    try:
        discovery = await _client(request).discover(server.url)
        rejected = sum(1 for t in discovery.tools if not t.callable)
        store.record_health(
            server.serverId,
            health="degraded" if rejected else _HEALTHY,
            checked_at=_now_iso(),
            latency_ms=round(discovery.latency_ms, 3),
            error=None,
            tools=discovery.tools,
        )
    except McpClientError as exc:
        store.record_health(
            server.serverId,
            health="unhealthy",
            checked_at=_now_iso(),
            latency_ms=None,
            error=exc.message,
            tools=[],
        )
    refreshed = store.get_server(principal.teamId, server_id)
    assert refreshed is not None
    return refreshed


@router.get("/servers/{server_id}/tools")
async def list_server_tools(server_id: str, request: Request, principal: RequirePrincipal) -> dict:
    store = _store(request)
    resolve_or_404(store, principal.teamId, server_id)
    return {"items": [t.model_dump() for t in store.list_tools(server_id)]}


def resolve_or_404(store: Store, team_id: str, server_id: str) -> ServerRecord:
    server = store.get_server(team_id, server_id)
    if server is None:
        raise http_error(status.HTTP_404_NOT_FOUND, "SERVER_NOT_FOUND", "no such MCP server")
    return server


# =================================================================== grants


@router.post("/grants", status_code=status.HTTP_201_CREATED)
async def create_grant_route(
    body: GrantRequest, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    store = _store(request)
    server = resolve_or_404(store, principal.teamId, body.serverId)
    _check_project(request, principal.teamId, body.projectId)
    if body.toolName != "*":
        tool = next((t for t in store.list_tools(server.serverId) if t.name == body.toolName), None)
        if tool is None:
            raise http_error(
                status.HTTP_404_NOT_FOUND,
                "TOOL_NOT_FOUND",
                f"server does not advertise tool {body.toolName!r}; refresh health first",
            )
        if not tool.callable:
            raise http_error(
                status.HTTP_409_CONFLICT,
                "TOOL_REJECTED",
                f"tool was rejected at discovery: {tool.rejectionReason}",
            )
        decision = check_tool_policy(store, team_id=principal.teamId, tool_name=body.toolName)
        if not decision.allowed:
            raise http_error(
                status.HTTP_409_CONFLICT,
                "TOOL_DENIED_BY_POLICY",
                f"tool {body.toolName!r} is blocked by team policy: {decision.reason}",
            )
    expires_at = body.expiresAt
    if expires_at is not None:
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            raise http_error(
                status.HTTP_400_BAD_REQUEST, "GRANT_EXPIRY_INVALID", "expiresAt must be ISO-8601"
            ) from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        if parsed <= datetime.now(UTC):
            raise http_error(
                status.HTTP_400_BAD_REQUEST, "GRANT_EXPIRY_PAST", "expiresAt must be in the future"
            )

    import uuid as _uuid

    from .models import ToolGrant

    grant = ToolGrant(
        grantId=str(_uuid.uuid4()),
        teamId=principal.teamId,
        serverId=server.serverId,
        toolName=body.toolName,
        projectId=body.projectId,
        runId=body.runId,
        expiresAt=expires_at,
        createdBy=principal.userId,
        createdAt=_now_iso(),
    )
    store.create_grant(grant)
    return grant.model_dump()


@router.get("/grants")
async def list_grants_route(request: Request, principal: RequirePrincipal) -> dict:
    return {"items": [g.model_dump() for g in _store(request).list_grants(principal.teamId)]}


@router.delete("/grants/{grant_id}")
async def revoke_grant_route(
    grant_id: str, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    store = _store(request)
    ok = store.revoke_grant(
        principal.teamId, grant_id, revoked_by=principal.userId, at=_now_iso()
    )
    if not ok:
        raise http_error(
            status.HTTP_404_NOT_FOUND, "GRANT_NOT_FOUND", "no active grant with that id"
        )
    grant = store.get_grant(principal.teamId, grant_id)
    assert grant is not None
    return grant.model_dump()


# ============================================================= custom tools


@router.post("/custom-tools", status_code=status.HTTP_201_CREATED)
async def create_custom_tool_route(
    body: CustomToolRequest, request: Request, principal: RequirePrincipal
) -> CustomTool:
    _require_write(principal)
    if not _SERVER_NAME_RE.match(body.name):
        raise http_error(
            status.HTTP_400_BAD_REQUEST,
            "CUSTOM_TOOL_NAME_INVALID",
            "name must be lowercase [a-z0-9._-], start alphanumeric, max 64 chars",
        )
    store = _store(request)
    server = resolve_or_404(store, principal.teamId, body.serverId)
    tool = next((t for t in store.list_tools(server.serverId) if t.name == body.sourceTool), None)
    if tool is None:
        raise http_error(
            status.HTTP_404_NOT_FOUND, "TOOL_NOT_FOUND", "source tool not found on this server"
        )
    if not tool.callable:
        raise http_error(
            status.HTTP_409_CONFLICT,
            "TOOL_REJECTED",
            f"source tool was rejected at discovery: {tool.rejectionReason}",
        )
    existing = [t for t in store.list_custom_tools(principal.teamId) if t.name == body.name]
    if existing:
        raise http_error(
            status.HTTP_409_CONFLICT, "CUSTOM_TOOL_NAME_CONFLICT", "custom tool name already exists"
        )
    props = tool.inputSchema.get("properties")
    if body.allowedArguments is not None and isinstance(props, dict):
        unknown = sorted(set(body.allowedArguments) - set(props))
        if unknown:
            raise http_error(
                status.HTTP_400_BAD_REQUEST,
                "ARG_UNKNOWN",
                f"allowedArguments names not in source schema: {', '.join(unknown)}",
            )
    pinned_unknown = []
    if isinstance(props, dict):
        pinned_unknown = sorted(set(body.defaultArguments) - set(props))
    if pinned_unknown:
        raise http_error(
            status.HTTP_400_BAD_REQUEST,
            "ARG_UNKNOWN",
            f"defaultArguments names not in source schema: {', '.join(pinned_unknown)}",
        )

    import uuid as _uuid

    custom = CustomTool(
        customToolId=str(_uuid.uuid4()),
        teamId=principal.teamId,
        name=body.name,
        description=body.description,
        serverId=server.serverId,
        sourceTool=body.sourceTool,
        allowedArguments=body.allowedArguments,
        defaultArguments=body.defaultArguments,
        createdBy=principal.userId,
        createdAt=_now_iso(),
    )
    store.create_custom_tool(custom)
    return custom


@router.get("/custom-tools")
async def list_custom_tools_route(request: Request, principal: RequirePrincipal) -> dict:
    return {"items": [t.model_dump() for t in _store(request).list_custom_tools(principal.teamId)]}


@router.delete("/custom-tools/{custom_tool_id}")
async def disable_custom_tool_route(
    custom_tool_id: str, request: Request, principal: RequirePrincipal
) -> CustomTool:
    _require_write(principal)
    store = _store(request)
    if not store.set_custom_tool_status(principal.teamId, custom_tool_id, "disabled"):
        raise http_error(
            status.HTTP_404_NOT_FOUND, "CUSTOM_TOOL_NOT_FOUND", "no such custom tool"
        )
    custom = store.get_custom_tool(principal.teamId, custom_tool_id)
    assert custom is not None
    return custom


def _call_error_status(code: str) -> int:
    if code.endswith("_NOT_FOUND"):
        return status.HTTP_404_NOT_FOUND
    if code in {
        "SCOPE_DENIED",
        "GRANT_EXPIRED",
        "SERVER_DISABLED",
        "PROJECT_CHECK_UNAVAILABLE",
        "TOOL_DENIED_BY_POLICY",
    }:
        return status.HTTP_403_FORBIDDEN
    if code in {"TOOL_REJECTED", "CUSTOM_TOOL_DISABLED"}:
        return status.HTTP_409_CONFLICT
    if code in {"ARG_PINNED", "ARG_NOT_ALLOWED"}:
        return status.HTTP_400_BAD_REQUEST
    if code == "MCP_TIMEOUT":
        return status.HTTP_504_GATEWAY_TIMEOUT
    if code in {"MCP_SERVER_UNREACHABLE", "MCP_PROTOCOL_ERROR"}:
        return status.HTTP_502_BAD_GATEWAY
    return status.HTTP_400_BAD_REQUEST


@router.post("/calls")
async def call_tool_route(
    body: RawCallRequest, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    _check_arguments_size(body.arguments)
    try:
        result = await execute_call(
            _store(request),
            _client(request),
            team_id=principal.teamId,
            server_id=body.serverId,
            tool_name=body.toolName,
            arguments=body.arguments,
            run_id=body.runId,
            project_id=body.projectId,
            project_owner_lookup=_lookup(request),
        )
    except CallDenied as denied:
        raise http_error(_call_error_status(denied.code), denied.code, denied.message) from denied
    return result.model_dump()


@router.post("/custom-tools/{custom_tool_id}/call")
async def call_custom_tool_route(
    custom_tool_id: str, body: CallRequest, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    _check_arguments_size(body.arguments)
    store = _store(request)
    custom = store.get_custom_tool(principal.teamId, custom_tool_id)
    if custom is None:
        raise http_error(status.HTTP_404_NOT_FOUND, "CUSTOM_TOOL_NOT_FOUND", "no such custom tool")
    try:
        result = await execute_custom_tool_call(
            store,
            _client(request),
            team_id=principal.teamId,
            custom_tool=custom,
            arguments=body.arguments,
            run_id=body.runId,
            project_id=body.projectId,
            project_owner_lookup=_lookup(request),
        )
    except CallDenied as denied:
        raise http_error(_call_error_status(denied.code), denied.code, denied.message) from denied
    return result.model_dump()


# =================================================================== skills


@router.post("/skills", status_code=status.HTTP_201_CREATED)
async def create_skill_route(
    body: SkillCreateRequest, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    try:
        skill, version = create_skill(
            _store(request),
            request.app.state.settings,
            team_id=principal.teamId,
            name=body.name,
            content=body.content,
            created_by=principal.userId,
        )
    except SkillError as exc:
        raise _skill_http(exc) from exc
    return {"skill": skill.model_dump(), "version": version.model_dump()}


def _skill_http(exc: SkillError) -> HTTPException:
    if exc.code.endswith("_NOT_FOUND"):
        return http_error(status.HTTP_404_NOT_FOUND, exc.code, exc.message)
    if exc.code.endswith("_CONFLICT") or exc.code in {"SKILL_REMOVED"}:
        return http_error(status.HTTP_409_CONFLICT, exc.code, exc.message)
    return http_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)


@router.get("/skills")
async def list_skills_route(request: Request, principal: RequirePrincipal) -> dict:
    return {"items": [s.model_dump() for s in _store(request).list_skills(principal.teamId)]}


@router.get("/skills/{skill_id}")
async def get_skill_route(
    skill_id: str, request: Request, principal: RequirePrincipal, version: int | None = None
) -> dict:
    try:
        skill, ver = get_skill_version(
            _store(request), team_id=principal.teamId, skill_id=skill_id, version=version
        )
    except SkillError as exc:
        raise _skill_http(exc) from exc
    return {"skill": skill.model_dump(), "version": ver.model_dump()}


@router.post("/skills/{skill_id}/versions", status_code=status.HTTP_201_CREATED)
async def add_skill_version_route(
    skill_id: str, body: SkillVersionRequest, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    try:
        version = add_skill_version(
            _store(request),
            request.app.state.settings,
            team_id=principal.teamId,
            skill_id=skill_id,
            content=body.content,
            created_by=principal.userId,
        )
    except SkillError as exc:
        raise _skill_http(exc) from exc
    return version.model_dump()


# ============================================================== marketplace


@router.get("/catalog")
async def list_catalog_route(request: Request, principal: RequirePrincipal) -> dict:
    catalog = _catalog(request)
    return {
        "items": [
            catalog.entries[pid].model_dump() for pid in sorted(catalog.entries)
        ]
    }


@router.get("/catalog/{plugin_id}")
async def get_catalog_entry(plugin_id: str, request: Request, principal: RequirePrincipal) -> dict:
    catalog = _catalog(request)
    entry = catalog.entries.get(plugin_id)
    if entry is None:
        raise http_error(status.HTTP_404_NOT_FOUND, "PLUGIN_NOT_FOUND", "no such plugin in catalog")
    install = _store(request).get_install(principal.teamId, plugin_id)
    return {
        "entry": entry.model_dump(),
        "serverTemplates": catalog.templates.get(plugin_id, []),
        "install": install.model_dump() if install else None,
    }


def _marketplace_http(exc: MarketplaceError) -> HTTPException:
    if exc.code.endswith("_NOT_FOUND"):
        return http_error(status.HTTP_404_NOT_FOUND, exc.code, exc.message)
    if exc.code in {"PLUGIN_REVOKED", "SKILL_SOURCE_CONFLICT"}:
        return http_error(status.HTTP_409_CONFLICT, exc.code, exc.message)
    return http_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)


@router.post("/catalog/{plugin_id}/install", status_code=status.HTTP_201_CREATED)
async def install_plugin_route(
    plugin_id: str, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    try:
        install = install_plugin(
            _store(request),
            request.app.state.settings,
            _catalog(request),
            team_id=principal.teamId,
            plugin_id=plugin_id,
            installed_by=principal.userId,
        )
    except MarketplaceError as exc:
        raise _marketplace_http(exc) from exc
    return install.model_dump()


@router.post("/plugins/{plugin_id}/uninstall")
async def uninstall_plugin_route(
    plugin_id: str, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    try:
        install = uninstall_plugin(_store(request), team_id=principal.teamId, plugin_id=plugin_id)
    except MarketplaceError as exc:
        raise _marketplace_http(exc) from exc
    return install.model_dump()


@router.post("/plugins/{plugin_id}/revoke")
async def revoke_plugin_route(
    plugin_id: str, request: Request, principal: RequirePrincipal
) -> dict:
    _require_admin(principal)
    install = revoke_plugin(
        _store(request), team_id=principal.teamId, plugin_id=plugin_id, revoked_by=principal.userId
    )
    return install.model_dump()


@router.get("/plugins")
async def list_plugin_installs(request: Request, principal: RequirePrincipal) -> dict:
    return {"items": [p.model_dump() for p in _store(request).list_installs(principal.teamId)]}


# ================================================================= commands


def _command_http(exc: CommandError) -> HTTPException:
    if exc.code == "COMMAND_NOT_FOUND":
        return http_error(status.HTTP_404_NOT_FOUND, exc.code, exc.message)
    if exc.code == "COMMAND_NAME_CONFLICT":
        return http_error(status.HTTP_409_CONFLICT, exc.code, exc.message)
    return http_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)


@router.post("/commands", status_code=status.HTTP_201_CREATED)
async def create_command_route(
    body: CommandRequest, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    try:
        command = create_command(
            _store(request),
            team_id=principal.teamId,
            name=body.name,
            template=body.template,
            arguments=body.arguments,
            description=body.description,
            created_by=principal.userId,
        )
    except CommandError as exc:
        raise _command_http(exc) from exc
    return command.model_dump()


@router.get("/commands")
async def list_commands_route(request: Request, principal: RequirePrincipal) -> dict:
    return {"items": [c.model_dump() for c in _store(request).list_commands(principal.teamId)]}


@router.post("/commands/{command_id}/invoke")
async def invoke_command_route(
    command_id: str, body: CommandInvokeRequest, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    try:
        command, rendered = invoke_command(
            _store(request),
            team_id=principal.teamId,
            command_id=command_id,
            arguments=body.arguments,
        )
    except CommandError as exc:
        raise _command_http(exc) from exc
    return {
        "commandId": command.commandId,
        "name": command.name,
        "renderedText": rendered,
        "renderedDigest": rendered_digest(rendered),
        "invokedBy": principal.userId,
        "invokedAt": _now_iso(),
    }


# ==================================================================== hooks


def _hook_http(exc: HookError) -> HTTPException:
    if exc.code in {"HOOK_NAME_CONFLICT", "HOOK_EVENT_INVALID"}:
        return http_error(status.HTTP_409_CONFLICT, exc.code, exc.message)
    if exc.code == "HOOK_NAME_INVALID":
        return http_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    return http_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)


@router.post("/hooks", status_code=status.HTTP_201_CREATED)
async def create_hook_route(
    body: HookRequest, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    try:
        hook = create_hook(
            _store(request),
            request.app.state.settings,
            team_id=principal.teamId,
            name=body.name,
            event_type=body.eventType,
            target=body.target,
            created_by=principal.userId,
        )
    except HookError as exc:
        raise _hook_http(exc) from exc
    return hook.model_dump()


@router.get("/hooks")
async def list_hooks_route(request: Request, principal: RequirePrincipal) -> dict:
    return {"items": [h.model_dump() for h in _store(request).list_hooks(principal.teamId)]}


@router.delete("/hooks/{hook_id}")
async def disable_hook_route(
    hook_id: str, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    store = _store(request)
    if not store.set_hook_status(principal.teamId, hook_id, "disabled"):
        raise http_error(status.HTTP_404_NOT_FOUND, "HOOK_NOT_FOUND", "no such hook")
    hook = store.get_hook(principal.teamId, hook_id)
    assert hook is not None
    return hook.model_dump()


@router.get("/hooks/{hook_id}/deliveries")
async def list_hook_deliveries_route(
    hook_id: str, request: Request, principal: RequirePrincipal, limit: int = 50
) -> dict:
    store = _store(request)
    if store.get_hook(principal.teamId, hook_id) is None:
        raise http_error(status.HTTP_404_NOT_FOUND, "HOOK_NOT_FOUND", "no such hook")
    limit = max(1, min(limit, 200))
    return {
        "items": [
            d.model_dump()
            for d in store.list_deliveries(principal.teamId, hook_id, limit=limit)
        ]
    }


@router.post("/hooks/dispatch")
async def dispatch_event_route(
    body: EventRequest, request: Request, principal: RequirePrincipal
) -> dict:
    """Internal ingestion point for events (wired to the real bus in S4).

    Callers can only dispatch events with their own team's hooks in scope;
    event type is validated against the known set.
    """
    _require_write(principal)
    from .hooks import validate_event_type

    try:
        validate_event_type(body.eventType)
    except HookError as exc:
        raise _hook_http(exc) from exc
    deliveries = await dispatch_event(
        _store(request),
        request.app.state.settings,
        _client(request),
        team_id=principal.teamId,
        event_type=body.eventType,
        payload=body.payload,
    )
    return {"deliveries": [d.model_dump() for d in deliveries]}


# =================================================================== policy


@router.post("/policy", status_code=status.HTTP_201_CREATED)
async def create_policy_rule_route(
    body: PolicyRuleRequest, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    import re as _re
    import uuid as _uuid

    pattern = body.pattern
    if not _re.fullmatch(r"[A-Za-z0-9*][A-Za-z0-9._*-]{0,127}", pattern) or "*" in pattern[:-1]:
        raise http_error(
            status.HTTP_400_BAD_REQUEST,
            "POLICY_PATTERN_INVALID",
            "pattern must be an exact tool name or a trailing-* wildcard",
        )
    store = _store(request)
    existing = [
        r
        for r in store.list_policy_rules(principal.teamId)
        if r.mode == body.mode and r.pattern == pattern
    ]
    if existing:
        raise http_error(
            status.HTTP_409_CONFLICT, "POLICY_RULE_CONFLICT", "rule already exists"
        )
    rule = ToolPolicyRule(
        policyId=str(_uuid.uuid4()),
        teamId=principal.teamId,
        mode=body.mode,
        pattern=pattern,
        createdBy=principal.userId,
        createdAt=_now_iso(),
    )
    store.add_policy_rule(rule)
    return rule.model_dump()


@router.get("/policy")
async def list_policy_rules_route(request: Request, principal: RequirePrincipal) -> dict:
    return {"items": [r.model_dump() for r in _store(request).list_policy_rules(principal.teamId)]}


@router.delete("/policy/{policy_id}")
async def delete_policy_rule_route(
    policy_id: str, request: Request, principal: RequirePrincipal
) -> dict:
    _require_write(principal)
    if not _store(request).delete_policy_rule(principal.teamId, policy_id):
        raise http_error(status.HTTP_404_NOT_FOUND, "POLICY_RULE_NOT_FOUND", "no such rule")
    return {"status": "deleted", "policyId": policy_id}


# ================================================================== the app


def _install_error_envelope(app: FastAPI) -> None:
    """Normalize non-2xx bodies to the frozen envelope (standalone posture).

    In the shared deployment assembly harness_identity's middleware does this
    globally; the local handler keeps the standalone service conformant.
    """
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_request: FastAPIRequest, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        code = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            413: "PAYLOAD_TOO_LARGE",
            422: "VALIDATION_ERROR",
            500: "INTERNAL_ERROR",
            502: "BAD_GATEWAY",
            503: "SERVICE_UNAVAILABLE",
            504: "GATEWAY_TIMEOUT",
        }.get(exc.status_code, f"HTTP_{exc.status_code}")
        message = detail if isinstance(detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}},
        )

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(
        _request: FastAPIRequest, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "request body failed validation",
                    "fields": exc.errors()[:10],
                }
            },
        )


def make_app(
    settings: Settings,
    store: Store | None = None,
    mcp_client: ExternalMcpClient | None = None,
    get_principal_dep: Callable[[Request], Any] | None = None,
    project_owner_lookup: Callable[[str], str | None] | None = None,
    catalog: Catalog | None = None,
) -> FastAPI:
    app = FastAPI(title="harness-extensions", version="1.0.0rc1")
    app.state.settings = settings
    app.state.extension_store = store or Store(settings.db_path)
    app.state.mcp_client = mcp_client or ExternalMcpClient(settings)
    app.state.catalog = catalog if catalog is not None else load_catalog(settings)
    app.state.project_owner_lookup = project_owner_lookup
    if get_principal_dep is not None:
        app.state.get_principal = get_principal_dep

    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    _install_error_envelope(app)
    return app
