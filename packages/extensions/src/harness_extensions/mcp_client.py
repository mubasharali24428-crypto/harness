"""External MCP client: discovery, health, tool calls over the real protocol.

This is the CLIENT half of the extensions plane — it speaks MCP outward to
servers teams register. The existing ``harness_mcp`` package stays the SERVER
half (it publishes Harness tools to desktop MCP clients); ``harness_mcp.client``
is a Harness-REST wrapper, not an MCP client, and is not reused here.

Everything external is hostile until validated:
  - discovery validates every tool inputSchema (schema_guard) and records
    rejections per tool instead of trusting the registry;
  - every connect path re-runs url_policy (deny-by-default SSRF posture);
  - every operation is bounded by an asyncio timeout;
  - tool-call observations are size-capped with an explicit truncation flag.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from .config import Settings
from .models import CallResult, DiscoveredTool
from .schema_guard import (
    ResultTruncation,
    SchemaLimits,
    SchemaRejectedError,
    bound_result_blocks,
    validate_tool_schema,
)
from .url_policy import validate_server_url

logger = logging.getLogger("harness.extensions")


class McpClientError(Exception):
    """Classified failure talking to an external MCP server."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _classify(exc: BaseException, url: str) -> McpClientError:
    if isinstance(exc, McpClientError):
        return exc
    if isinstance(exc, TimeoutError):
        return McpClientError("MCP_TIMEOUT", f"no response from {url} within the timeout")
    if isinstance(exc, OSError):
        return McpClientError(
            "MCP_SERVER_UNREACHABLE", f"{url} unreachable ({type(exc).__name__})"
        )
    return McpClientError(
        "MCP_PROTOCOL_ERROR", f"{url} failed the MCP handshake ({type(exc).__name__})"
    )


@dataclass
class DiscoveryResult:
    tools: list[DiscoveredTool]
    server_info: dict = field(default_factory=dict)
    protocol_version: str | None = None
    latency_ms: float = 0.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _bounded_description(tool: object, max_bytes: int) -> str:
    """Description string capped to the configured byte budget (UTF-8 safe)."""
    desc = getattr(tool, "description", None) or ""
    return desc.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")


class ExternalMcpClient:
    """Discovery/health/call against external MCP servers (streamable HTTP)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._limits = SchemaLimits(
            max_bytes=settings.max_schema_bytes,
            max_depth=settings.max_schema_depth,
            max_nodes=settings.max_schema_nodes,
        )

    @property
    def settings(self) -> Settings:
        return self._settings

    # ------------------------------------------------------------------
    # Discovery / health — one bounded session: initialize → list_tools
    # ------------------------------------------------------------------

    async def discover(self, url: str) -> DiscoveryResult:
        """Connect, initialize, list tools and validate every schema."""
        validate_server_url(url, allow_private_hosts=self._settings.allow_private_hosts)
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._settings.discover_timeout_s):
                async with streamable_http_client(url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        init = await session.initialize()
                        listed = await session.list_tools()
        except BaseException as exc:  # noqa: BLE001 — re-classified below
            raise _classify(exc, url) from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        tools: list[DiscoveredTool] = []
        now = _now_iso()
        for tool in listed.tools:
            bounded = _bounded_description(tool, self._settings.max_tool_description_bytes)
            try:
                if (
                    len((tool.description or "").encode("utf-8"))
                    > self._settings.max_tool_description_bytes
                ):
                    raise SchemaRejectedError(
                        "description exceeds "
                        f"{self._settings.max_tool_description_bytes} bytes"
                    )
                schema = validate_tool_schema(dict(tool.inputSchema or {}), self._limits)
                tools.append(
                    DiscoveredTool(
                        name=tool.name,
                        description=bounded,
                        inputSchema=schema,
                        callable=True,
                        discoveredAt=now,
                    )
                )
            except SchemaRejectedError as rejection:
                logger.warning("tool %r from %s rejected: %s", tool.name, url, rejection.reason)
                tools.append(
                    DiscoveredTool(
                        name=tool.name,
                        description=bounded,
                        inputSchema={"type": "object"},
                        callable=False,
                        rejectionReason=rejection.reason,
                        discoveredAt=now,
                    )
                )
        server_info = {}
        if init.serverInfo is not None:
            server_info = {
                "name": init.serverInfo.name,
                "version": init.serverInfo.version,
            }
        return DiscoveryResult(
            tools=tools,
            server_info=server_info,
            protocol_version=str(init.protocolVersion),
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Tool calls
    # ------------------------------------------------------------------

    async def call_tool(self, url: str, tool_name: str, arguments: dict) -> CallResult:
        validate_server_url(url, allow_private_hosts=self._settings.allow_private_hosts)
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._settings.call_timeout_s):
                async with streamable_http_client(url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments)
        except BaseException as exc:  # noqa: BLE001 — re-classified below
            raise _classify(exc, url) from exc
        duration_ms = (time.perf_counter() - started) * 1000.0

        blocks = [block.model_dump(exclude_none=True) for block in result.content]
        bounded: ResultTruncation = bound_result_blocks(blocks, self._settings.max_result_bytes)
        return CallResult(
            observationId=str(uuid.uuid4()),
            serverId="",
            toolName=tool_name,
            status="failed" if result.isError else "succeeded",
            blocks=bounded.blocks,
            isError=bool(result.isError),
            truncated=bounded.truncated,
            durationMs=round(duration_ms, 3),
        )

    # ------------------------------------------------------------------
    # stdio connector — for local workers only, never the registry service
    # ------------------------------------------------------------------

    async def discover_stdio(self, command: str, args: list[str]) -> DiscoveryResult:
        """Same contract as :meth:`discover` for locally spawned servers.

        The registry SERVICE never spawns processes (deny-by-default); this
        entry point exists for desktop/local workers that already run under
        Section 5 confinement.
        """
        params = StdioServerParameters(command=command, args=args)
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._settings.discover_timeout_s):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        listed = await session.list_tools()
        except BaseException as exc:  # noqa: BLE001 — re-classified below
            raise _classify(exc, f"stdio:{command}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0
        tools: list[DiscoveredTool] = []
        now = _now_iso()
        for tool in listed.tools:
            bounded = _bounded_description(tool, self._settings.max_tool_description_bytes)
            try:
                if (
                    len((tool.description or "").encode("utf-8"))
                    > self._settings.max_tool_description_bytes
                ):
                    raise SchemaRejectedError(
                        "description exceeds "
                        f"{self._settings.max_tool_description_bytes} bytes"
                    )
                schema = validate_tool_schema(dict(tool.inputSchema or {}), self._limits)
                tools.append(
                    DiscoveredTool(
                        name=tool.name,
                        description=bounded,
                        inputSchema=schema,
                        callable=True,
                        discoveredAt=now,
                    )
                )
            except SchemaRejectedError as rejection:
                tools.append(
                    DiscoveredTool(
                        name=tool.name,
                        description=bounded,
                        inputSchema={"type": "object"},
                        callable=False,
                        rejectionReason=rejection.reason,
                        discoveredAt=now,
                    )
                )
        return DiscoveryResult(tools=tools, latency_ms=latency_ms)
