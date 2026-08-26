"""Shared fixtures: in-memory MCP client⇄server pair + stubbed harness API.

The MCP leg is REAL: the official SDK's lowlevel Server drives a real
initialize → tools/list → tools/call session over memory streams, exactly the
traffic `@modelcontextprotocol/inspector` produces. Only the UPSTREAM section
APIs are stubbed (route-table client subclass), so tool logic is exercised
end-to-end without the compose stack.

Loop discipline (repo pitfall, TestClient/portal family): the server task,
the client session AND every test-side call must execute on ONE event loop.
pytest-asyncio would finalize async fixtures on a different task than the
tests run on (anyio forbids that), and a second loop deadlocks the session.
So the `mcp` fixture hosts the whole pair on one dedicated loop thread and
hands tests ``run(fn)``: submit ``fn(session)`` and it executes ON that loop,
blocking for the result.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from harness_mcp.client import HarnessApiClient, UpstreamError
from harness_mcp.config import Settings
from harness_mcp.server import build_server
from harness_mcp.tools import build_tools


def make_settings(**over: Any) -> Settings:
    base = dict(
        service_token="m22-test-token",
        identity_base_url="http://identity.test",
        orchestrator_base_url="http://orchestrator.test",
        context_base_url="http://context.test",
    )
    base.update(over)
    return Settings(**base)


class StubAPI(HarnessApiClient):
    """HarnessApiClient whose HTTP layer is an in-memory route table.

    Routes are matched most-recently-registered-first, so tests override the
    conftest defaults by simply re-registering. A responder returns a
    JSON-able dict or raises UpstreamError.
    """

    def __init__(self) -> None:
        super().__init__(make_settings())
        self.routes: list[tuple[str, str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def on(self, method: str, url_substring: str, responder: Any) -> None:
        self.routes.append((method, url_substring, responder))

    async def _route(
        self, method: str, url: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, url, json_body))
        for m, needle, responder in reversed(self.routes):
            if m == method and needle in url:
                res = responder(json_body)
                if isinstance(res, UpstreamError):
                    raise res
                return res
        raise UpstreamError(404, f"stub has no route for {method} {url}")

    async def _request(
        self, method: str, url: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Route-table override of the real HTTP verb (same signature).
        return await self._route(method, url, json_body=json_body)


@pytest.fixture
def stub_api() -> StubAPI:
    api = StubAPI()
    # Default happy-path routes.
    api.on(
        "GET",
        "/api/v1/projects",
        lambda _: {"items": [{"id": "p-1", "name": "demo", "repoFullName": "acme/demo"}]},
    )
    api.on(
        "POST",
        "/api/v1/orchestrator/plans",
        lambda _: {
            "planId": "plan-777",
            "plan": {"goal": "g", "steps": [], "expectedResult": "done"},
        },
    )
    api.on("POST", "/plans/plan-777/approve", lambda _: {"runId": "run-abc123"})
    api.on("POST", "/runs/run-abc123/start", lambda _: {"runId": "run-abc123", "state": "executing"})
    api.on(
        "GET",
        "/runs/run-abc123/status",
        lambda _: {"runId": "run-abc123", "state": "executing", "planApproved": True},
    )
    api.on(
        "POST",
        "/api/v1/context/search",
        lambda body: {
            "projectId": (body or {}).get("projectId"),
            "query": (body or {}).get("query"),
            "results": [
                {
                    "path": "pkg/a.py",
                    "lang": "python",
                    "startLine": 1,
                    "endLine": 10,
                    "content": "def f(): ...",
                    "score": 0.91,
                }
            ],
        },
    )
    return api


@pytest.fixture
def mcp(stub_api):
    """Real MCP ClientSession ⇄ build_server pair on one dedicated loop.

    Yields ``run(fn)``: executes ``fn(session)`` ON the pair's loop (fn may
    return or await a session call) and blocks for the result. Coroutines are
    created where they're awaited — the cardinal rule that keeps anyio's
    cancel-scope/task-affinity law intact.
    """
    import asyncio

    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    holder: dict[str, Any] = {}
    ready = threading.Event()

    async def _serve_and_client() -> None:
        holder["loop"] = asyncio.get_running_loop()
        server = build_server(make_settings(), stub_api)
        stop = anyio.Event()
        holder["stop"] = stop

        async with create_client_server_memory_streams() as (client_streams, server_streams):

            async def run_srv() -> None:
                await server.run(
                    server_streams[0],
                    server_streams[1],
                    server.create_initialization_options(),
                    raise_exceptions=False,
                )

            async with anyio.create_task_group() as tg:
                tg.start_soon(run_srv)
                async with ClientSession(client_streams[0], client_streams[1]) as session:
                    await session.initialize()
                    holder["session"] = session
                    ready.set()
                    await stop.wait()
                tg.cancel_scope.cancel()

    thread = threading.Thread(target=lambda: asyncio.run(_serve_and_client()), daemon=True)
    thread.start()
    assert ready.wait(15), "MCP test pair failed to initialize within 15s"

    def run(fn, timeout: float = 30.0) -> Any:
        async def _runner() -> Any:
            out = fn(holder["session"])
            if asyncio.iscoroutine(out):
                out = await out
            return out

        fut = asyncio.run_coroutine_threadsafe(_runner(), holder["loop"])
        return fut.result(timeout)

    yield run

    # Clean teardown on the pair's own loop: set the anyio stop event.
    holder["loop"].call_soon_threadsafe(holder["stop"].set)
    thread.join(timeout=10)


@pytest.fixture
def registry(stub_api):
    """The tool registry itself (schema/doc assertions without a session)."""
    return build_tools(stub_api)


def parse_payload(result) -> dict[str, Any]:
    """Extract the JSON payload of the single TextContent block."""
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return json.loads(result.content[0].text)
