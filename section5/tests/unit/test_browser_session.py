"""browser_session.py: the policy and wire parts of a persistent browser (W10).

The session needs Chromium for its actions, but not for the rules that decide
whether an action may run at all: destination policy, private-address refusal,
path containment, action caps, and the WebSocket/CDP framing it speaks to the
browser. Those are tested here without a browser; the live drill
(``scripts/e2e/w10_browser_session_drill.py``) exercises the rest.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

SESSION = Path(__file__).resolve().parents[2] / "src" / "harness_sandbox" / "browser_session.py"


def _session():
    spec = importlib.util.spec_from_file_location("browser_session", SESSION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["browser_session"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def sess():
    return _session()


# ------------------------------------------------------------------ policy


@pytest.mark.parametrize("host", [
    "127.0.0.1", "10.1.2.3", "172.16.5.4", "192.168.1.10", "169.254.169.254",
    "::1", "fe80::1", "0.0.0.0", "localhost", "app.localhost",
])
def test_private_and_loopback_addresses_are_refused(sess, host):
    assert sess._private_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "93.184.216.34", "api.deepseek.com"])
def test_public_hosts_are_not_private(sess, host):
    assert sess._private_host(host) is False


def test_navigation_policy_keeps_the_gate_and_adds_private_refusal(sess, tmp_path, monkeypatch):
    page = tmp_path / "p.html"
    page.write_text("<html></html>")
    gate = sess.gate

    # The gate decides scheme/allow-list/workspace; a stub keeps this test offline.
    monkeypatch.setattr(gate, "check_url", lambda url, ws, proxy: None)
    assert sess.check_navigation("file://" + str(page), str(tmp_path), None) is None
    refused = sess.check_navigation("https://127.0.0.1/", str(tmp_path), "http://proxy:3128")
    assert refused and "private or loopback" in refused

    # The real gate still refuses a file outside the workspace.
    monkeypatch.undo()
    outside = sess.check_navigation("file:///etc/passwd", str(tmp_path), None)
    assert outside and "outside the workspace" in outside


def test_bare_paths_resolve_inside_the_workspace(sess, tmp_path):
    page = tmp_path / "p.html"
    page.write_text("x")
    assert sess._resolve_url("p.html", str(tmp_path)) == "file://" + str(page)
    assert sess._resolve_url("https://example.com/", str(tmp_path)) == "https://example.com/"


def test_action_caps_refuse_before_touching_the_browser(sess, tmp_path):
    browser = sess.BrowserSession(str(tmp_path), chromium="/bin/true")
    with pytest.raises(sess.Refused):
        browser.dispatch({"action": "drop-tables"})
    with pytest.raises(sess.Refused):
        browser.type_text("x" * (sess.MAX_TYPE + 1))
    with pytest.raises(sess.Refused):
        browser.click(browser.width, 10)
    with pytest.raises(sess.Refused):
        browser.screenshot("../escape.png")


# ------------------------------------------------------------------ wire


def _accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1(
        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()


def _read_frame(conn: socket.socket) -> tuple[int, bytes, bool]:
    first, second = conn.recv(2)
    opcode = first & 0x0F
    masked, length = second >> 7, second & 0x7F
    if length == 126:
        length = struct.unpack(">H", conn.recv(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", conn.recv(8))[0]
    mask = conn.recv(4) if masked else b""
    payload = b""
    while len(payload) < length:
        chunk = conn.recv(length - len(payload))
        if not chunk:
            break
        payload += chunk
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload, bool(masked)


def _send_frame(conn: socket.socket, text: str, opcode: int = 1) -> None:
    payload = text.encode()
    header = bytes([0x80 | opcode])
    if len(payload) < 126:
        header += bytes([len(payload)])
    else:
        header += bytes([126]) + struct.pack(">H", len(payload))
    conn.sendall(header + payload)


class FakeServer:
    """One connection: handshake, then a scripted reply to the first frame."""

    def __init__(self, script):
        self.script = script
        self.received: list[tuple[int, bytes, bool]] = []
        self.ready = threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        conn, _ = self.sock.accept()
        request = b""
        while b"\r\n\r\n" not in request:
            request += conn.recv(4096)
        key = next(line.split(b":", 1)[1].strip().decode() for line in request.split(b"\r\n")
                   if line.lower().startswith(b"sec-websocket-key:"))
        accept = _accept(key)
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        self.ready.set()
        self.script(conn, self)
        conn.close()

    def start(self):
        self.thread.start()
        return self


def test_websocket_handshake_masking_and_pong(sess):
    def script(conn, server):
        # Ask the client to prove it answers pings. Its command was already sent,
        # so the frames arrive in the order: command, then pong.
        _send_frame(conn, "", opcode=9)
        server.received.append(_read_frame(conn))  # the command
        server.received.append(_read_frame(conn))  # the pong
        _send_frame(conn, json.dumps({"id": 1, "result": {"ok": True}}))

    server = FakeServer(script).start()
    ws = sess.WebSocket("127.0.0.1", server.port, "/devtools/browser/x")
    ws.send("hello")
    assert json.loads(ws.recv(5))["result"] == {"ok": True}
    ws.close()
    server.thread.join(timeout=2)
    opcode, payload, masked = server.received[0]
    assert opcode == 1 and payload == b"hello" and masked is True  # always masked
    opcode, payload, masked = server.received[1]
    assert opcode == 10 and payload == b""  # pong


def test_cdp_returns_by_id_and_keeps_events(sess):
    def script(conn, server):
        event = {"method": "Network.requestWillBeSent",
                 "params": {"request": {"url": "file:///ws/p.html"}}}
        _send_frame(conn, json.dumps(event))
        _send_frame(conn, json.dumps({"id": 1, "result": {"value": 7}}))

    server = FakeServer(script).start()
    ws = sess.WebSocket("127.0.0.1", server.port, "/devtools/browser/x")
    cdp = sess.CDP(ws)
    assert cdp.call("Runtime.evaluate", {"expression": "7"}) == {"value": 7}
    assert cdp.events and cdp.events[0]["method"] == "Network.requestWillBeSent"
    ws.close()
    server.thread.join(timeout=2)


def test_cdp_surfaces_protocol_errors(sess):
    def script(conn, server):
        _read_frame(conn)
        _send_frame(conn, json.dumps({"id": 1, "error": {"code": -32601,
                                                          "message": "no such method"}}))

    server = FakeServer(script).start()
    ws = sess.WebSocket("127.0.0.1", server.port, "/devtools/browser/x")
    cdp = sess.CDP(ws)
    with pytest.raises(sess.CDPError, match="no such method"):
        cdp.call("Nope.method")
    ws.close()
    server.thread.join(timeout=2)


def test_bounded_wait_and_step_cap(sess, tmp_path, monkeypatch):
    browser = sess.BrowserSession(str(tmp_path), chromium="/bin/true")
    started = time.time()
    result = browser.wait(10_000)  # capped, not ten seconds
    assert time.time() - started < 6
    assert result["requestedMs"] == 10_000
    assert "wait" in sess._ACTIONS

    class Stdin:
        @staticmethod
        def read() -> str:
            return json.dumps({"workspace": str(tmp_path),
                               "steps": [{"action": "wait"}] * (sess.MAX_STEPS + 1)})

    monkeypatch.setattr(sys, "stdin", Stdin())
    assert sess.main() == 2


def test_async_is_not_needed_for_actions(sess, tmp_path):
    """Guard against a refactor that turns actions async without awaiting them."""
    browser = sess.BrowserSession(str(tmp_path), chromium="/bin/true")
    assert not asyncio.iscoroutinefunction(browser.navigate)
