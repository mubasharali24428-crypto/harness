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
    api.on(
        "POST",
        "/runs/run-abc123/start",
        lambda _: {"runId": "run-abc123", "state": "executing"},
    )
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


class AsgiLifespanDriver:
    """Drive an ASGI app over HTTP *with* real lifespan semantics.

    Why not ``httpx.ASGITransport``: it silently skips the ASGI "lifespan"
    scope, so apps whose startup hosts background task groups (the MCP SDK's
    StreamableHTTPSessionManager) die on their first request with "Task group
    is not initialized" — and you cannot fake lifespan by entering the
    manager's ``run()`` from a request task, because its task group must live
    longer than any one request's task. This driver mirrors uvicorn: a host
    task serves the lifespan scope for the whole session, and HTTP requests
    are forwarded in-line through a minimal ASGI transport (which honors
    exact-path routes directly instead of auto-following redirects like the
    stock transport, hiding routing bugs).
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._tg: Any = None
        self._startup_done: Any = None
        self._shutdown_requested: Any = None
        self._startup_error: str | None = None
        self._lifespan_startup_delivered = False

    async def start(self, timeout: float = 15.0) -> None:
        """Run ASGI startup on a host task; raise if the app fails it."""
        import anyio

        self._startup_done = anyio.Event()
        self._shutdown_requested = anyio.Event()
        tg = anyio.create_task_group()
        await tg.__aenter__()
        self._tg = tg
        try:
            with anyio.fail_after(timeout):
                await tg.start(self._run_host)  # host task lives in tg
                await self._startup_done.wait()  # until startup completes/fails
        except BaseException:
            await self._abandon()
            raise
        if self._startup_error is not None:
            await self._abandon()
            raise RuntimeError(f"ASGI lifespan startup failed: {self._startup_error}")

    async def stop(self) -> None:
        """Deliver ASGI shutdown and reap the host task."""
        tg, self._tg = self._tg, None
        if tg is None:
            return
        self._shutdown_requested.set()
        await tg.__aexit__(None, None, None)

    async def _abandon(self) -> None:
        """Best-effort teardown when startup already failed."""
        tg, self._tg = self._tg, None
        if tg is None:
            return
        self._shutdown_requested.set()
        try:
            await tg.__aexit__(None, None, None)
        except BaseException:  # noqa: BLE001 — teardown after failure is best-effort
            pass

    async def _run_host(self, *, task_status: Any = None) -> None:
        import anyio

        if task_status is None:
            task_status = anyio.TASK_STATUS_IGNORED
        task_status.started()
        await self._app(
            {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.3"}},
            self._lifespan_receive,
            self._lifespan_send,
        )

    def _lifespan_receive(self):  # type: ignore[no-untyped-def]
        # ASGI lifespan receive is a CALLABLE returning one message per call.
        # (An async-generator here would make the app re-read startup
        # forever.) Exactly two events: startup immediately, shutdown after
        # stop() sets the event.
        async def _next_message():  # type: ignore[no-untyped-def]
            if not self._lifespan_startup_delivered:
                self._lifespan_startup_delivered = True
                return {"type": "lifespan.startup"}
            await self._shutdown_requested.wait()
            return {"type": "lifespan.shutdown"}

        return _next_message()

    async def _lifespan_send(self, message: dict[str, Any]) -> None:
        if message["type"] == "lifespan.startup.complete":
            self._startup_done.set()
        elif message["type"] == "lifespan.startup.failed":
            self._startup_error = str(message.get("message") or "unknown error")
            self._startup_done.set()

    def transport(self) -> _RawAsgiHttpxTransport:
        return _RawAsgiHttpxTransport(self)


class _RawAsgiHttpxTransport:
    """Minimal httpx AsyncBaseTransport bridging straight into the ASGI app."""

    def __init__(self, driver: AsgiLifespanDriver) -> None:
        self._driver = driver

    async def handle_async_request(self, request: Any) -> Any:
        import urllib.parse

        import httpx

        url = request.url
        body = await request.aread()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": request.method,
            "scheme": url.scheme or "http",
            "path": urllib.parse.unquote(url.path),
            "raw_path": url.raw_path,
            "query_string": url.query,  # bytes, no leading '?'
            "root_path": "",
            "headers": [(k.lower(), v) for k, v in request.headers.raw],
            "client": ("asgi-lifespan-driver", 0),
            "server": ("testserver", 80),
        }
        state = {"sent_body": False}

        async def receive() -> dict[str, Any]:
            # Body was buffered up-front; afterwards only disconnect happens.
            if state["sent_body"]:
                return {"type": "http.disconnect"}
            state["sent_body"] = True
            return {"type": "http.request", "body": body, "more_body": False}

        collected: dict[str, Any] = {"status": 0, "headers": [], "chunks": []}

        async def send(message: dict[str, Any]) -> None:
            kind = message["type"]
            if kind == "http.response.start":
                collected["status"] = message["status"]
                collected["headers"] = list(message.get("headers", []))
            elif kind == "http.response.body":
                collected["chunks"].append(message.get("body", b""))

        await self._driver._app(scope, receive, send)
        return httpx.Response(
            status_code=collected["status"],
            headers=collected["headers"],
            content=b"".join(collected["chunks"]),
        )
