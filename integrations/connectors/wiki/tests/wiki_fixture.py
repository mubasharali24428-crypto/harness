"""A REAL local wiki API server for connector tests (never mocked HTTP)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


class WikiFixture:
    def __init__(self, pages: dict[str, dict]) -> None:
        self.pages = pages  # pageId -> {"title", "content", "updatedAt"}

    async def list_pages(self, request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "pages": [
                    {"id": pid, "title": p["title"], "updatedAt": p.get("updatedAt")}
                    for pid, p in sorted(self.pages.items())
                ]
            }
        )

    async def get_page(self, request: Request) -> JSONResponse:
        pid = request.path_params["page_id"]
        page = self.pages.get(pid)
        if page is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(
            {
                "id": pid,
                "title": page["title"],
                "content": page["content"],
                "updatedAt": page.get("updatedAt"),
            }
        )

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/pages", self.list_pages),
                Route("/pages/{page_id:path}", self.get_page),
            ]
        )


@contextlib.asynccontextmanager
async def wiki_server(pages: dict[str, dict]) -> AsyncIterator[str]:
    fixture = WikiFixture(pages)
    config = uvicorn.Config(fixture.app(), host="127.0.0.1", port=0, log_level="warning")
    uv = uvicorn.Server(config)
    task = asyncio.create_task(uv.serve())
    try:
        for _ in range(250):
            if uv.started:
                break
            await asyncio.sleep(0.02)
        else:
            raise RuntimeError("wiki server did not start")
        port = uv.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        uv.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=10)
