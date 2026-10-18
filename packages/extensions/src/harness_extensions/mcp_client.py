"""Minimal external MCP client over Streamable HTTP (JSON-RPC 2.0).

stdio transport is deliberately unsupported: spawning a subprocess for an MCP server would
bypass Section 5 enforcement. Responses are size-capped; redirects are never followed.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from .netguard import UrlRejected, check_resolution

PROTOCOL = "2025-03-26"
MAX_PAGES = 5


class McpError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class McpClient:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
        max_bytes: int = 256 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_loopback: bool = False,
        allow_private: bool = False,
        deadline: float = 30.0,
    ):
        self.url, self.timeout, self.max_bytes, self._transport = url, timeout, max_bytes, transport
        self._allow = {"allow_loopback": allow_loopback, "allow_private": allow_private}
        self.deadline = deadline
        self._headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }
        if token:
            self._headers["authorization"] = f"Bearer {token}"
        self._next = 0
        self._session: str | None = None

    async def _post(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        headers = dict(self._headers)
        if self._session:
            headers["mcp-session-id"] = self._session
        try:
            async with client.stream("POST", self.url, json=payload, headers=headers) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    raise McpError(
                        "REDIRECT_REFUSED", "server redirected; redirects are not followed"
                    )
                if r.status_code >= 400:
                    raise McpError("UPSTREAM_ERROR", f"server answered HTTP {r.status_code}")
                self._session = r.headers.get("mcp-session-id", self._session)
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > self.max_bytes:
                        raise McpError(
                            "RESPONSE_TOO_LARGE", "server response exceeded the size limit"
                        )
                ctype = r.headers.get("content-type", "")
        except httpx.TimeoutException as exc:
            raise McpError("TIMEOUT", "server timed out") from exc
        except httpx.HTTPError as exc:
            raise McpError("UNREACHABLE", f"server unreachable ({type(exc).__name__})") from exc
        if "id" not in payload:  # notification: no body expected
            return None
        text = bytes(buf).decode("utf-8", "replace")
        try:
            if "text/event-stream" in ctype:
                for line in text.splitlines():
                    if line.startswith("data:"):
                        msg = json.loads(line[5:].strip())
                        if isinstance(msg, dict) and msg.get("id") == payload["id"]:
                            return msg
                raise McpError("BAD_RESPONSE", "no JSON-RPC response in event stream")
            msg = json.loads(text)
        except ValueError as exc:
            raise McpError("BAD_RESPONSE", "server returned malformed JSON") from exc
        if not isinstance(msg, dict) or msg.get("id") != payload["id"]:
            raise McpError("BAD_RESPONSE", "response does not match the request id")
        return msg

    async def _rpc(
        self, client: httpx.AsyncClient, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        self._next += 1
        msg = await self._post(
            client,
            {
                "jsonrpc": "2.0",
                "id": self._next,
                "method": method,
                **({"params": params} if params else {}),
            },
        )
        assert msg is not None
        if "error" in msg:
            err = msg["error"] if isinstance(msg["error"], dict) else {}
            raise McpError("TOOL_ERROR", str(err.get("message", "server reported an error"))[:300])
        return msg.get("result")

    async def _session_run(self, fn):
        try:  # DNS-rebinding check on every session, not just at registration
            check_resolution(self.url, **self._allow)
        except UrlRejected as exc:
            raise McpError("URL_REJECTED", str(exc)) from exc
        try:
            # httpx timeouts are per-read; the deadline bounds a slow-drip server overall
            async with (
                asyncio.timeout(self.deadline),
                httpx.AsyncClient(
                    transport=self._transport, timeout=self.timeout, follow_redirects=False
                ) as client,
            ):
                init = await self._rpc(
                    client,
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {},
                        "clientInfo": {"name": "harness-extensions", "version": "0.1.0"},
                    },
                )
                await self._post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"})
                return await fn(client, init)
        except TimeoutError as exc:
            raise McpError("TIMEOUT", "server exceeded the overall deadline") from exc

    async def discover(self) -> tuple[dict[str, Any], list[Any]]:
        """Returns (serverInfo+capabilities, raw tool list). Tools are UNTRUSTED here."""

        async def go(client, init):
            tools: list[Any] = []
            cursor = None
            for _ in range(MAX_PAGES):
                res = await self._rpc(client, "tools/list", {"cursor": cursor} if cursor else None)
                if not isinstance(res, dict) or not isinstance(res.get("tools"), list):
                    raise McpError("BAD_RESPONSE", "tools/list result is malformed")
                tools.extend(res["tools"])
                cursor = res.get("nextCursor")
                if not cursor:
                    break
            info = init if isinstance(init, dict) else {}
            return {
                "serverInfo": info.get("serverInfo"),
                "capabilities": info.get("capabilities"),
            }, tools

        return await self._session_run(go)

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        async def go(client, _init):
            return await self._rpc(client, "tools/call", {"name": tool, "arguments": arguments})

        return await self._session_run(go)

    async def ping_latency(self) -> float:
        t0 = time.monotonic()
        await self._session_run(lambda client, init: self._rpc(client, "ping"))
        return time.monotonic() - t0
