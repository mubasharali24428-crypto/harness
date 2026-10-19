"""Single-loop raw ASGI websocket driver for tests (same pattern as test_ws_auth.py)."""

import asyncio
import json
from urllib.parse import urlsplit


class WsRejected(AssertionError):
    def __init__(self, code):
        super().__init__(f"websocket rejected with close code {code}")
        self.code = code


class MiniWs:
    def __init__(self, app, target: str, headers: dict | None = None, client_addr=None):
        parts = urlsplit(target)
        self.scope = {
            "type": "websocket",
            "path": parts.path,
            "query_string": parts.query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "subprotocols": [],
            "server": ("testserver", 80),
            "client": client_addr or ("testclient", 123),
        }
        self.app = app
        self._in: asyncio.Queue = asyncio.Queue()
        self._out: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.accepted = False
        self.close_code: int | None = None

    async def __aenter__(self) -> "MiniWs":
        self._task = asyncio.ensure_future(self.app(self.scope, self._in.get, self._out.put))
        await self._in.put({"type": "websocket.connect"})
        first = await asyncio.wait_for(self._out.get(), timeout=5)
        if first["type"] == "websocket.accept":
            self.accepted = True
        elif first["type"] == "websocket.close":
            self.close_code = first.get("code", 1000)
        return self

    async def recv_json(self, timeout: float = 5):
        while True:
            msg = await asyncio.wait_for(self._out.get(), timeout=timeout)
            if msg["type"] == "websocket.send":
                return json.loads(msg["text"])
            if msg["type"] == "websocket.close":
                self.close_code = msg.get("code", 1000)
                raise WsRejected(self.close_code)

    async def recv_all(self, quiet: float = 0.3) -> list[dict]:
        """Everything the server sends until it has been silent for `quiet` seconds."""
        out = []
        while True:
            try:
                out.append(await self.recv_json(timeout=quiet))
            except TimeoutError:
                return out

    async def __aexit__(self, *exc) -> None:
        if self.accepted and self.close_code is None:
            await self._in.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
