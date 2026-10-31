"""M07 worker daemon: the one process that holds the Docker socket and creates confined workers.

The orchestrator container has no socket by design (``container_runtime``: "Docker socket
stays on host"); mounting one there would hand the API a root-equivalent control channel, and
shipping ``off`` would give up the boundary the product is. This daemon is the reviewed
alternative: it accepts a run id, the goal and the run's policy, and nothing that shapes the
boundary. Paths come from its own ``DSH_WORKSPACE``/``DSH_SESSION_ROOT``, the image from its
``HARNESS_ENGINE_IMAGE``, the mode is always ``container``, the limits are ``confine``'s.

    POST /runs/<id>          NDJSON: {"progress": {...}} per tool event, then {"result": {...}}
    POST /runs/<id>/cancel   {"confirmed": bool}  also stops an orphan this daemon does not hold
    POST /runs/<id>/reap     {"confirmed": bool, "held": bool}  orphan only; never a held run
    GET  /runs/<id>          {"running": bool}  from Docker when this daemon does not hold it
    GET  /boundaries         {"runs": [run id, ...]} labelled boundaries Docker still has
    GET  /version            {"codeDigest": sha256} of this package, for W23 readiness
    POST /sweep              {"settledRunIds": [...], "olderThanSeconds": n} -> sweep report

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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from harness_orchestrator import confine, container_runtime, readiness
from harness_orchestrator.engine import DAEMON_TOKEN_ENV, DAEMON_URL_ENV, DeepSeekHarnessWrapper

logger = logging.getLogger(__name__)

MAX_BODY = 48 * 1024 * 1024  # goal images ride the request as base64
_PATH = re.compile(r"/runs/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(?:/(cancel|reap))?")
_RUNS: dict[str, DeepSeekHarnessWrapper] = {}
_LOCK = threading.Lock()


def _container_mode() -> bool:
    return os.environ.get(confine.CONFINEMENT_ENV) == "container"


def _reap_unheld(session_id: str) -> bool | None:
    """W22: stop a run's orphaned boundary; None when this daemon holds the run.

    After a SIGKILL the restarted daemon holds nothing, while the old worker may
    still run and write. Done under the lock: a new attempt for the same run
    cannot register and build its boundary until this one is gone.
    """
    with _LOCK:
        if session_id in _RUNS:
            return None
        if not _container_mode():
            return True  # process workers die with this daemon; no boundary can outlive it
        container_runtime.sweep_orphan_boundaries(set(), {session_id}, only_run_id=session_id)
        return not container_runtime.run_boundary_running(session_id)


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

    def _route(self) -> tuple[str, str] | None:
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return None
        match = _PATH.fullmatch(self.path)
        if not match:
            self._reply(404, {"error": "not found"})
            return None
        return match.group(1), match.group(2) or ""

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/boundaries" and self._authorized():
            runs = sorted(set(container_runtime.labelled_boundary_runs().values()))
            self._reply(200, {"runs": runs})
            return
        if self.path == "/version" and self._authorized():  # W23 readiness: same code?
            self._reply(200, {"codeDigest": readiness.code_digest()})
            return
        route = self._route()
        if route is None or route[1]:
            return None if route is None else self._reply(404, {"error": "not found"})
        with _LOCK:
            wrapper = _RUNS.get(route[0])
        if wrapper is None:  # W22: not ours in memory is not proof nothing runs
            running = _container_mode() and container_runtime.run_boundary_running(route[0])
            self._reply(200, {"running": running})
            return
        self._reply(200, {"running": wrapper.is_worker_running(route[0])})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/sweep" and self._authorized():
            self._sweep()
            return
        route = self._route()
        if route is None:
            return
        session_id, action = route
        if action == "reap":
            confirmed = _reap_unheld(session_id)
            self._reply(200, {"confirmed": bool(confirmed), "held": confirmed is None})
            return
        if action == "cancel":
            confirmed = _reap_unheld(session_id)
            if confirmed is None:
                with _LOCK:
                    wrapper = _RUNS.get(session_id)
                confirmed = True if wrapper is None else wrapper.cancel(session_id)
            self._reply(200, {"confirmed": confirmed})
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

    def _sweep(self) -> None:
        """W22: the strong sweep, with the settlement proof supplied by the orchestrator.

        A SIGKILLed daemon leaves its worker running; this process cannot tell that
        corpse from a live run, the orchestrator's event log can. Runs this daemon
        still holds are never touched, whatever the caller says.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 1024 * 1024:
            self._reply(413, {"error": "body missing or too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            settled = {str(r) for r in body["settledRunIds"]}
            grace = float(body["olderThanSeconds"])
        except (ValueError, KeyError, TypeError) as exc:
            self._reply(400, {"error": f"bad sweep body: {type(exc).__name__}"})
            return
        with _LOCK:
            held = set(_RUNS)
        report = container_runtime.sweep_orphan_boundaries(
            held, settled - held, older_than_seconds=grace
        )
        self._reply(200, report)


def serve(bind: str) -> ThreadingHTTPServer:
    host, _, port = bind.rpartition(":")
    return ThreadingHTTPServer((host or "0.0.0.0", int(port)), Handler)  # noqa: S104


def _boundary_sweeper(interval: float, grace: float) -> None:
    """Reclaim boundary networks this daemon no longer owns (W8 slice 2).

    This process is the one that creates them and the one that holds the Docker
    socket, so it is the only place the cleanup can run - and it runs without the
    database, which is why it gets the WEAK proof only: ``settled_run_ids=None``
    disables the rule that stops a still-running container. A network is removed
    when this daemon holds no run for its label, it is older than the grace
    window, and nothing attached to it is running.

    That leaves the SIGKILLed-daemon case uncleaned on purpose: after the daemon
    dies, its own worker container is still running and nothing local can tell
    that apart from a live run. The daemon restarts, sweeps the quiet leftovers on
    its first pass; the corpse itself is stopped by the orchestrator's durable-proof
    sweep, which reaches this socket through ``POST /sweep`` (W22).
    """
    while True:
        time.sleep(interval)
        try:
            with _LOCK:
                held = set(_RUNS)
            report = container_runtime.sweep_orphan_boundaries(held, None, older_than_seconds=grace)
            if report.get("error"):
                logger.warning("boundary sweep could not list networks: %s", report["error"])
            elif report["count"]:
                logger.info(
                    "boundary sweep removed %d orphan network(s): %s",
                    report["count"],
                    [entry["network"] for entry in report["removed"]],
                )
        except Exception:  # noqa: BLE001 — cleanup must never take the daemon down
            logger.exception("boundary sweep pass failed")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.environ.get(DAEMON_TOKEN_ENV):
        print(f"{DAEMON_TOKEN_ENV} is required; refusing to start", file=sys.stderr)
        return 2
    # The daemon exists to run this boundary and no other, and never forwards to itself.
    os.environ[confine.CONFINEMENT_ENV] = "container"
    for key in (DAEMON_URL_ENV, "HARNESS_ENGINE_WORKER_MODULE"):
        os.environ.pop(key, None)
    interval = float(os.environ.get("HARNESS_BOUNDARY_SWEEP_SECONDS", "600") or 0)
    if interval > 0:
        threading.Thread(
            target=_boundary_sweeper,
            args=(interval, float(os.environ.get("BOUNDARY_SWEEP_GRACE_SECONDS", "300") or 300)),
            daemon=True,
            name="boundary-sweep",
        ).start()
    server = serve(os.environ.get("HARNESS_WORKER_DAEMON_BIND", "0.0.0.0:8010"))
    logger.info("worker daemon listening on %s:%s", *server.server_address[:2])
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
