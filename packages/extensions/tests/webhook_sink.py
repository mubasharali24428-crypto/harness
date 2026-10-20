"""A REAL local webhook receiver for hook-delivery tests.

Runs a Starlette/uvicorn app on an ephemeral port and records every POST body
it receives (in memory), so tests assert on actual wire deliveries — never on
mocked HTTP calls.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


class WebhookSink:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.status = 200

    async def handler(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.requests.append(body)
        return JSONResponse({"ok": True}, status_code=self.status)

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/hook", self.handler, methods=["POST"])])


@contextlib.asynccontextmanager
async def webhook_sink() -> AsyncIterator[tuple[str, WebhookSink]]:
    sink = WebhookSink()
    config = uvicorn.Config(sink.app(), host="127.0.0.1", port=0, log_level="warning")
    uv = uvicorn.Server(config)
    task = asyncio.create_task(uv.serve())
    try:
        for _ in range(250):
            if uv.started:
                break
            await asyncio.sleep(0.02)
        else:
            raise RuntimeError("webhook sink did not start")
        port = uv.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}/hook", sink
    finally:
        uv.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=10)
