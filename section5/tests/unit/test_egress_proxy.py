"""Egress proxy boundary: only allow-listed HTTPS CONNECT tunnels get out.

The proxy is the sole internet peer of the gateway-less sandbox network used by
opt-in networking. These tests pin the refusal paths locally (no external
traffic): wrong method, unlisted host, non-443 port, and upstream failure.
"""

import asyncio
import socket

from harness_sandbox import egress_proxy
from harness_sandbox.egress import is_allowed_destination


class _FakeWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, chunk: bytes) -> None:
        self.data.extend(chunk)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


async def _roundtrip(
    payload: bytes, body_len: int = 0, purpose: str = "sandbox"
) -> tuple[bytes, bytes]:
    server = await asyncio.start_server(
        lambda r, w: egress_proxy.handle(r, w, purpose), "127.0.0.1", 0
    )
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(payload)
        await writer.drain()
        status = await asyncio.wait_for(reader.readline(), 2)
        while (await asyncio.wait_for(reader.readline(), 2)) not in (b"\r\n", b"\n", b""):
            pass
        body = b""
        if body_len:
            body = await asyncio.wait_for(reader.read(body_len), 2)
        return status, body
    finally:
        writer.close()
        await asyncio.sleep(0)
        server.close()
        await server.wait_closed()


def test_non_connect_method_is_refused():
    status, _ = asyncio.run(
        _roundtrip(b"GET https://github.com/ HTTP/1.1\r\nHost: github.com\r\n\r\n")
    )
    assert b"405" in status


def test_unlisted_host_is_refused():
    status, _ = asyncio.run(_roundtrip(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n"))
    assert b"403" in status


def test_non_443_port_is_refused():
    status, _ = asyncio.run(_roundtrip(b"CONNECT github.com:80 HTTP/1.1\r\n\r\n"))
    assert b"403" in status


def test_allowed_host_is_tunnelled(monkeypatch):
    real_open_connection = asyncio.open_connection
    calls: list[tuple[str, int]] = []

    async def fake_open_connection(host, port):
        if (host, port) != ("github.com", 443):
            return await real_open_connection(host, port)
        calls.append((host, port))
        # StreamReader binds to the running loop, so it is created inside one.
        upstream_reader = asyncio.StreamReader()
        upstream_reader.feed_data(b"pong")
        upstream_reader.feed_eof()
        return upstream_reader, _FakeWriter()

    monkeypatch.setattr(egress_proxy.asyncio, "open_connection", fake_open_connection)
    status, body = asyncio.run(
        _roundtrip(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n", body_len=4)
    )
    assert b"200" in status
    assert body == b"pong"
    assert calls == [("github.com", 443)]


def test_upstream_failure_is_reported_as_bad_gateway(monkeypatch):
    real_open_connection = asyncio.open_connection

    async def fake_open_connection(host, port):
        if (host, port) == ("github.com", 443):
            raise OSError("upstream unreachable")
        return await real_open_connection(host, port)

    monkeypatch.setattr(egress_proxy.asyncio, "open_connection", fake_open_connection)
    status, _ = asyncio.run(_roundtrip(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n"))
    assert b"502" in status


def test_destination_matching_is_exact_not_suffixed():
    assert is_allowed_destination("github.com")
    assert is_allowed_destination("https://github.com/org/repo.git")
    assert not is_allowed_destination("github.com.evil.test")
    assert not is_allowed_destination("evil.test")


def test_the_sandbox_port_refuses_docs_but_the_web_port_allows_them(monkeypatch):
    # W4: purposes are separated by listener, not by a caller-supplied header.
    sandbox_status, _ = asyncio.run(
        _roundtrip(b"CONNECT api-docs.deepseek.com:443 HTTP/1.1\r\n\r\n")
    )
    assert b"403" in sandbox_status

    real_open_connection = asyncio.open_connection
    calls: list[tuple[str, int]] = []

    async def fake_open_connection(host, port):
        if (host, port) != ("api-docs.deepseek.com", 443):
            return await real_open_connection(host, port)
        calls.append((host, port))
        reader = asyncio.StreamReader()
        reader.feed_data(b"ok")
        reader.feed_eof()
        return reader, _FakeWriter()

    monkeypatch.setattr(egress_proxy.asyncio, "open_connection", fake_open_connection)
    web_status, body = asyncio.run(
        _roundtrip(b"CONNECT api-docs.deepseek.com:443 HTTP/1.1\r\n\r\n",
                   body_len=2, purpose="web")
    )
    assert b"200" in web_status and body == b"ok"
    assert calls == [("api-docs.deepseek.com", 443)]


def test_the_web_port_still_refuses_a_host_outside_its_list():
    status, _ = asyncio.run(
        _roundtrip(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n", purpose="web")
    )
    assert b"403" in status


def test_healthcheck_fails_when_a_purpose_listener_is_dead():
    # A closed port stands in for a dead 3129: health must not read "up" just
    # because the other listener answered.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    assert egress_proxy.healthcheck("127.0.0.1", (closed_port,)) == 1
