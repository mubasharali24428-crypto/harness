"""M07 worker daemon: the one process that holds the Docker socket and creates confined workers.

The orchestrator container has no socket by design (``container_runtime``: "Docker socket
stays on host"); mounting one there would hand the API a root-equivalent control channel, and
shipping ``off`` would give up the boundary the product is. This daemon is the reviewed
alternative: it accepts a run id, the goal and the run's policy, and nothing that shapes the
boundary. Paths come from its own ``DSH_WORKSPACE``/``DSH_SESSION_ROOT``, the image from its
``HARNESS_ENGINE_IMAGE``, the mode is always ``container``, the limits are ``confine``'s.

    POST /runs/<id>          NDJSON: {"progress": {...}} per tool event, then {"result": {...}}
    POST /runs/<id>/cancel   {"confirmed": bool}
    GET  /runs/<id>          {"running": bool}

Every call carries ``Authorization: Bearer $HARNESS_WORKER_DAEMON_TOKEN``; without a token
configured the daemon refuses to start. It must listen only on a network the orchestrator
shares, never a published port.

    python -m harness_orchestrator.worker_daemon
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from harness_orchestrator import confine
from harness_orchestrator.engine import DAEMON_TOKEN_ENV, DAEMON_URL_ENV, DeepSeekHarnessWrapper

logger = logging.getLogger(__name__)

MAX_BODY = 48 * 1024 * 1024  # goal images ride the request as base64
_PATH = re.compile(r"/runs/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(/cancel)?")
_RUNS: dict[str, DeepSeekHarnessWrapper] = {}
_LOCK = threading.Lock()


def _wrapper(session_id: str, body: dict[str, Any]) -> DeepSeekHarnessWrapper:
    wrapper = DeepSeekHarnessWrapper(
        api_key=str(body.get("apiKey") or ""),
        base_url=str(body.get("baseUrl") or ""),
        model=str(body.get("model") or "") or None,
        live_mode="dry",  # construction only; the run below is explicit
        isolated=True,
    )
    wrapper.daemon_url = ""  # this IS the daemon
    env = body.get("env") if isinstance(body.get("env"), dict) else {}
    wrapper.worker_env = {
        k: str(v)
        for k, v in env.items()
        if isinstance(k, str) and k.startswith(confine.ENV_ALLOW_PREFIX) and "\0" not in str(v)
    }
    policy = body.get("shellPolicy")
    if isinstance(policy, dict):
        wrapper.set_shell_policy(session_id, policy)
    return wrapper


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0: the stream ends when the connection closes - no chunking to get wrong.
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # never the body: it carries the key
        logger.info("%s %s", self.command, self.path.split("?")[0])

    def _authorized(self) -> bool:
        token = os.environ.get(DAEMON_TOKEN_ENV, "")
        given = self.headers.get("Authorization", "")
        return bool(token) and hmac.compare_digest(given.encode(), f"Bearer {token}".encode())

    def _reply(self, code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _route(self) -> tuple[str, bool] | None:
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return None
        match = _PATH.fullmatch(self.path)
        if not match:
            self._reply(404, {"error": "not found"})
            return None
        return match.group(1), bool(match.group(2))

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        route = self._route()
        if route is None or route[1]:
            return None if route is None else self._reply(404, {"error": "not found"})
        with _LOCK:
            wrapper = _RUNS.get(route[0])
        self._reply(200, {"running": bool(wrapper and wrapper.is_worker_running(route[0]))})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        route = self._route()
        if route is None:
            return
        session_id, cancel = route
        if cancel:
            with _LOCK:
                wrapper = _RUNS.get(session_id)
            self._reply(200, {"confirmed": True if wrapper is None else wrapper.cancel(session_id)})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            self._reply(413, {"error": "body missing or too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
        except ValueError as exc:
            self._reply(400, {"error": str(exc)})
            return
        with _LOCK:
            if session_id in _RUNS:
                self._reply(409, {"error": "run already has a worker"})
                return
            wrapper = _RUNS[session_id] = _wrapper(session_id, body)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()

            def emit(record: dict[str, Any]) -> None:
                self.wfile.write((json.dumps({"progress": record}, default=str) + "\n").encode())
                self.wfile.flush()

            # A disconnect raises inside emit, which _run_isolated turns into an error after its
            # own cleanup kills the container: a worker never outlives the orchestrator's stream.
            text, status, used, events = wrapper._run_isolated(
                str(body.get("goal") or ""),
                session_id,
                wrapper.model,
                emit,
                body.get("images") or None,
            )
            result = {"text": text, "status": status, "model": used, "events": events}
            self.wfile.write((json.dumps({"result": result}, default=str) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):
            wrapper.cancel(session_id)
        finally:
            with _LOCK:
                _RUNS.pop(session_id, None)


def serve(bind: str) -> ThreadingHTTPServer:
    host, _, port = bind.rpartition(":")
    return ThreadingHTTPServer((host or "0.0.0.0", int(port)), Handler)  # noqa: S104


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.environ.get(DAEMON_TOKEN_ENV):
        print(f"{DAEMON_TOKEN_ENV} is required; refusing to start", file=sys.stderr)
        return 2
    # The daemon exists to run this boundary and no other, and never forwards to itself.
    os.environ[confine.CONFINEMENT_ENV] = "container"
    for key in (DAEMON_URL_ENV, "HARNESS_ENGINE_WORKER_MODULE"):
        os.environ.pop(key, None)
    server = serve(os.environ.get("HARNESS_WORKER_DAEMON_BIND", "0.0.0.0:8010"))
    logger.info("worker daemon listening on %s:%s", *server.server_address[:2])
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
