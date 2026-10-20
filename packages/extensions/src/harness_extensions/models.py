"""DTOs for the extensions plane. camelCase on the wire per api-conventions.md."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServerRecord(BaseModel):
    serverId: str
    teamId: str
    projectId: str | None = None
    name: str
    transport: str = "http"
    url: str
    status: str = "enabled"  # enabled | disabled | revoked
    createdBy: str
    createdAt: str
    health: str = "unknown"  # unknown | healthy | degraded | unhealthy
    healthCheckedAt: str | None = None
    healthLatencyMs: float | None = None
    healthError: str | None = None
    toolCount: int = 0
    rejectedToolCount: int = 0


class DiscoveredTool(BaseModel):
    name: str
    description: str | None = None
    inputSchema: dict
    callable: bool = True
    rejectionReason: str | None = None
    discoveredAt: str


class ToolGrant(BaseModel):
    grantId: str
    teamId: str
    serverId: str
    toolName: str  # exact tool name or "*"
    projectId: str | None = None
    runId: str | None = None
    expiresAt: str | None = None
    createdBy: str
    createdAt: str
    status: str = "active"  # active | revoked
    revokedAt: str | None = None
    revokedBy: str | None = None


class CatalogEntry(BaseModel):
    pluginId: str
    name: str
    version: str
    description: str
    digest: str
    skillCount: int
    permissionsNote: str | None = None


class PluginInstall(BaseModel):
    pluginId: str
    version: str
    digest: str
    teamId: str
    status: str = "installed"  # installed | revoked | removed
    installedBy: str
    installedAt: str
    updatedAt: str | None = None


class SkillSummary(BaseModel):
    skillId: str
    teamId: str
    name: str
    source: str  # "user" | "plugin:<pluginId>"
    status: str = "active"  # active | removed
    latestVersion: int
    createdBy: str
    createdAt: str
    updatedAt: str


class CustomTool(BaseModel):
    """A user-curated wrapper around one discovered external MCP tool.

    Wrapping narrows the callable surface: only ``allowedArguments`` (when
    set) may be supplied, ``defaultArguments`` are merged in first, and the
    underlying grant for the SOURCE tool is still required at call time.
    """

    customToolId: str
    teamId: str
    name: str
    description: str | None = None
    serverId: str
    sourceTool: str
    allowedArguments: list[str] | None = None  # None = pass-through (documented)
    defaultArguments: dict = Field(default_factory=dict)
    status: str = "active"  # active | disabled
    createdBy: str
    createdAt: str


class SkillVersion(BaseModel):
    skillId: str
    version: int
    content: str
    digest: str
    createdBy: str
    createdAt: str
    sourceRef: str | None = None


class CallResult(BaseModel):
    schemaVersion: str = "hap/1"
    observationId: str
    serverId: str
    toolName: str
    status: str  # succeeded | failed
    blocks: list[dict] = Field(default_factory=list)
    isError: bool = False
    truncated: bool = False
    durationMs: float


class Command(BaseModel):
    """A namespaced, team-scoped slash-style command: a parameterized template.

    ``name`` is the full namespaced key (``team/<slug>`` for user commands,
    ``plugin:<pluginId>/<slug>`` for plugin-shipped ones).
    """

    commandId: str
    teamId: str
    namespace: str  # "team" | "plugin:<pluginId>"
    name: str
    description: str | None = None
    template: str
    arguments: list[str] = Field(default_factory=list)
    source: str = "user"
    status: str = "active"  # active | removed
    createdBy: str
    createdAt: str
    updatedAt: str


class HookDefinition(BaseModel):
    """A team-scoped event subscription.

    ``target`` is one of:
      * ``{"kind": "webhook", "url": ...}`` — JSON POST of the event envelope;
      * ``{"kind": "mcp_tool", "serverId": ..., "toolName": ..., "arguments": {...}}``
        — invoke a granted tool with the event appended as ``_event``.
    """

    hookId: str
    teamId: str
    name: str
    eventType: str  # "tool_call.succeeded" | "tool_call.failed" | "*"
    target: dict
    status: str = "active"  # active | disabled
    createdBy: str
    createdAt: str


class HookDelivery(BaseModel):
    deliveryId: str
    hookId: str
    teamId: str
    eventType: str
    status: str  # delivered | failed | refused
    detail: str | None = None
    durationMs: float | None = None
    createdAt: str


class ToolPolicyRule(BaseModel):
    """One allow/deny rule. Patterns are exact tool names or ``prefix*``."""

    policyId: str
    teamId: str
    mode: str  # "deny" | "allow"
    pattern: str
    createdBy: str
    createdAt: str
