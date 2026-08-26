"""Streamable HTTP transport: a real MCP client drives the ASGI app over HTTP.

Mirrors what a remote Claude/Cursor-class client (or the inspector's HTTP tab)
does against `http://host:8011/mcp`. Upstream stays stubbed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # reuse conftest helpers

from conftest import make_settings  # noqa: E402

import httpx


async def test_http_transport_handshake_and_tools_list(stub_api):
    anyio = __import__("anyio")
    mcp = __import__("mcp")

    from mcp.client.streamable_http import streamable_http_client

    from harness_mcp.server import build_server, http_asgi_app

    server = build_server(make_settings())
    server._harness_api = stub_api  # type: ignore[attr-defined]
    asgi = http_asgi_app(server)

    async def run_pair():
        # httpx ASGI transport keeps this hermetic — no listening socket needed.
        transport = httpx.ASGITransport(app=asgi)  # type: ignore[arg-type]
        async with streamable_http_client(
            "http://testserver/mcp", http_client=httpx.AsyncClient(transport=transport)
        ) as streams:
            async with mcp.ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert [t.name for t in tools.tools] == [
                    "start_run",
                    "get_run_status",
                    "search_context",
                    "list_projects",
                ]
                res = await session.call_tool("list_projects", {})
                assert res.isError is not True
                payload = json.loads(res.content[0].text)
                assert payload["items"][0]["id"] == "p-1"
                return True

    assert anyio.run(run_pair)
