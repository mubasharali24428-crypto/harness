"""Per-run private network and fixed-origin model relay. Docker socket stays on host."""

from __future__ import annotations

import json
import os
import re
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


#: A boundary network with this label can be traced back to the run that owns
#: it. Without it, an orphaned network is anonymous: nothing on `docker network
#: ls` says which run created it, so no sweeper can tell a crash's leftovers from
#: a live run's equipment — and guessing means deleting a network a running agent
#: is using. See `sweep_orphan_boundaries`.
RUN_LABEL = "harness-run"
BOUNDARY_NET_RE = re.compile(r"^harness-[0-9a-f]{32}-net$")


class ContainerBoundary:
    def __init__(self, endpoint: str, run_id: str | None = None):
        url = urlsplit(endpoint or "https://api.deepseek.com")
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError(
                "container model endpoint must be an http(s) origin without credentials"
            )
        self.url = url
        self.run_id = run_id
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
        create = ["network", "create", "--internal"]
        if self.run_id:
            create += ["--label", f"{RUN_LABEL}={self.run_id}"]
        create.append(self.network)
        docker(*create)
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


def labelled_boundary_runs() -> dict[str, str]:
    """``{network: run id}`` for the boundary networks Docker still has.

    A caller that must ask a database about those runs (which of them the
    lifecycle says are over) needs this list first; it is the same read the
    sweep does, separated so the sweep itself stays free of the DB.
    """
    listing = docker(
        "network", "ls", "--format", "{{.Name}}\t{{.Label \"harness-run\"}}", check=False
    )
    if listing.returncode != 0:
        return {}
    found: dict[str, str] = {}
    for line in listing.stdout.splitlines():
        name, _, label = line.partition("\t")
        name = name.strip()
        label = label.strip()
        if label and BOUNDARY_NET_RE.match(name):
            found[name] = label
    return found


def _attached_container_ids(network: str) -> list[str]:
    out = docker("network", "inspect", network, check=False)
    if out.returncode != 0:
        return []
    try:
        import json

        info = json.loads(out.stdout)[0]
    except (ValueError, IndexError, KeyError):
        return []  # unreadable: the caller treats it as "in use", which is the safe side
    return sorted((info.get("Containers") or {}).keys())


def _running_containers(ids: list[str]) -> list[tuple[str, str]]:
    """``(id, name)`` for the given container ids that are currently running.

    Piped, not space-separated: a container NAME may contain spaces, and a
    space-delimited line was mis-parsed into "no container is running" — which
    reads as a fully quiet network to the sweeper, i.e. a licence to delete a
    live run's boundary. Test-pinned in ``test_boundary_sweep``.
    """
    if not ids:
        return []
    out = docker(
        "ps", "--all", "--no-trunc", "--format", "{{.ID}}|{{.Names}}|{{.State}}", check=False
    )
    wanted = set(ids)
    found: list[tuple[str, str]] = []
    for line in out.stdout.splitlines():
        cid, _, rest = line.partition("|")
        if cid not in wanted:
            continue
        name, _, state = rest.rpartition("|")
        if state.strip() == "running":
            found.append((cid, name or cid))
    return found


def sweep_orphan_boundaries(
    held_run_ids: set[str],
    settled_run_ids: set[str] | None,
    *,
    older_than_seconds: float = 0.0,
) -> dict[str, object]:
    """Remove per-run boundaries that their owner demonstrably abandoned.

    The engine's own `close()` runs in a `finally`, so the ordinary failure paths
    (worker killed, timeout, exception) already clean up. What they cannot clean
    up is the process being killed: a SIGKILLed launcher leaves its network, its
    relay container and - because `docker run` without `-d` detaches when its
    client dies - a *running* worker container behind. Nothing reaped those, so
    leaks accumulated silently until Docker refused every new run (W4 measured
    17-20 orphan networks on one dev stack, then "all predefined address pools
    have been fully subnetted" and NO run could start).

    Every candidate is labelled, older than `older_than_seconds`, and NOT held by
    this process (`held_run_ids`: the boundaries of the runs it is executing).
    Then two independent proofs, because a deployment gives the sweeper only one
    of them:

    * ``settled_run_ids`` - the durable lifecycle says the run is terminal. This
      may stop a still-running container: an orphaned worker has no client left,
      and only the DB knows the run it belongs to is finished.
    * otherwise, nothing attached is running. Safe without any DB, which is why
      the socket-holding daemon can sweep on this rule alone - but it cannot
      catch the detached-client corpse, whose container is still up.

    A run satisfying neither is KEPT. "I do not know this run" is not evidence of
    death: an unlabelled pre-label network, a local CLI run with no ``agent_runs``
    row, and a peer daemon's live boundary all look identical to an orphan from
    here. The conservative side costs a leak the next sweep catches; the other
    side stops a live agent's network out from under it, which the W7/W8 gates
    forbid.

    Removal order is the one `close()` learned the hard way: stop the run's own
    containers, disconnect every endpoint (the shared egress proxy included), then
    remove the network - a network with an attached endpoint cannot be removed.
    """
    listing = docker(
        "network", "ls", "--format", "{{.Name}}\t{{.Label \"harness-run\"}}", check=False
    )
    if listing.returncode != 0:
        # An empty list here would read as "no orphans": say what actually failed.
        return {
            "removed": [],
            "kept": [],
            "count": 0,
            "error": (listing.stderr or listing.stdout or "docker network ls failed")[:300],
        }
    removed: list[str] = []
    kept: list[str] = []
    for line in listing.stdout.splitlines():
        name, _, label = line.partition("\t")
        name = name.strip()
        if not BOUNDARY_NET_RE.match(name):
            continue
        run_id = label.strip()
        if not run_id or run_id in held_run_ids:
            kept.append(name)  # anonymous, or ours to keep: not ours to destroy
            continue
        if older_than_seconds > 0:
            created = docker(
                "network", "inspect", "--format", "{{json .Created}}", name, check=False
            ).stdout.strip()
            try:
                # W22: plain {{.Created}} is Go's "2026-10-30 18:35:07.8 +0000 UTC", which
                # fromisoformat rejects - every network was kept. JSON gives RFC 3339.
                age = time.time() - _parse_iso(json.loads(created))
            except (ValueError, TypeError):
                kept.append(name)
                continue
            if age < older_than_seconds:
                kept.append(name)
                continue
        base = name[: -len("-net")]
        proxy = os.environ.get(EGRESS_PROXY_CONTAINER_ENV, "harness-sandbox-egress-1")
        stopped: list[str] = []
        attached = _attached_container_ids(name)
        # The egress proxy is attached here ON PURPOSE and serves every run: it
        # may LEAVE this network, but stopping or removing it would take the
        # allow-list gateway down for the live runs beside this one. (`close()`
        # disconnects it for exactly this reason.)
        running = [cid for cid, name in _running_containers(attached) if name != proxy]
        settled = settled_run_ids is not None and run_id in settled_run_ids
        if not settled and running:
            kept.append(name)  # rule 1 alone cannot judge a container that is still up
            continue
        for cid in running:
            if docker("stop", "--time", "5", cid, check=False).returncode == 0:
                stopped.append(cid)
        for container in (base, f"{base}-relay"):
            docker("rm", "--force", container, check=False)
        for cid in attached:
            docker("network", "disconnect", "--force", name, cid, check=False)
        docker(
            "network",
            "disconnect",
            name,
            os.environ.get(EGRESS_PROXY_CONTAINER_ENV, "harness-sandbox-egress-1"),
            check=False,
        )
        gone = docker("network", "rm", name, check=False)
        if gone.returncode == 0:
            removed.append({"network": name, "runId": run_id, "stopped": stopped})
        else:
            kept.append(name)
    return {
        "removed": removed,
        "kept": kept,
        "count": len(removed),
        "containersStopped": sum(len(entry["stopped"]) for entry in removed),
    }


def _parse_iso(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
