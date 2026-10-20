"""REAL MCP servers for tests — official SDK, streamable HTTP, ephemeral port.

Tests exercise the extensions plane against genuine MCP traffic (initialize →
tools/list → tools/call over HTTP), never against mocks of the protocol. The
fixture server can be built with deliberately hostile descriptions: recursion
bombs, megabyte descriptions, non-object roots, slow tools, crashing tools.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

Handler = Callable[[dict[str, Any]], Awaitable[list[types.ContentBlock]]]


def _text(payload: str) -> list[types.ContentBlock]:
    return [types.TextContent(type="text", text=payload)]


@dataclass
class FixtureTool:
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    handler: Handler | None = None


async def _echo(args: dict[str, Any]) -> list[types.ContentBlock]:
    return _text(f"echo:{args.get('text', '')}")


async def _slow(args: dict[str, Any]) -> list[types.ContentBlock]:
    await asyncio.sleep(float(args.get("seconds", 5)))
    return _text("slow-done")


async def _big(args: dict[str, Any]) -> list[types.ContentBlock]:
    size = int(args.get("bytes", 200_000))
    return _text("x" * size)


async def _crash(args: dict[str, Any]) -> list[types.ContentBlock]:
    raise RuntimeError("handler exploded")


def default_tools() -> list[FixtureTool]:
    return [
        FixtureTool(
            name="echo",
            description="Echo text back.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=_echo,
        ),
        FixtureTool(
            name="slow",
            description="Sleeps, then answers.",
            input_schema={
                "type": "object",
                "properties": {"seconds": {"type": "number"}},
            },
            handler=_slow,
        ),
        FixtureTool(
            name="big",
            description="Returns a huge text block.",
            input_schema={
                "type": "object",
                "properties": {"bytes": {"type": "integer"}},
            },
            handler=_big,
        ),
        FixtureTool(
            name="crash",
            description="Always raises.",
            input_schema={"type": "object", "properties": {}},
            handler=_crash,
        ),
    ]


def _deep_schema(depth: int) -> dict:
    node: dict[str, Any] = {"type": "object", "properties": {}}
    for i in range(depth):
        node = {"type": "object", "properties": {f"l{i}": node}}
    return node


def malicious_tools() -> list[FixtureTool]:
    """One good tool + four hostile discoveries."""
    hostile = [
        FixtureTool(
            name="deep_bomb",
            description="Recursion bomb schema.",
            input_schema=_deep_schema(60),
        ),
        FixtureTool(
            name="huge_desc",
            description="D" * (1024 * 1024),
            input_schema={"type": "object", "properties": {}},
        ),
        FixtureTool(
            name="non_object_root",
            description="Root is not an object.",
            input_schema={"type": "string"},
        ),
        FixtureTool(
            name="ref_bomb",
            description="Ref-heavy schema.",
            input_schema={
                "type": "object",
                "properties": {f"p{i}": {"$ref": "#/definitions/n"} for i in range(300)},
                "definitions": {"n": {"type": "object"}},
            },
        ),
    ]
    good = FixtureTool(
        name="echo",
        description="Echo text back.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=_echo,
    )
    return [good, *hostile]


def build_fixture_server(tools: list[FixtureTool]) -> Server:
    server = Server("fixture-mcp", version="0.0.1")
    registry = {t.name: t for t in tools}

    async def on_list_tools(_req: types.ListToolsRequest) -> types.ServerResult:
        return types.ServerResult(
            types.ListToolsResult(
                tools=[
                    types.Tool(
                        name=t.name, description=t.description, inputSchema=t.input_schema
                    )
                    for t in tools
                ]
            )
        )

    async def on_call_tool(req: types.CallToolRequest) -> types.ServerResult:
        tool = registry.get(req.params.name)
        if tool is None or tool.handler is None:
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text="no such tool")], isError=True
                )
            )
        args = dict(req.params.arguments or {})
        try:
            blocks = await tool.handler(args)
        except Exception as exc:  # noqa: BLE001 — fixture mirrors an honest server error
            return types.ServerResult(
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=f"boom: {exc}")], isError=True
                )
            )
        return types.ServerResult(types.CallToolResult(content=blocks))

    server.request_handlers[types.ListToolsRequest] = on_list_tools
    server.request_handlers[types.CallToolRequest] = on_call_tool
    return server


def _asgi_app(server: Server) -> Any:
    session_manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)

    async def handle_streamable_http(scope: Any, receive: Any, send: Any) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def _lifespan(_app: Any) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    from starlette.applications import Starlette
    from starlette.routing import Mount, Route

    class _RawAsgi:
        def __init__(self, app: Any) -> None:
            self._app = app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            await self._app(scope, receive, send)

    return Starlette(
        lifespan=_lifespan,
        routes=[
            Route("/mcp", _RawAsgi(handle_streamable_http), methods=["GET", "POST", "DELETE"]),
            Mount("/mcp", app=handle_streamable_http),
        ],
    )


@contextlib.asynccontextmanager
async def http_mcp_server(tools: list[FixtureTool]) -> AsyncIterator[str]:
    """Serve a real MCP server on an ephemeral port; yields the /mcp URL."""
    app = _asgi_app(build_fixture_server(tools))
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    uv = uvicorn.Server(config)
    task = asyncio.create_task(uv.serve())
    try:
        for _ in range(250):
            if uv.started:
                break
            await asyncio.sleep(0.02)
        else:
            raise RuntimeError("fixture MCP server did not start")
        port = uv.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        uv.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=10)
