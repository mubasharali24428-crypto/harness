import asyncio
import json

import httpx
import pytest

from harness_extensions.mcp_client import McpClient, McpError

OPEN = {"allow_loopback": True, "allow_private": True}


def rpc(req, result):
    return {"jsonrpc": "2.0", "id": req["id"], "result": result}


def server(handler):
    return httpx.MockTransport(handler)


def client(handler, **kw):
    return McpClient("http://127.0.0.1:9/mcp", transport=server(handler), **OPEN, **kw)


def happy(request):
    body = json.loads(request.content)
    if "id" not in body:
        return httpx.Response(202)
    m = body["method"]
    if m == "initialize":
        return httpx.Response(
            200, json=rpc(body, {"serverInfo": {"name": "t"}, "capabilities": {}})
        )
    if m == "tools/list":
        return httpx.Response(200, json=rpc(body, {"tools": [{"name": "echo"}]}))
    return httpx.Response(200, json=rpc(body, {"ok": body["params"]["arguments"]}))


async def test_discover_and_call():
    info, tools = await client(happy).discover()
    assert info["serverInfo"] == {"name": "t"} and tools == [{"name": "echo"}]
    assert await client(happy).call("echo", {"a": 1}) == {"ok": {"a": 1}}


async def test_redirects_refused():
    def h(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    with pytest.raises(McpError) as e:
        await client(h).discover()
    assert e.value.code == "REDIRECT_REFUSED"


async def test_oversize_response_cut_off():
    def h(request):
        return httpx.Response(200, content=b"x" * 5000)

    with pytest.raises(McpError) as e:
        await client(h, max_bytes=1000).discover()
    assert e.value.code == "RESPONSE_TOO_LARGE"


async def test_mismatched_id_and_malformed_json_rejected():
    def wrong_id(request):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 999, "result": {}})

    with pytest.raises(McpError) as e:
        await client(wrong_id).discover()
    assert e.value.code == "BAD_RESPONSE"
    with pytest.raises(McpError) as e:
        await client(lambda r: httpx.Response(200, content=b"not json")).discover()
    assert e.value.code == "BAD_RESPONSE"


async def test_server_error_message_is_truncated():
    def h(request):
        body = json.loads(request.content)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"message": "e" * 900}}
        )

    with pytest.raises(McpError) as e:
        await client(h).discover()
    assert e.value.code == "TOOL_ERROR" and len(e.value.message) == 300


async def test_overall_deadline_stops_slow_server():
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    with pytest.raises(McpError) as e:
        await client(slow, deadline=0.2).discover()
    assert e.value.code == "TIMEOUT"


async def test_ssrf_guard_runs_before_any_request():
    hit = []

    def h(request):
        hit.append(1)
        return happy(request)

    c = McpClient("https://localhost/mcp", transport=server(h))  # defaults deny loopback
    with pytest.raises(McpError) as e:
        await c.discover()
    assert e.value.code == "URL_REJECTED" and not hit


async def test_sse_response_parsed():
    def h(request):
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        res = {"serverInfo": {}} if body["method"] == "initialize" else {"tools": []}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"event: message\ndata: {json.dumps(rpc(body, res))}\n\n".encode(),
        )

    _, tools = await client(h).discover()
    assert tools == []
