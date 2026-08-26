"""MCP protocol assembly: handlers, capabilities handshake, transports.

Uses the official ``mcp`` SDK's lowlevel Server with constructor-registered
``on_list_tools`` / ``on_call_tool`` handlers. tools/list publishes exactly
the registry from tools.py (registry ⇄ handshake can't drift); tools/call
maps every failure to an honest isError result — a tool failing must look
like a tool error, never like a JSON-RPC protocol fault.

Transports:
  - stdio (default for desktop clients): ``python -m harness_mcp``
  - streamable HTTP: ASGI app via :func:`http_asgi_app`, served by uvicorn
    on HARNESS_MCP_HTTP_PORT (default 8011), endpoint POST/GET /mcp.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .client import HarnessApiClient
from .config import Settings
from .tools import build_tools

logger = logging.getLogger("harness.mcp")

_CALL_ERROR_CODE = "TOOL_EXECUTION_ERROR"


def _error_blocks(code: str, message: str) -> tuple[list[dict[str, Any]], bool]:
    """Error payload as plain dict blocks + the isError flag kept OUT of band.

    The flag rides beside the blocks, never inside them: TextContent has no
    isError field, and a model_dump() round-trip silently drops extra keys.
    """
    text = json.dumps({"error": {"code": code, "message": message}}, separators=(",", ":"))
    return [{"type": "text", "text": text}], True


def _result(blocks: list[dict[str, Any]], *, is_error: bool = False) -> types.CallToolResult:
    """Materialize tool.py's plain-dict blocks into a CallToolResult."""
    content: list[types.ContentBlock] = [
        types.TextContent(type=b["type"], text=b["text"]) for b in blocks
    ]
    return types.CallToolResult(content=content, is_error=is_error)


def build_server(settings: Settings, api: HarnessApiClient | None = None) -> Server:
    """Build the configured lowlevel MCP Server (transports attach outside).

    ``api``: inject a pre-built client (tests pass a stubbed subclass);
    default constructs a real HTTP client from settings.
    """
    api = api if api is not None else HarnessApiClient(settings)
    tools = build_tools(api)

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t["name"],
                    description=t["description"],
                    input_schema=t["inputSchema"],
                )
                for t in tools
            ]
        )

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool = next((t for t in tools if t["name"] == params.name), None)
        if tool is None:
            blocks, err = _error_blocks("UNKNOWN_TOOL", f"no such harness tool: {params.name}")
            return _result(blocks, is_error=err)
        arguments = dict(params.arguments or {})
        missing = [k for k in tool["inputSchema"].get("required", []) if k not in arguments]
        if missing:
            blocks, err = _error_blocks(
                "INVALID_ARGUMENTS", f"missing required argument(s): {', '.join(missing)}"
            )
            return _result(blocks, is_error=err)
        try:
            blocks_dict = await tool["run"](arguments)
        except Exception as exc:  # noqa: BLE001 — last-resort honesty net
            logger.exception("tool '%s' crashed", params.name)
            blocks, err = _error_blocks(_CALL_ERROR_CODE, f"{type(exc).__name__}: {exc}")
            return _result(blocks, is_error=err)
        # Tool-layer failures (upstream 4xx/5xx) mark their blocks with isError.
        is_err = any(b.get("isError") for b in blocks_dict)
        return _result(
            [{k: v for k, v in b.items() if k != "isError"} for b in blocks_dict],
            is_error=is_err,
        )

    server = Server(
        settings.server_name,
        version=settings.server_version,
        title="AI Engineering Harness",
        description="Governed AI engineering runs, exposed as MCP tools.",
        instructions=(
            "Tools drive an AI engineering harness: start_run begins a governed "
            "run (plan minted + approval-gate walk), get_run_status polls it, "
            "search_context grounds goals in the project's indexed codebase, "
            "and list_projects lists accessible projects. Every action is "
            "attributed to this server's configured service principal; failures "
            "come back as isError results carrying the harness' own error codes."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    # Transport runners need the shared client for lifecycle management.
    server._harness_api = api  # type: ignore[attr-defined]
    return server


def http_asgi_app(server: Server) -> Any:
    """Starlette ASGI app serving the MCP endpoint at POST/GET /mcp."""
    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,  # plain JSON responses; friendliest to curl/inspector
        stateless=True,
    )

    async def handle_streamable_http(scope: Any, receive: Any, send: Any) -> None:
        await session_manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def _lifespan(_app: Any) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    from starlette.applications import Starlette
    from starlette.routing import Mount, Route

    class _RawAsgi:
        """Expose a (scope, receive, send) coroutine to Route as-is.

        starlette.routing.Route treats plain-function endpoints as
        ``func(request) -> response``; a callable class keeps the raw ASGI
        signature so the MCP session manager sees the untouched scope.
        """

        def __init__(self, app: Any) -> None:
            self._app = app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            await self._app(scope, receive, send)

    # Exact-path Route FIRST: a bare Mount("/mcp") only partially matches
    # "/mcp" (it wants a trailing slash) and Starlette answers POSTs with a
    # 307 to "/mcp/" whose empty body carries no content-type — which the
    # MCP client rejects ("Unexpected content type"). Clients POST exactly
    # /mcp, so serve it directly; the Mount keeps "/mcp/..." working.
    return Starlette(
        lifespan=_lifespan,
        routes=[
            Route(
                "/mcp",
                _RawAsgi(handle_streamable_http),
                methods=["GET", "POST", "DELETE"],
                name="mcp",
            ),
            Mount("/mcp", app=handle_streamable_http),
        ],
    )


def run_stdio(server: Server, api: HarnessApiClient) -> None:
    """Blocking stdio serve loop."""

    async def runner() -> None:
        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                    raise_exceptions=False,
                )
        finally:
            await api.aclose()

    anyio.run(runner)


def main() -> None:
    """Console entry point: stdio transport (desktop-client default)."""
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    server = build_server(settings)
    run_stdio(server, server._harness_api)


if __name__ == "__main__":
    main()
