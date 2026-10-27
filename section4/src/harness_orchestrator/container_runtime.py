"""Per-run private network and fixed-origin model relay. Docker socket stays on host."""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

IMAGE = "harness-engine-worker:p0"

#: W4: the engine worker joins its run network with no gateway; the only peer
#: with a route out is the allow-list proxy, attached here under this alias and
#: reached on the *web* purpose port (docs + provider endpoints). The sandbox's
#: narrower port (3128) is not given to the worker.
EGRESS_PROXY_CONTAINER_ENV = "HARNESS_EGRESS_PROXY_CONTAINER"
EGRESS_PROXY_ALIAS = "harness-egress"
EGRESS_WEB_PORT = int(os.environ.get("EGRESS_WEB_PORT", "3129"))

RELAY_READY = """
import socket, time
for _ in range(100):
    try:
        with socket.create_connection(('127.0.0.1', 8080), timeout=.2):
            break
    except OSError:
        time.sleep(.05)
else:
    raise SystemExit('model relay did not become ready')
"""


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True, timeout=30
    )


class ContainerBoundary:
    def __init__(self, endpoint: str):
        url = urlsplit(endpoint or "https://api.deepseek.com")
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError(
                "container model endpoint must be an http(s) origin without credentials"
            )
        self.url = url
        self.name = "harness-" + uuid.uuid4().hex
        self.network = self.name + "-net"
        self.relay = self.name + "-relay"
        self.image = os.environ.get("HARNESS_ENGINE_IMAGE", IMAGE)
        self.web_proxy_url: str | None = None
        self.egress_proxy: str | None = None

    def _attach_egress_proxy(self) -> str | None:
        """Make the allow-list proxy the run network's only way out.

        Docker lets a container join several networks, so connecting the proxy to
        this gateway-less network is enough: the worker reaches ``harness-egress``
        and nothing else. Missing proxy (bare local dev, no compose) is not fatal
        — web_fetch then fails closed with a name-resolution error, and the
        denial path still works because it never dials.
        """
        name = os.environ.get(EGRESS_PROXY_CONTAINER_ENV, "harness-sandbox-egress-1")
        connected = docker(
            "network", "connect", "--alias", EGRESS_PROXY_ALIAS, self.network, name,
            check=False,
        )
        if connected.returncode != 0:
            return None
        self.egress_proxy = name
        return f"http://{EGRESS_PROXY_ALIAS}:{EGRESS_WEB_PORT}"

    def start(self) -> str:
        docker("network", "create", "--internal", self.network)
        try:
            self.web_proxy_url = self._attach_egress_proxy()
            host = self.url.hostname
            if host in {"localhost", "127.0.0.1", "::1"}:
                host = "host.docker.internal"
            docker(
                "create",
                "--name",
                self.relay,
                "--network",
                "bridge",
                "--add-host",
                "host.docker.internal:host-gateway",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "32",
                "--memory",
                "128m",
                "--cpus",
                "0.25",
                "--user",
                "10001:10001",
                "--entrypoint",
                "python",
                self.image,
                "-m",
                "harness_orchestrator.model_relay",
                self.url.scheme,
                host,
                str(self.url.port or (443 if self.url.scheme == "https" else 80)),
            )
            docker("network", "connect", "--alias", "model", self.network, self.relay)
            docker("start", self.relay)
            docker("exec", self.relay, "python", "-c", RELAY_READY)

        except Exception:
            self.close()
            raise
        return urlunsplit(("http", "model:8080", self.url.path, self.url.query, ""))

    def confirmed_gone(self) -> bool:
        try:
            result = docker(
                "container",
                "ls",
                "--all",
                "--quiet",
                "--filter",
                f"name=^/{self.name}$",
                check=False,
            )
            return result.returncode == 0 and not result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return False

    def kill(self, proc: subprocess.Popen | None = None) -> bool:
        try:
            return self._kill(proc)
        except (OSError, subprocess.SubprocessError):
            return False

    def _kill(self, proc: subprocess.Popen | None = None) -> bool:
        # The CLI can exist before Docker creates the container. Keep killing until
        # the launcher exits, then inspect; an early empty inspect is not confirmation.
        deadline = time.monotonic() + 10
        while True:
            docker("rm", "--force", self.name, check=False)
            result = docker(
                "container",
                "ls",
                "--all",
                "--quiet",
                "--filter",
                f"name=^/{self.name}$",
                check=False,
            )
            if (proc is None or proc.poll() is not None) and result.returncode == 0:
                return not result.stdout.strip()
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def close(self) -> None:
        # A network with any attached container cannot be removed, and W4 attaches the
        # shared egress proxy to this run's network on purpose — so the proxy has to
        # leave before the network can. Without this every run leaked its network:
        # measured 2026-10-27 on one dev stack with 17 orphans, until Docker answered
        # "all predefined address pools have been fully subnetted" and NO run could
        # start at all.
        if self.egress_proxy:
            try:
                docker("network", "disconnect", self.network, self.egress_proxy, check=False)
            except (OSError, subprocess.SubprocessError):
                pass  # best effort: the removals below still try, and a leak is loud
        for args in (("rm", "--force", self.relay), ("network", "rm", self.network)):
            try:
                docker(*args, check=False)
            except (OSError, subprocess.SubprocessError):
                pass  # daemon loss must never turn an unconfirmed stop into success
