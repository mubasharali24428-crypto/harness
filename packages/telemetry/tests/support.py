"""Shared test plumbing: fixed principals + a real fake OTLP collector."""

from __future__ import annotations

import contextlib
import socket
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


@dataclass
class Principal:
    userId: str
    teamId: str
    role: str


def principal_dep(mapping: dict[str, Principal]):
    """Bearer-token -> Principal resolver in the house app.state pattern."""

    def resolve(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return mapping.get(token)

    return resolve


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def fake_otlp_collector() -> AsyncIterator[tuple[str, list[bytes]]]:
    """A real HTTP collector capturing raw OTLP/HTTP export bodies."""
    received: list[bytes] = []

    async def traces(request: Request) -> JSONResponse:
        received.append(await request.body())
        return JSONResponse({"partialSuccess": {}})

    app = Starlette(routes=[Route("/v1/traces", traces, methods=["POST"])])
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        await _sleep()
    try:
        yield f"http://127.0.0.1:{port}/v1/traces", received
    finally:
        server.should_exit = True
        thread.join(timeout=10)


async def _sleep() -> None:
    import asyncio

    await asyncio.sleep(0.02)
