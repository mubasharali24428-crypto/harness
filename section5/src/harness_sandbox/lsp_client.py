"""A bounded LSP stdio client (W4 spike, extended in W7).

The product did not speak LSP in W4; W7 integrates a real language server. The
W4 question was narrower: **if we spawn a language server inside the worker,
what stops it from outliving, out-writing or out-reading its policy?** This
client is the answer's shape:

* **Spawned, never imported.** The server is a child process in its own session
  (``start_new_session``), so a timeout kills the whole process group.
* **A scrubbed environment.** Only ``PATH``/``LANG``/``LC_ALL`` pass through,
  plus a fresh ``HOME``/``TMPDIR`` outside the workspace. Anything whose name
  looks like a credential is refused rather than filtered.
* **Bounded reads.** Every message is capped by ``max_bytes`` *before* the body
  is read, and a request has a wall-clock deadline; exceeding either kills the
  child instead of growing the buffer.
* **Bounded notifications.** Server-to-client notifications (diagnostics,
  progress) are queued for ``wait_notification``; past
  ``MAX_PENDING_NOTIFICATIONS`` the server is treated as misbehaving and killed.
* **Read-only by policy outside this class.** The client itself never writes;
  the drill mounts the workspace read-only, so a server that tries to write is
  refused by the mount, and the probe records the error.

W7 adds the four read-only operations the harness needs — diagnostics (via
``textDocument/didOpen`` + ``textDocument/publishDiagnostics``), document
symbols, definitions and references — as scenario methods and a CLI mode:

    python3 -I lsp_client.py --cwd <dir> --command '["pylsp"]' \\
        --scenario diagnostics --file <path>

The old single-request form (``--method``/``--params``) still works.
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
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

DEFAULT_TIMEOUT = 5.0
MAX_MESSAGE_BYTES = 4_000_000
MAX_PENDING_NOTIFICATIONS = 256
_MAX_DOCUMENT_BYTES = 2_000_000
_ENV_ALLOW = ("PATH", "LANG", "LC_ALL")
_ENV_EXTRA_ALLOW = frozenset({"PYTHONPATH", "PYTHONUNBUFFERED"})
_SECRET = re.compile(r"harness|deepseek|token|secret|password|api[-_]?key|credential", re.I)
_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".rs": "rust",
    ".go": "go",
    ".md": "markdown",
    ".json": "json",
}


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


def file_uri(path: str | Path) -> str:
    """An absolute ``file://`` URI for a local path, with spaces escaped."""
    return "file://" + quote(str(Path(path).resolve()))


def path_from_uri(uri: str) -> Path | None:
    """The local path behind a ``file://`` URI, or ``None`` for any other scheme."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


def language_id_for(path: str | Path) -> str:
    """The LSP language id for a path, by suffix; ``plaintext`` when unknown."""
    return _LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "plaintext")


def _dig(data: Any, dotted: str) -> Any:
    """Walk ``a.b.c`` through nested dicts, returning ``None`` on any gap."""
    current = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


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
        self._notifications: deque[dict[str, Any]] = deque()
        self._documents: dict[str, str] = {}
        self._next_version = 1
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

    def _answer_server_request(self, message: dict[str, Any]) -> None:
        """Answer a server-initiated request with a null result.

        We advertise no capabilities, so the only requests a well-behaved server
        sends are housekeeping (``window/workDoneProgress/create``,
        ``workspace/configuration``, ``client/registerCapability``). Leaving
        them unanswered makes real servers block; answering ``None`` is the
        documented way to say "nothing configured".
        """
        self._send({"jsonrpc": "2.0", "id": message["id"], "result": None})

    def _dispatch(self, message: dict[str, Any]) -> None:
        """Route a message that is not our pending response."""
        if "method" in message and message.get("id") is not None:
            self._answer_server_request(message)
            return
        if "method" in message:
            if len(self._notifications) >= MAX_PENDING_NOTIFICATIONS:
                self.kill()
                raise LspError(
                    f"more than {MAX_PENDING_NOTIFICATIONS} unread notifications queued;"
                    " the language server is misbehaving")
            self._notifications.append(message)

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
                    self._dispatch(message)
                    continue
                if "error" in message:
                    raise LspError(f"{method}: {message['error']}")
                return message.get("result")
        except (LspTimeout, LspOutputTooLarge):
            self.kill()
            raise

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self, root: str | Path | None = None) -> Any:
        root_uri = file_uri(root or self.cwd)
        result = self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"versionSupport": True},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                },
                "workspace": {"configuration": False, "workspaceFolders": True},
            },
            "workspaceFolders": [{"uri": root_uri, "name": Path(self.cwd).name or "workspace"}],
        })
        self.notify("initialized", {})
        return result

    # --------------------------------------------------------------- notifications

    def pending_notifications(self) -> list[dict[str, Any]]:
        return list(self._notifications)

    def _take_notification(self, method: str,
                           match: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Remove and return the first queued notification matching ``method``.

        ``match`` compares dotted paths inside ``params``, e.g.
        ``{"textDocument.uri": uri}``, so a diagnostics burst for another file
        cannot satisfy a wait for this one.
        """
        for index, message in enumerate(self._notifications):
            if message.get("method") != method:
                continue
            if match and not all(
                    _dig(message.get("params") or {}, path) == want
                    for path, want in match.items()):
                continue
            del self._notifications[index]
            return message
        return None

    def wait_notification(self, method: str, *,
                          match: dict[str, Any] | None = None,
                          timeout: float | None = None) -> dict[str, Any]:
        """Wait for a server notification, reading frames as needed."""
        found = self._take_notification(method, match)
        if found is not None:
            return found
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        try:
            while True:
                message = self._read_message(deadline)
                if message.get("method") and message.get("id") is None:
                    if message["method"] == method and (
                            not match or all(
                                _dig(message.get("params") or {}, path) == want
                                for path, want in match.items())):
                        return message
                self._dispatch(message)
                found = self._take_notification(method, match)
                if found is not None:
                    return found
        except (LspTimeout, LspOutputTooLarge):
            self.kill()
            raise

    # ---------------------------------------------------------------- documents

    def open_document(self, path: str | Path, text: str | None = None, *,
                      language_id: str | None = None, uri: str | None = None) -> str:
        """Send ``textDocument/didOpen``; returns the URI it was opened under.

        ``text`` defaults to the file's current bytes. The client never writes:
        an open document lives in the server's memory and the workspace mount
        stays read-only.
        """
        path_p = Path(path)
        uri = uri or file_uri(path_p)
        if text is None:
            if not path_p.is_file():
                raise LspError(f"not a file: {path_p}")
            raw = path_p.read_bytes()
            if len(raw) > _MAX_DOCUMENT_BYTES:
                raise LspError(f"document exceeds {_MAX_DOCUMENT_BYTES} bytes: {path_p}")
            text = raw.decode("utf-8", "replace")
        elif len(text.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
            raise LspError(f"document exceeds {_MAX_DOCUMENT_BYTES} bytes: {path_p}")
        version = self._next_version
        self._next_version += 1
        self._documents[uri] = text
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri,
            "languageId": language_id or language_id_for(path_p),
            "version": version,
            "text": text,
        }})
        return uri

    def diagnostics(self, uri: str, *, timeout: float | None = None) -> list[dict[str, Any]]:
        """The diagnostics the server publishes for ``uri`` after it is opened."""
        message = self.wait_notification(
            "textDocument/publishDiagnostics",
            match={"uri": uri},
            timeout=timeout,
        )
        return list((message.get("params") or {}).get("diagnostics") or [])

    def document_symbols(self, uri: str, *, timeout: float | None = None) -> Any:
        return self.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}},
                            timeout=timeout)

    def definition(self, uri: str, line: int, character: int, *,
                   timeout: float | None = None) -> Any:
        return self.request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        }, timeout=timeout)

    def references(self, uri: str, line: int, character: int, *,
                   include_declaration: bool = True,
                   timeout: float | None = None) -> Any:
        return self.request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        }, timeout=timeout)

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

    @property
    def pid(self) -> int:
        """The child's pid — an auditor can read ``/proc/<pid>/environ``."""
        return self._proc.pid

    def __enter__(self) -> LspSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _scenario(session: LspSession, name: str, path: Path, line: int, character: int,
              timeout: float | None) -> dict[str, Any]:
    """Run one read-only language-server scenario against one file."""
    if name == "diagnostics":
        uri = session.open_document(path)
        return {"uri": uri, "diagnostics": session.diagnostics(uri, timeout=timeout)}
    uri = session.open_document(path)
    if name == "documentSymbol":
        return {"uri": uri, "symbols": session.document_symbols(uri, timeout=timeout)}
    if name == "definition":
        return {"uri": uri, "locations": session.definition(uri, line, character,
                                                            timeout=timeout)}
    if name == "references":
        return {"uri": uri, "locations": session.references(uri, line, character,
                                                            timeout=timeout)}
    raise LspError(f"unknown scenario {name!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="bounded stdio LSP client")
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--command", required=True, help="JSON array of argv strings")
    ap.add_argument("--scenario", choices=("diagnostics", "documentSymbol",
                                           "definition", "references"))
    ap.add_argument("--file", help="workspace file the scenario opens")
    ap.add_argument("--line", type=int, default=0)
    ap.add_argument("--character", type=int, default=0)
    ap.add_argument("--method", help="single raw request (legacy form)")
    ap.add_argument("--params", default="{}", help="JSON object")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--max-bytes", type=int, default=MAX_MESSAGE_BYTES)
    args = ap.parse_args(argv)
    if bool(args.scenario) == bool(args.method):
        print(json.dumps({"error": {
            "code": "Usage", "message": "pass exactly one of --scenario or --method"}}))
        return 2
    if args.scenario and not args.file:
        print(json.dumps({"error": {
            "code": "Usage", "message": "--scenario requires --file"}}))
        return 2
    try:
        command = json.loads(args.command)
        params = json.loads(args.params)
        if not isinstance(command, list) or not isinstance(params, dict):
            raise LspError("command must be a JSON array and params a JSON object")
        with LspSession(command, cwd=args.cwd, timeout=args.timeout,
                        max_bytes=args.max_bytes) as session:
            initialized = session.initialize()
            if args.scenario:
                result = _scenario(session, args.scenario, Path(args.file),
                                   args.line, args.character, args.timeout)
            else:
                result = session.request(args.method, params)
            print(json.dumps({"initialize": initialized, "result": result}))
        return 0
    except (LspError, OSError, ValueError) as exc:
        print(json.dumps({"error": {"code": type(exc).__name__, "message": str(exc)}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
