"""Streamable HTTP transport: a real MCP client drives the ASGI app over HTTP.

Mirrors what a remote Claude/Cursor-class client (or the inspector's HTTP tab)
does against `http://host:8011/mcp`. Upstream stays stubbed. Runs as a sync
test (one anyio.run) so the whole transport lives on a single event loop.
"""

from __future__ import annotations

import json

import httpx
from conftest import make_settings


def test_http_transport_handshake_tools_list_and_call(stub_api):
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from harness_mcp.server import build_server, http_asgi_app

    server = build_server(make_settings(), stub_api)
    asgi = http_asgi_app(server)

    async def run_pair():
        # Raw-ASGI driver instead of httpx.ASGITransport: the httpx one skips
        # the ASGI lifespan, so the SDK session manager's task group never
        # starts ("Task group is not initialized"), and its redirect handling
        # would also mask routing bugs. This mirrors what uvicorn does: run
        # the app's lifespan on a host task, serve requests in-line. Raw
        # scope paths hit the exact "/mcp" Route directly (no 307 dance).
        from conftest import AsgiLifespanDriver

        driver = AsgiLifespanDriver(asgi)
        await driver.start()
        try:
            transport = driver.transport()  # type: ignore[arg-type]
            async with streamable_http_client(
                "http://testserver/mcp", http_client=httpx.AsyncClient(transport=transport)
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert [t.name for t in tools.tools] == [
                        "start_run",
                        "get_run_status",
                        "search_context",
                        "list_projects",
                    ]
                    res = await session.call_tool("list_projects", {})
                    assert res.is_error is not True
                    payload = json.loads(res.content[0].text)
                    assert payload["items"][0]["id"] == "p-1"
        finally:
            await driver.stop()

    anyio.run(run_pair)
