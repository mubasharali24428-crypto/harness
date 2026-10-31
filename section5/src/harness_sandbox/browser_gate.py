"""Section 5 browser gate for the engine's ``browser`` tool (W10).

Rendering a page is a capability the runtime does not ship, and a raw Chromium
spawn would be an unconfined program with the worker's uid. This gate is the
decision point, run exactly like ``shell_gate.py`` and ``web_gate.py``:

    python -I browser_gate.py < {"action", "url", "path", "workspace", ...}

What it guarantees, each piece testable without a browser (``chromium`` is
injected through ``HARNESS_CHROMIUM`` so the tests use a stand-in):

* **The output stays in the workspace.** ``path`` is resolved with realpath and
  must land under ``workspace``; anything else is refused, symlinks included.
* **The browser gets no environment.** Chromium is spawned with ``PATH``,
  ``HOME`` and the egress proxy variables only — never the model key, never the
  runner token. The profile directory is a throwaway under the private tmp dir.
* **Remote pages go through the same allow-list as ``web_fetch``.** Only
  ``https://`` listed for the ``web`` egress purpose may be loaded, and only
  through the proxy; with no proxy configured a remote URL is refused instead of
  being attempted. ``file://`` pages are confined to the workspace.
* **Renders are bounded.** A wall-clock timeout, a hard byte cap on the PNG
  (default matches the 4 MiB goal-image cap), and a refusal for anything that is
  not a PNG the gate wrote itself.

One JSON line out: ``{"ok": true, "path", "bytes", "width", "height"}`` for a
screenshot, ``{"ok": true, "text", ...}`` for a dump, or ``{"error": reason}`` /
``{"denied": reason}``.

Stdlib only; ``egress.py`` is loaded by file path, so the worker image needs the
two files and ``egress_allowlist.json`` — not the ``harness_sandbox`` package.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_BYTES_DEFAULT = 4 * 1024 * 1024  # the goal-image cap; a render is an image
# A cold Chromium under the worker's limits (1 CPU share, read-only root, tmpfs
# /tmp) needs far longer than a warm one: measured 30 s+ on the first render of a
# run, where the same call on an idle container finishes in a few seconds.
MAX_SECONDS_DEFAULT = 60
MAX_SECONDS_CAP = 120
MAX_DOM_CHARS = 200_000
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_ACTIONS = ("screenshot", "dump")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


egress = _load("egress")


class Refused(Exception):
    """A request the gate will not render (path, scheme, destination, size)."""

    def __init__(self, reason: str):
        super().__init__(reason)


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the IHDR chunk, or None when this is not a PNG."""
    if len(data) < 24 or data[:8] != _PNG_MAGIC or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width == 0 or height == 0:
        return None
    return width, height


def resolve_target(workspace: str, path: str) -> Path:
    """The absolute PNG path, or Refused — never a path outside the workspace."""
    if not isinstance(path, str) or not path.strip():
        raise Refused("path must be a non-empty relative path ending in .png")
    ws = os.path.realpath(workspace)
    if not os.path.isdir(ws):
        raise Refused("workspace does not exist")
    target = os.path.realpath(os.path.join(ws, path))
    if os.path.commonpath([ws, target]) != ws:
        raise Refused(f"path {path!r} escapes the workspace")
    if target == ws:
        raise Refused("path must name a file, not the workspace root")
    if not target.lower().endswith(".png"):
        raise Refused("a screenshot path must end in .png")
    return Path(target)


def check_url(url: str, workspace: str, proxy: str | None) -> str | None:
    """Why ``url`` may not be loaded, or None when it may be.

    A bare path (``index.html``, ``docs/page.html``) is the workspace-relative
    form of a file URL: the agent should not have to know where the run's
    workspace is mounted to look at a page it can already read.
    """
    if not isinstance(url, str) or not url.strip():
        return "url must be a non-empty string"
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme == "":
        ws = os.path.realpath(workspace)
        target = os.path.realpath(os.path.join(ws, url))
        if os.path.commonpath([ws, target]) != ws:
            return f"page {url!r} is outside the workspace"
        if not os.path.isfile(target):
            return f"page {url!r} does not exist in the workspace"
        return None
    if scheme == "file":
        ws = os.path.realpath(workspace)
        target = os.path.realpath(unquote(parsed.path))
        if os.path.commonpath([ws, target]) != ws:
            return f"file url {url!r} is outside the workspace"
        if not os.path.isfile(target):
            return f"file url {url!r} does not name a file in the workspace"
        return None
    if scheme == "data":
        return None
    if scheme != "https":
        return f"only https:// and file:// are reachable (got {scheme or 'no scheme'!r})"
    if parsed.port not in (None, 443):
        return f"only port 443 is reachable (got {parsed.port})"
    decision = egress.egress_decision(url, egress.PURPOSE_WEB)
    if not decision.allowed:
        return decision.reason
    if not proxy:
        # The run network has no gateway: without the allow-list proxy a remote
        # render would hang until the timeout and look like a product bug.
        return "remote pages need the egress proxy (none configured for this run)"
    return None


def _browser_env(proxy: str | None) -> dict[str, str]:
    """Exactly what the browser needs: no model key, no tokens, no host secrets."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
    }
    if proxy:
        env.update(
            HTTPS_PROXY=proxy, https_proxy=proxy, HTTP_PROXY=proxy, http_proxy=proxy,
            NO_PROXY="", no_proxy="",
        )
    return env


def _chromium() -> str:
    override = os.environ.get("HARNESS_CHROMIUM", "").strip()
    if override:
        return override
    found = shutil.which("chromium") or shutil.which("chromium-browser")
    if not found:
        raise Refused("no chromium in this image (HARNESS_CHROMIUM unset)")
    return found


def _run_chromium(
    argv: list[str], timeout: float, proxy: str | None
) -> tuple[int, bytes, str, bool]:
    """(exit code, stdout, stderr tail, timed out). A timeout kills the whole renderer tree."""
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_browser_env(proxy),
        start_new_session=True,  # so a timeout kills the renderer tree, not just the CLI
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        # Helpers that closed their pipes can outlive the main process: reap the group too.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        return proc.returncode, out or b"", (err or b"").decode("utf-8", "replace")[-800:], False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        out, err = proc.communicate()
        return -1, out or b"", (err or b"").decode("utf-8", "replace")[-800:], True


def _base_args(binary: str, profile: str, proxy: str | None, width: int, height: int) -> list[str]:
    args = [
        binary,
        "--headless=new",
        # The container is the sandbox (cap-drop ALL, no-new-privileges,
        # read-only root, uid 10001): Chromium's own setuid sandbox cannot run
        # there, and adding the privileges it wants would undo the boundary.
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",  # /dev/shm is small in containers
        "--hide-scrollbars",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        f"--window-size={width},{height}",
    ]
    if proxy:
        args.append(f"--proxy-server={proxy}")
    else:
        # No proxy ⇒ no name resolution at all, so a remote subresource fails fast.
        args.append("--host-resolver-rules=MAP * ~NOTFOUND")
    return args


def render(request: dict) -> dict:
    """A policy refusal is a result, not an exception: callers always get one dict."""
    try:
        return _render(request)
    except Refused as exc:
        return {"denied": str(exc)}


def _render(request: dict) -> dict:
    action = str(request.get("action") or "screenshot")
    if action not in _ACTIONS:
        raise Refused(f"action must be one of {_ACTIONS}")
    url = str(request.get("url") or "")
    workspace = str(request.get("workspace") or "")
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip() or None
    timeout = float(request.get("timeoutSeconds") or MAX_SECONDS_DEFAULT)
    timeout = max(1.0, min(timeout, MAX_SECONDS_CAP))
    max_bytes = int(request.get("maxBytes") or MAX_BYTES_DEFAULT)
    width = max(320, min(int(request.get("width") or DEFAULT_WIDTH), 3840))
    height = max(240, min(int(request.get("height") or DEFAULT_HEIGHT), 2160))

    reason = check_url(url, workspace, proxy)
    if reason is not None:
        return {"denied": reason}
    if urlsplit(url).scheme == "":
        url = "file://" + os.path.realpath(os.path.join(workspace, url))
    target = (
        resolve_target(workspace, str(request.get("path") or ""))
        if action == "screenshot"
        else None
    )
    binary = _chromium()

    with tempfile.TemporaryDirectory(prefix="harness-browser-") as tmp:
        profile = os.path.join(tmp, "profile")
        args = _base_args(binary, profile, proxy, width, height)
        if action == "screenshot":
            assert target is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            args += [f"--screenshot={target}", "--virtual-time-budget=4000", url]
        else:
            args += ["--dump-dom", "--virtual-time-budget=4000", url]
        code, stdout, stderr, timed_out = _run_chromium(args, timeout, proxy)

        if action == "dump":
            if code != 0:
                reason = f"render timed out after {timeout:g}s" if timed_out else \
                    f"chromium exited {code}: {stderr}".strip()
                return {"error": reason}
            dom = stdout.decode("utf-8", "replace")
            truncated = len(dom) > MAX_DOM_CHARS
            return {
                "ok": True,
                "url": url,
                "truncated": truncated,
                "text": dom[:MAX_DOM_CHARS],
            }

        if not target.exists():
            reason = f"render timed out after {timeout:g}s without a screenshot" if timed_out else \
                f"chromium exited {code} without a screenshot: {stderr}".strip()
            return {"error": reason}
        size = target.stat().st_size
        if size > max_bytes:
            target.unlink(missing_ok=True)
            return {"error": f"screenshot is {size} bytes (cap {max_bytes}); render smaller "
                             "with width/height"}
        data = target.read_bytes()
        dims = png_size(data)
        if dims is None:
            target.unlink(missing_ok=True)
            return {"error": f"chromium wrote something that is not a PNG ({size} bytes)"}
        if timed_out:
            # The render finished writing a complete PNG and then the CLI lingered
            # past the deadline (cold start under the worker's limits). The image
            # exists and is valid: hand it over, and say it was slow, rather than
            # deleting evidence the agent can use.
            pass
        elif code != 0:
            return {"error": f"chromium exited {code} after writing the screenshot: "
                             f"{stderr}".strip()}
        ws = os.path.realpath(workspace)
        return {
            "ok": True,
            "path": os.path.relpath(target, ws),
            "bytes": size,
            "width": dims[0],
            "height": dims[1],
            "slowRender": timed_out,
            "hint": "open this path with the read tool to see the image",
        }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        result = render(request)
    except Refused as exc:
        result = {"denied": str(exc)}
    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        result = {"error": f"malformed gate request: {exc}"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
