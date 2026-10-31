"""Persistent browser session for the engine's browser tool (W10).

``browser_gate.py`` answers one question per process: "render this page to one
PNG, or dump its DOM". That is enough to *look*, not enough to *act*: clicking,
typing and scrolling need a page that stays loaded between calls, and a page
that stays loaded needs a lifecycle, a cleanup path and a policy for every
action, not just for the first URL.

This module is that lifecycle. It launches Chromium once, drives it over the
DevTools protocol (CDP) with a minimal stdlib WebSocket client — the worker has
no Node/Playwright runtime, and the image deliberately keeps Chromium itself as
the engine — and exposes one bounded, audited action per call:

    navigate  a file:// page under the workspace, or an https:// host the
              egress allow-list permits (and only through the proxy)
    inspect   the accessibility tree (role, name, backend node) and the text
    clickAx   find an AX node by role+name, take its box from the DOM and click
              its centre with real mouse events ("visual grounding")
    click     a bounded viewport coordinate
    type      focus a selector and insert bounded text
    scroll    a bounded wheel delta
    screenshot one PNG inside the workspace, header-checked and size-capped
    clipboard whether page script can read the clipboard (it must not)
    requests  every network request the page made since the session began

What is enforced HERE, per action:

* **The same URL policy as the gate.** ``file://`` only inside the workspace;
  ``https://`` only when the shared allow-list says yes *and* a proxy is
  configured. Literal private/loopback/link-local addresses are refused even
  when an allow-list mentions the host.
* **Redirects are re-checked, not assumed.** The final ``location.href`` after a
  navigation (and after a page-triggered redirect) must pass the same policy.
* **Downloads are denied** at the browser level (``Browser.setDownloadBehavior:
  deny``), not by hoping the page behaves.
* **Permissions are denied wholesale** (``--deny-permission-prompts``), so
  clipboard/geolocation/camera prompts cannot be answered by a page.
* **The browser gets no environment.** PATH, HOME, LANG and proxy variables
  only (``browser_gate._browser_env``) — never a model key or runner token.
* **Bounded.** Per-call and per-wait timeouts, a step cap in the CLI, capped
  text/AX size, capped typed text, capped scroll and screenshot sizes.
* **Cleaned up.** ``close()`` asks the browser to exit, kills the whole process
  group if it does not, and removes the throwaway profile directory.

Honest limit: this is a prototype adapter with a CLI (audit lines on stdout);
the engine's ``browser`` tool still calls the one-shot gate. Subresource
destinations are enforced by the egress proxy (https) and by Chromium's
``file://`` origin rules (local); the session records every request it saw so a
drill can assert what actually left the page.

    python3 -I browser_session.py < {"workspace": "/ws", "steps": [...]}

One JSON line per step on stdout: ``{"action", "ok", "ms", ...result}`` with
``{"denied": reason}`` for a policy refusal and ``{"error": reason}`` for a
protocol failure.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import ipaddress
import json
import os
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_STEPS = 50
MAX_TEXT = 200_000
MAX_AX_NODES = 500
MAX_TYPE = 2_000
MAX_SCROLL = 5_000
MAX_REQUESTS = 500
CALL_TIMEOUT = 20.0
READY_TIMEOUT = 15.0
MAX_SECONDS = 120.0
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ACTIONS = ("navigate", "inspect", "clickAx", "click", "box", "type", "scroll", "screenshot",
            "clipboard", "requests", "wait", "close")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load("browser_gate")


class Refused(Exception):
    """A request the session will not perform (destination, path, size, shape)."""


class CDPError(Exception):
    """The browser answered an error, or the connection died mid-call."""


# ------------------------------------------------------------------ policy


def _private_host(host: str) -> bool:
    name = host.strip("[]").lower()
    if name in ("localhost",) or name.endswith(".localhost"):
        return True
    try:
        addr = ipaddress.ip_address(name)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def check_navigation(url: str, workspace: str, proxy: str | None) -> str | None:
    """Why this URL may not be loaded, or None when it may be.

    Delegates the scheme/allow-list/workspace parts to the gate (one source of
    truth for both tools) and adds the literal-address refusal the gate leaves
    to the proxy.
    """
    reason = gate.check_url(url, workspace, proxy)
    if reason is not None:
        return reason
    parsed = urlsplit(url)
    if parsed.scheme.lower() in ("https", "http") and parsed.hostname:
        if _private_host(parsed.hostname):
            return f"{parsed.hostname!r} is a private or loopback address"
    return None


def _resolve_url(url: str, workspace: str) -> str:
    """The absolute URL for a bare workspace path (same rule as the gate)."""
    if urlsplit(url).scheme:
        return url
    return "file://" + os.path.realpath(os.path.join(workspace, url))


def _permission_state(value) -> str:
    return str(value or "unknown")


# ------------------------------------------------------------------ websocket


class WebSocket:
    """The smallest client that can speak CDP: text frames, ping/pong, close."""

    def __init__(self, host: str, port: int, path: str, timeout: float = 10.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buffer = b""
        self._handshake(host, port, path)

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        while b"\r\n\r\n" not in self._buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("browser closed the socket during the handshake")
            self._buffer += chunk
        head, _, rest = self._buffer.partition(b"\r\n\r\n")
        self._buffer = rest
        lines = head.decode("latin-1").split("\r\n")
        if not lines or "101" not in lines[0]:
            raise CDPError(f"websocket handshake refused: {lines[0] if lines else 'empty'}")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
                         .encode()).digest()).decode()
        accept = next((ln.split(":", 1)[1].strip() for ln in lines[1:]
                       if ln.lower().startswith("sec-websocket-accept:")), "")
        if accept and accept != expected:
            raise CDPError("websocket handshake: wrong Sec-WebSocket-Accept")

    def _read_exact(self, size: int, deadline: float) -> bytes:
        while len(self._buffer) < size:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for the browser")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(max(4096, size - len(self._buffer)))
            if not chunk:
                raise CDPError("browser connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def send(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])  # FIN + text
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = secrets.token_bytes(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        mask = secrets.token_bytes(4)
        header = bytearray([0x80 | opcode, 0x80 | len(payload)]) + mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4]
                                                for i, b in enumerate(payload)))

    def recv(self, timeout: float) -> str | None:
        """One text message, or None when the browser closed the connection."""
        deadline = time.time() + timeout
        fragments: list[bytes] = []
        while True:
            first, second = self._read_exact(2, deadline)
            fin, opcode = first >> 7, first & 0x0F
            masked, length = second >> 7, second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2, deadline))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8, deadline))[0]
            mask = self._read_exact(4, deadline) if masked else b""
            payload = self._read_exact(length, deadline)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 8:
                return None
            if opcode == 9:
                self._send_control(10, payload[:125])
                continue
            if opcode == 10:
                continue
            fragments.append(payload)
            if fin:
                return b"".join(fragments).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._send_control(8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class CDP:
    """Request/response over one browser WebSocket, with flat session ids."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self._id = 0
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None, *, session: str | None = None,
             timeout: float = CALL_TIMEOUT) -> dict:
        self._id += 1
        message: dict = {"id": self._id, "method": method}
        if params is not None:
            message["params"] = params
        if session:
            message["sessionId"] = session
        self.ws.send(json.dumps(message))
        deadline = time.time() + timeout
        while True:
            raw = self.ws.recv(max(0.1, deadline - time.time()))
            if raw is None:
                raise CDPError(f"browser closed during {method}")
            data = json.loads(raw)
            if data.get("id") == self._id:
                if "error" in data:
                    error = data["error"]
                    raise CDPError(f"{method}: {error.get('message')} ({error.get('code')})")
                return data.get("result", {})
            if "method" in data:
                self.events.append(data)
                if len(self.events) > 5_000:
                    del self.events[:1_000]


# ------------------------------------------------------------------ session


def _png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the IHDR chunk, or None when this is not a PNG."""
    return gate.png_size(data)


class BrowserSession:
    """One Chromium, one page, bounded actions, and a close() that cleans up."""

    def __init__(self, workspace: str, *, width: int = DEFAULT_WIDTH,
                 height: int = DEFAULT_HEIGHT, timeout: float = READY_TIMEOUT,
                 max_bytes: int = gate.MAX_BYTES_DEFAULT,
                 chromium: str | None = None) -> None:
        self.workspace = os.path.realpath(workspace)
        if not os.path.isdir(self.workspace):
            raise Refused("workspace does not exist")
        self.width = max(320, min(int(width), 3840))
        self.height = max(240, min(int(height), 2160))
        self.timeout = max(1.0, min(float(timeout), MAX_SECONDS))
        self.max_bytes = max(1, min(int(max_bytes), gate.MAX_BYTES_DEFAULT))
        self.proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                      or "").strip() or None
        try:
            self._chromium = chromium or os.environ.get("HARNESS_CHROMIUM") or gate._chromium()
        except gate.Refused as exc:  # the gate's own class: same refusal, ours to raise
            raise Refused(str(exc)) from exc
        self._tmp = ""
        self._proc: subprocess.Popen | None = None
        self._ws: WebSocket | None = None
        self._cdp: CDP | None = None
        self._target = ""
        self._session = ""
        self._requests: list[str] = []
        self._downloads: list[dict] = []
        self._actions: list[dict] = []
        self.env_keys: list[str] = []
        self.closed = False

    # -- lifecycle

    def __enter__(self) -> BrowserSession:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def start(self) -> dict:
        self._tmp = tempfile.mkdtemp(prefix="harness-session-")
        profile = os.path.join(self._tmp, "profile")
        os.makedirs(profile, exist_ok=True)
        argv = gate._base_args(self._chromium, profile, self.proxy,
                               self.width, self.height) + [
            "--remote-debugging-port=0",
            # Permissions are refused as a class; a page cannot answer its own prompt.
            "--deny-permission-prompts",
            "--password-store=basic",
            "--disable-save-password-bubble",
            "--disable-notifications",
            "--no-pings",
        ]
        env = gate._browser_env(self.proxy)
        self.env_keys = sorted(env)
        log = open(os.path.join(self._tmp, "chromium.log"), "wb")
        self._proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
            start_new_session=True,
        )
        port, path = self._devtools_endpoint(profile)
        self._ws = WebSocket("127.0.0.1", port, path)
        self._cdp = CDP(self._ws)
        self._cdp.call("Browser.setDownloadBehavior", {"behavior": "deny",
                                                       "eventsEnabled": True})
        self._cdp.call("Browser.resetPermissions", {})
        target = self._cdp.call("Target.createTarget", {"url": "about:blank"})
        self._target = target["targetId"]
        attached = self._cdp.call("Target.attachToTarget",
                                  {"targetId": self._target, "flatten": True})
        self._session = attached["sessionId"]
        for domain in ("Page.enable", "Runtime.enable", "Network.enable",
                       "Accessibility.enable", "DOM.enable"):
            self._cdp.call(domain, {}, session=self._session)
        # Pin the viewport to the size the caller asked for: `--window-size` sizes the
        # window, and a headless viewport can come out smaller (measured 640x313 for
        # 640x400), which would push a control's centre outside the clickable area.
        self._cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": self.width, "height": self.height,
            "deviceScaleFactor": 1, "mobile": False,
        }, session=self._session)
        return {"width": self.width, "height": self.height, "proxy": bool(self.proxy),
                "envKeys": self.env_keys, "target": self._target}

    def _devtools_endpoint(self, profile: str) -> tuple[int, str]:
        active = Path(profile) / "DevToolsActivePort"
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if active.is_file():
                lines = active.read_text().splitlines()
                if len(lines) >= 2 and lines[1].startswith("/devtools/"):
                    return int(lines[0]), lines[1].strip()
            if self._proc is not None and self._proc.poll() is not None:
                tail = ""
                log = Path(self._tmp) / "chromium.log"
                if log.is_file():
                    tail = log.read_text("utf-8", "replace")[-600:]
                raise CDPError(f"chromium exited {self._proc.returncode}: {tail}".strip())
            time.sleep(0.1)
        raise CDPError("chromium did not open a DevTools port")

    def close(self) -> dict:
        if self.closed:
            return {"closed": True, "profileRemoved": not Path(self._tmp).exists()}
        self.closed = True
        try:
            if self._cdp is not None:
                self._cdp.call("Browser.close", {}, timeout=5.0)
        except (TimeoutError, CDPError, OSError):
            pass
        if self._ws is not None:
            self._ws.close()
        if self._proc is not None:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            # Renderers and helpers share the group; they can outlive the main process, so the
            # group is signalled even when the leader already exited (an empty group is a no-op).
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except OSError:
                pass
            self._proc.wait(timeout=5)
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
        return {"closed": True, "profileRemoved": not Path(self._tmp).exists()}

    # -- helpers

    def _call(self, method: str, params: dict | None = None, *, timeout: float | None = None
              ) -> dict:
        if self._cdp is None:
            raise CDPError("session is not started")
        return self._cdp.call(method, params, session=self._session,
                              timeout=CALL_TIMEOUT if timeout is None else timeout)

    def _audit(self, action: str, target: str, started: float, **extra) -> dict:
        entry = {"action": action, "target": target[:200],
                 "ms": round((time.time() - started) * 1000, 1), **extra}
        self._actions.append({"action": action, "ms": entry["ms"]})
        return entry

    def evaluate(self, expression: str) -> object:
        result = self._call("Runtime.evaluate", {"expression": expression,
                                                 "returnByValue": True,
                                                 "awaitPromise": True})
        details = result.get("exceptionDetails")
        if details:
            raise CDPError(f"page script failed: {details.get('text')}")
        return (result.get("result") or {}).get("value")

    def _drain_requests(self) -> None:
        if self._cdp is None:
            return
        for event in self._cdp.events:
            method = event.get("method")
            if method == "Network.requestWillBeSent":
                url = ((event.get("params") or {}).get("request") or {}).get("url")
                if url and url not in self._requests:
                    self._requests.append(url)
            elif method in ("Browser.downloadWillBegin", "Browser.downloadProgress"):
                params = event.get("params") or {}
                entry = {"event": method,
                         "file": params.get("suggestedFilename"),
                         "state": params.get("state")}
                if entry not in self._downloads:
                    self._downloads.append(entry)
        if len(self._requests) > MAX_REQUESTS:
            del self._requests[:-MAX_REQUESTS]

    def _check(self, url: str) -> None:
        reason = check_navigation(url, self.workspace, self.proxy)
        if reason is not None:
            raise Refused(reason)

    def _wait_ready(self) -> None:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            state = self.evaluate("document.readyState")
            if state in ("interactive", "complete"):
                return
            time.sleep(0.05)
        raise CDPError("page did not finish loading")

    # -- actions

    def navigate(self, url: str) -> dict:
        started = time.time()
        url = _resolve_url(str(url), self.workspace)
        self._check(url)
        result = self._call("Page.navigate", {"url": url})
        if result.get("errorText"):
            raise CDPError(f"navigation failed: {result['errorText']}")
        self._wait_ready()
        final = str(self.evaluate("location.href") or "")
        reason = check_navigation(unquote(final), self.workspace, self.proxy)
        if reason is not None:
            # A page-initiated redirect is checked where it lands. Quarantine the page
            # before refusing: the refused document must not stay readable to the next
            # action (the request log still records that it was fetched).
            self._drain_requests()
            fetched = list(self._requests)
            self._call("Page.navigate", {"url": "about:blank"})
            self._wait_ready()
            raise Refused(f"redirect reached {final!r}: {reason} "
                          f"(quarantined; fetched: {fetched[-3:]})")
        self._drain_requests()
        return self._audit("navigate", final, started, url=final,
                           title=self.evaluate("document.title"))

    def inspect(self) -> dict:
        started = time.time()
        tree = self._call("Accessibility.getFullAXTree", {}).get("nodes", [])
        nodes = []
        for node in tree:
            if node.get("ignored"):
                continue
            role = (node.get("role") or {}).get("value")
            name = (node.get("name") or {}).get("value")
            if not role:
                continue
            nodes.append({"role": role, "name": (name or "")[:200],
                          "backendNodeId": node.get("backendDOMNodeId")})
            if len(nodes) >= MAX_AX_NODES:
                break
        text = str(self.evaluate("document.body ? document.body.innerText : ''") or "")[:MAX_TEXT]
        self._drain_requests()
        return self._audit("inspect", "ax", started, url=str(self.evaluate("location.href")),
                           ax=nodes, text=text)

    def _ax_node(self, role: str, name: str) -> dict:
        tree = self._call("Accessibility.getFullAXTree", {}).get("nodes", [])
        for node in tree:
            if node.get("ignored"):
                continue
            got_role = (node.get("role") or {}).get("value")
            got_name = (node.get("name") or {}).get("value") or ""
            if got_role == role and got_name == name:
                return node
        raise CDPError(f"no AX node with role={role!r} name={name!r}")

    def _box(self, backend_node_id: int) -> tuple[int, int, int, int]:
        """(centre x, centre y, width, height) of an element's content box."""
        model = self._call("DOM.getBoxModel", {"backendNodeId": backend_node_id})
        quad = (model.get("model") or {}).get("content")
        if not quad or len(quad) < 8:
            raise CDPError("element has no box")
        xs, ys = quad[0::2], quad[1::2]
        x = int(sum(xs) / len(xs))
        y = int(sum(ys) / len(ys))
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise Refused(f"control centre ({x},{y}) is outside the viewport")
        return x, y, max(xs) - min(xs), max(ys) - min(ys)

    def _box_centre(self, backend_node_id: int) -> tuple[int, int]:
        x, y, _w, _h = self._box(backend_node_id)
        return x, y

    def _mouse_click(self, x: int, y: int) -> None:
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            params = {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1}
            self._call("Input.dispatchMouseEvent", params)

    def click_ax(self, role: str, name: str) -> dict:
        started = time.time()
        node = self._ax_node(role, name)
        backend = node.get("backendDOMNodeId")
        if not backend:
            raise CDPError("AX node has no backendDOMNodeId")
        x, y = self._box_centre(int(backend))
        self._mouse_click(x, y)
        pressed = self.evaluate(
            "(() => { const n = document.querySelector('[aria-pressed]');"
            " return n ? n.getAttribute('aria-pressed') : null; })()")
        return self._audit("clickAx", f"{role}:{name}", started, x=x, y=y,
                           backendNodeId=int(backend), ariaPressed=pressed,
                           stillThere=self._ax_exists(role, name))

    def _ax_exists(self, role: str, name: str) -> bool:
        try:
            self._ax_node(role, name)
            return True
        except CDPError:
            return False

    def box(self, role: str, name: str) -> dict:
        """The box of an AX node — what accessibility data offers a grounding step."""
        started = time.time()
        node = self._ax_node(role, name)
        backend = node.get("backendDOMNodeId")
        if not backend:
            raise CDPError("AX node has no backendDOMNodeId")
        x, y, width, height = self._box(int(backend))
        return self._audit("box", f"{role}:{name}", started, x=x, y=y, width=width,
                           height=height, backendNodeId=int(backend))

    def click(self, x: int, y: int) -> dict:
        started = time.time()
        if not (0 <= int(x) < self.width and 0 <= int(y) < self.height):
            raise Refused(f"({x},{y}) is outside the {self.width}x{self.height} viewport")
        self._mouse_click(int(x), int(y))
        return self._audit("click", f"{int(x)},{int(y)}", started, x=int(x), y=int(y))

    def type_text(self, text: str, selector: str | None = None) -> dict:
        started = time.time()
        if len(text) > MAX_TYPE:
            raise Refused(f"text is longer than {MAX_TYPE} characters")
        if selector:
            focused = self.evaluate(
                "(() => { const el = document.querySelector(" + json.dumps(selector) + ");"
                " if (!el) return null; el.focus(); return el.tagName; })()")
            if not focused:
                raise CDPError(f"no element matches {selector!r}")
        self._call("Input.insertText", {"text": text})
        value = self.evaluate(
            "(() => { const el = document.activeElement;"
            " return el && 'value' in el ? el.value : null; })()")
        return self._audit("type", selector or "focused", started, typed=len(text),
                           value=str(value or "")[:200])

    def scroll(self, dy: int) -> dict:
        started = time.time()
        dy = max(-MAX_SCROLL, min(int(dy), MAX_SCROLL))
        self._call("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": self.width // 2, "y": self.height // 2,
            "deltaX": 0, "deltaY": dy, "button": "none",
        })
        time.sleep(0.1)
        position = self.evaluate("window.scrollY")
        return self._audit("scroll", str(dy), started, deltaY=dy, scrollY=position)

    def screenshot(self, path: str) -> dict:
        started = time.time()
        try:
            target = gate.resolve_target(self.workspace, str(path))
        except gate.Refused as exc:
            raise Refused(str(exc)) from exc
        result = self._call("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(result.get("data") or "")
        if not data:
            raise CDPError("the browser returned an empty screenshot")
        if len(data) > self.max_bytes:
            raise Refused(f"screenshot is {len(data)} bytes (cap {self.max_bytes})")
        dims = _png_size(data)
        if dims is None:
            raise CDPError("the browser wrote something that is not a PNG")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return self._audit("screenshot", str(target.relative_to(self.workspace)), started,
                           path=str(target.relative_to(self.workspace)), bytes=len(data),
                           width=dims[0], height=dims[1])

    def clipboard(self) -> dict:
        started = time.time()
        result = self.evaluate(
            "navigator.clipboard && navigator.clipboard.readText"
            " ? navigator.clipboard.readText().then(t => 'READ:' + t)"
            "   .catch(e => 'DENIED:' + e.name)"
            " : 'UNAVAILABLE'")
        state = self.evaluate(
            "navigator.permissions.query({name: 'clipboard-read'})"
            " .then(r => r.state).catch(e => 'ERROR:' + e.name)")
        return self._audit("clipboard", "readText", started, outcome=str(result),
                           permission=_permission_state(state))

    def wait(self, ms: int) -> dict:
        started = time.time()
        time.sleep(max(0, min(int(ms), 5_000)) / 1000)
        return self._audit("wait", str(ms), started, requestedMs=ms)

    def requests(self) -> dict:
        started = time.time()
        self._drain_requests()
        return self._audit("requests", "network", started, urls=list(self._requests),
                           downloads=list(self._downloads))

    # -- CLI dispatch

    def dispatch(self, step: dict) -> dict:
        action = str(step.get("action") or "")
        if action not in _ACTIONS:
            raise Refused(f"action must be one of {_ACTIONS}")
        if action == "navigate":
            return self.navigate(step.get("url") or "")
        if action == "inspect":
            return self.inspect()
        if action == "clickAx":
            return self.click_ax(str(step.get("role") or ""), str(step.get("name") or ""))
        if action == "click":
            return self.click(int(step.get("x") or 0), int(step.get("y") or 0))
        if action == "box":
            return self.box(str(step.get("role") or ""), str(step.get("name") or ""))
        if action == "type":
            return self.type_text(str(step.get("text") or ""), step.get("selector"))
        if action == "scroll":
            return self.scroll(int(step.get("dy") or 0))
        if action == "screenshot":
            return self.screenshot(str(step.get("path") or ""))
        if action == "clipboard":
            return self.clipboard()
        if action == "requests":
            return self.requests()
        if action == "wait":
            return self.wait(int(step.get("ms") or 0))
        return self.close()  # action == "close"


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"malformed request: {exc}"}))
        return 2
    steps = request.get("steps") or []
    if len(steps) > MAX_STEPS:
        print(json.dumps({"error": f"at most {MAX_STEPS} steps per session"}))
        return 2
    session = None
    try:
        session = BrowserSession(
            str(request.get("workspace") or ""),
            width=int(request.get("width") or DEFAULT_WIDTH),
            height=int(request.get("height") or DEFAULT_HEIGHT),
            timeout=float(request.get("timeoutSeconds") or READY_TIMEOUT),
            max_bytes=int(request.get("maxBytes") or gate.MAX_BYTES_DEFAULT),
        )
        print(json.dumps({"action": "start", "ok": True, **session.start()}))
        for step in steps:
            started = time.time()
            action = str(step.get("action") or "?")
            try:
                result = session.dispatch(step)
                print(json.dumps({"action": action, "ok": True, **result}, default=str))
            except (Refused, gate.Refused) as exc:
                print(json.dumps({"action": action, "ok": False, "denied": str(exc),
                                  "ms": round((time.time() - started) * 1000, 1)}))
            except (TimeoutError, CDPError, OSError) as exc:
                print(json.dumps({"action": action, "ok": False, "error": str(exc),
                                  "ms": round((time.time() - started) * 1000, 1)}))
    except (Refused, gate.Refused) as exc:
        print(json.dumps({"action": "start", "ok": False, "denied": str(exc)}))
        return 2
    except (CDPError, OSError) as exc:
        print(json.dumps({"action": "start", "ok": False, "error": str(exc)}))
        return 2
    finally:
        if session is not None and not session.closed:
            print(json.dumps({"action": "cleanup", "ok": True, **session.close()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
