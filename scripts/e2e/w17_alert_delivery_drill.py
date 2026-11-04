#!/usr/bin/env python3
"""W17 Stage-2: prove alert routing reaches a LIVE target, using the real template.

docs/runbooks/slo-and-capacity.md: "Prometheus currently evaluates locally; there
is no configured Alertmanager delivery destination. Until delivery is connected
and exercised, operators must keep the Prometheus alerts page open." The stack's
live Alertmanager points ``smtp_smarthost`` at ``mailpit:1025`` — a container that
exited six days ago — so a firing alert (``HarnessBackupStale`` is firing as this
runs) is delivered nowhere.

This drill does not invent a paging destination (that is the owner's decision).
It proves the *mechanism*: it renders ``deploy/observability/alertmanager.yml``
exactly the way the compose entrypoint does, enables the webhook receiver, runs a
throwaway Alertmanager, and asserts a synthetic alert lands on a live local HTTP
sink. A team that sets ``HARNESS_ALERT_WEBHOOK_URL`` to their real relay gets
this path with their own URL.

Nothing in the shared deployment is modified; the throwaway container and the
temporary config are removed in ``finally``.

    python3 scripts/e2e/w17_alert_delivery_drill.py
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "deploy/observability/alertmanager.yml"
IMAGE = "prom/alertmanager:v0.28.1"
ALERT_NAME = "HarnessDrillProbe"
WAIT_SECONDS = 60.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Sink:
    """A live HTTP target that records every POST body it receives."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.times: list[float] = []
        self.started = time.time()
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = {"_raw": raw.decode("utf-8", "replace")}
                with outer.lock:
                    outer.bodies.append(payload)
                    outer.times.append(time.time())
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *_args) -> None:
                return  # keep the drill output clean

        self.port = free_port()
        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def wait_for(self, name: str, seconds: float) -> dict | None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            with self.lock:
                for body, at in zip(self.bodies, self.times, strict=False):
                    alerts = body.get("alerts", []) if isinstance(body, dict) else []
                    if any(a.get("labels", {}).get("alertname") == name for a in alerts):
                        body["_receivedAfterSeconds"] = round(at - self.started, 3)
                        return body
            time.sleep(0.5)
        return None


def sh(*argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, check=check)


def render_config(sink_port: int) -> str:
    """Render the template the way deploy/docker-compose.yml does, then point the
    webhook at the live sink instead of url_file.

    ``#WEBHOOK#`` prefix lines are enabled (marker stripped); the remaining
    ``${VAR}`` placeholders are filled with drill values. SMTP points at a
    deliberately dead host: this drill proves the webhook channel, and a failed
    email branch must not stop it.
    """
    values = {
        "HARNESS_SMTP_HOST": "smtp.invalid",
        "HARNESS_SMTP_PORT": "25",
        "HARNESS_SMTP_USERNAME": "drill",
        "HARNESS_SMTP_FROM": "drill@harness.local",
        "HARNESS_SMTP_STARTTLS": "false",
        "HARNESS_ALERT_EMAIL_TO": "drill@harness.local",
    }
    lines = []
    for line in TEMPLATE.read_text().splitlines():
        line = re.sub(r"^ *#WEBHOOK#", "", line)
        for key, value in values.items():
            line = line.replace("${" + key + "}", value)
        lines.append(line)
    rendered = "\n".join(lines) + "\n"
    sink_url = f"http://host.docker.internal:{sink_port}/alert"
    return rendered.replace("url_file: /tmp/webhook_url", f"url: {sink_url}")


def main() -> int:
    tag = uuid.uuid4().hex[:10]
    container = f"w17-am-drill-{tag}"
    evidence: dict = {"container": container, "checks": {}, "verdict": "FAIL"}
    path = REPO / "docs/briefs/evidence/w17"
    path.mkdir(parents=True, exist_ok=True)
    sink = Sink()
    work = Path(tempfile.mkdtemp(prefix=f"w17-am-{tag}-"))
    try:
        config = work / "alertmanager.yml"
        config.write_text(render_config(sink.port))
        sink.start()
        sink.started = time.time()
        api_port = free_port()
        sh("docker", "run", "-d", "--rm", "--name", container,
           "--add-host", "host.docker.internal:host-gateway",
           "-p", f"127.0.0.1:{api_port}:9093",
           "-v", f"{work}:/cfg:ro", IMAGE,
           "--config.file=/cfg/alertmanager.yml", "--storage.path=/tmp/am")
        api = f"http://127.0.0.1:{api_port}"
        for _ in range(40):
            out = sh("docker", "port", container, "9093/tcp", check=False).stdout.strip()
            if out:
                break
            time.sleep(0.5)
        assert out, "throwaway alertmanager never bound its API port"
        evidence["api"] = api
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"{api}/-/ready", timeout=2) as resp:
                    if resp.status == 200:
                        break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.5)
        check = sh("docker", "exec", container, "amtool", "check-config",
                   "/cfg/alertmanager.yml", check=False)
        evidence["checkConfig"] = check.stdout.strip() or check.stderr.strip()

        starts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        payload = [{
            "labels": {"alertname": ALERT_NAME, "severity": "page", "instance": "drill"},
            "annotations": {"summary": "W17 alert-delivery drill"},
            "startsAt": starts,
        }]
        req = urllib.request.Request(
            f"{api}/api/v2/alerts", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            accepts = resp.status
        body = sink.wait_for(ALERT_NAME, WAIT_SECONDS)
        assert body is not None, f"no delivery to the live sink within {WAIT_SECONDS:g}s"
        delivered = [a for a in body.get("alerts", [])
                     if a.get("labels", {}).get("alertname") == ALERT_NAME]
        evidence["checks"] = {
            "configValid": "SUCCESS" in evidence["checkConfig"],
            "apiAcceptedSyntheticAlert": accepts in (200, 202),
            "sinkReceivedPost": True,
            "alertnamePreserved": bool(delivered),
            "status": delivered[0].get("status") if delivered else None,
        }
        assert evidence["checks"]["configValid"], "rendered config failed amtool check"
        assert evidence["checks"]["alertnamePreserved"], "sink got a POST with no matching alert"
        evidence["receivedAfterSeconds"] = body.get("_receivedAfterSeconds")
        evidence["receiver"] = body.get("receiver")
        evidence["verdict"] = "PASS"
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        sh("docker", "rm", "-f", container, check=False)
        if sink.thread.is_alive():
            sink.stop()
        shutil.rmtree(work, ignore_errors=True)
        out = path / f"w17-alert-delivery-{tag}.json"
        out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"verdict": evidence["verdict"], "evidence": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
