"""A bounded LSP stdio client (W4 spike).

The product does not speak LSP yet; W7 integrates real language servers. Before
that is worth doing, the W4 question is narrower: **if we spawn a language
server inside the worker, what stops it from outliving, out-writing or
out-reading its policy?**

This client is the answer's shape, not a full LSP implementation:

* **Spawned, never imported.** The server is a child process in its own session
  (``start_new_session``), so a timeout kills the whole process group.
* **A scrubbed environment.** Only ``PATH``/``LANG``/``LC_ALL`` pass through,
  plus a fresh ``HOME``/``TMPDIR`` outside the workspace. Anything whose name
  looks like a credential is refused rather than filtered.
* **Bounded reads.** Every message is capped by ``max_bytes`` *before* the body
  is read, and a request has a wall-clock deadline; exceeding either kills the
  child instead of growing the buffer.
* **Read-only by policy outside this class.** The client itself never writes;
  the drill mounts the workspace read-only, so a server that tries to write is
  refused by the mount, and the probe records the error.

    python3 -I lsp_client.py --cwd <dir> --command '["python3","-I","srv.py"]' \\
        --method textDocument/definition --params '{...}'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_TIMEOUT = 5.0
MAX_MESSAGE_BYTES = 4_000_000
_ENV_ALLOW = ("PATH", "LANG", "LC_ALL")
_ENV_EXTRA_ALLOW = frozenset({"PYTHONPATH", "PYTHONUNBUFFERED"})
_SECRET = re.compile(r"harness|deepseek|token|secret|password|api[-_]?key|credential", re.I)


class LspError(RuntimeError):
    """The server misbehaved, or the request cannot be honoured."""


class LspTimeout(LspError):
    """The server did not answer before the deadline; it has been killed."""


class LspOutputTooLarge(LspError):
    """A frame exceeded the byte cap; the server has been killed."""


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a language server gets: no credentials, its own HOME."""
    env = {key: os.environ[key] for key in _ENV_ALLOW if key in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    for key, value in (extra or {}).items():
        if str(key) not in _ENV_EXTRA_ALLOW:
            raise LspError(f"refusing to pass environment key {key!r} to a language server")
        if _SECRET.search(str(key)):
            raise LspError(f"refusing to pass credential-shaped key {key!r}")
        env[str(key)] = str(value)
    home = tempfile.mkdtemp(prefix="lsp-home-")
    env["HOME"] = home
    env["TMPDIR"] = home
    return env


class LspSession:
    """One stdio language-server process, spoken to with Content-Length frames."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | Path,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = MAX_MESSAGE_BYTES,
        env: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise LspError("command is required")
        cwd_p = Path(cwd).resolve()
        if not cwd_p.is_dir():
            raise LspError(f"cwd is not a directory: {cwd_p}")
        if timeout <= 0 or max_bytes <= 0:
            raise LspError("timeout and max_bytes must be positive")
        self.cwd = cwd_p
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        self._proc = subprocess.Popen(  # noqa: S603 - the command is caller-supplied and bounded
            [str(part) for part in command],
            cwd=str(cwd_p),
            env=scrubbed_env(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._buffer = bytearray()
        self._next_id = 1
        self._closed = False
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._proc.stdout, selectors.EVENT_READ)

    # ------------------------------------------------------------------ framing

    def _send(self, message: dict[str, Any]) -> None:
        body = json.dumps(message).encode("utf-8")
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(frame)
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise LspError("language server closed stdin") from exc

    def _parse_frame(self) -> dict[str, Any] | None:
        sep = self._buffer.find(b"\r\n\r\n")
        if sep < 0:
            return None
        header = self._buffer[:sep].decode("ascii", "replace")
        length: int | None = None
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1].strip())
                except ValueError as exc:
                    raise LspError(f"bad Content-Length header: {line!r}") from exc
        if length is None:
            raise LspError("frame has no Content-Length")
        if length > self.max_bytes:
            raise LspOutputTooLarge(
                f"frame of {length} bytes exceeds the {self.max_bytes}-byte cap")
        start = sep + 4
        if len(self._buffer) < start + length:
            return None
        body = bytes(self._buffer[start:start + length])
        del self._buffer[:start + length]
        try:
            parsed = json.loads(body)
        except ValueError as exc:
            raise LspError("frame body is not JSON") from exc
        if not isinstance(parsed, dict):
            raise LspError("frame body is not a JSON object")
        return parsed

    def _read_message(self, deadline: float) -> dict[str, Any]:
        while True:
            message = self._parse_frame()
            if message is not None:
                return message
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LspTimeout("language server did not answer before the deadline")
            if not self._selector.select(remaining):
                raise LspTimeout("language server did not answer before the deadline")
            assert self._proc.stdout is not None
            chunk = os.read(self._proc.stdout.fileno(), 65536)
            if not chunk:
                raise LspError("language server closed stdout")
            self._buffer.extend(chunk)
            if len(self._buffer) > self.max_bytes + 131072:
                raise LspOutputTooLarge(
                    f"unframed output exceeds the {self.max_bytes}-byte cap")

    # ------------------------------------------------------------------- protocol

    def request(self, method: str, params: dict[str, Any] | None = None,
                timeout: float | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method,
                    "params": params or {}})
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        try:
            while True:
                message = self._read_message(deadline)
                if message.get("id") != request_id:
                    continue  # a notification or a server request: not our answer
                if "error" in message:
                    raise LspError(f"{method}: {message['error']}")
                return message.get("result")
        except (LspTimeout, LspOutputTooLarge):
            self.kill()
            raise

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self, root: str | Path | None = None) -> Any:
        root_uri = "file://" + quote(str(Path(root or self.cwd).resolve()))
        result = self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {},
        })
        self.notify("initialized", {})
        return result

    # -------------------------------------------------------------------- process

    def kill(self) -> None:
        if self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                self._proc.kill()
            try:
                self._proc.wait(2.0)
            except subprocess.TimeoutExpired:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._proc.poll() is None:
                self.request("shutdown", {}, timeout=min(1.0, self.timeout))
                self.notify("exit")
                try:
                    self._proc.wait(1.0)
                except subprocess.TimeoutExpired:
                    self.kill()
        except (LspError, OSError, ValueError):
            self.kill()
        finally:
            self._selector.close()
            for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    @property
    def returncode(self) -> int | None:
        return self._proc.poll()

    def __enter__(self) -> LspSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="bounded stdio LSP client")
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--command", required=True, help="JSON array of argv strings")
    ap.add_argument("--method", required=True)
    ap.add_argument("--params", default="{}", help="JSON object")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--max-bytes", type=int, default=MAX_MESSAGE_BYTES)
    args = ap.parse_args(argv)
    try:
        command = json.loads(args.command)
        params = json.loads(args.params)
        if not isinstance(command, list) or not isinstance(params, dict):
            raise LspError("command must be a JSON array and params a JSON object")
        with LspSession(command, cwd=args.cwd, timeout=args.timeout,
                        max_bytes=args.max_bytes) as session:
            initialized = session.initialize()
            result = session.request(args.method, params)
            print(json.dumps({"initialize": initialized, "result": result}))
        return 0
    except (LspError, OSError, ValueError) as exc:
        print(json.dumps({"error": {"code": type(exc).__name__, "message": str(exc)}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
