"""W8 slice 2: the boundary sweeper's refusal logic, against a fake Docker daemon.

`sweep_orphan_boundaries` deletes infrastructure belonging to other runs, so the
tests are about what it REFUSES to touch. The fake models the two daemon
behaviours that make removal order matter and that a wrong order would hide:
`network inspect` lists only RUNNING containers as endpoints, and `network rm`
fails while any endpoint (the shared egress proxy alias among them) is attached.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone

from harness_orchestrator import container_runtime

PROXY = "harness-sandbox-egress-1"


class FakeDocker:
    """networks: {name: {"run": label, "containers": {cid: "running"|"exited"}}}"""

    def __init__(self, networks, *, created_ago=3600.0, listing_fails=False):
        self.calls = []
        self.listing_fails = listing_fails
        self.created_ago = created_ago
        self.now = datetime.now(timezone.utc)
        self.net = {
            name: {"run": spec.get("run", ""), "containers": dict(spec.get("containers", {}))}
            for name, spec in networks.items()
        }

    def __call__(self, *args, check=True):
        argv = list(args)
        self.calls.append(argv)

        def done(returncode=0, stdout="", stderr=""):
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

        if self.listing_fails and argv[:2] == ["network", "ls"]:
            return done(1, stderr="Cannot connect to the Docker daemon")
        if argv[:2] == ["network", "ls"]:
            return done(
                stdout="\n".join(f"{n}\t{i['run']}" for n, i in sorted(self.net.items()))
            )
        if argv[:2] == ["network", "inspect"]:
            name = argv[-1]
            info = self.net.get(name)
            if info is None:
                return done(1, stderr="no such network")
            if "--format" in argv:
                return done(stdout=(self.now - timedelta(seconds=self.created_ago)).isoformat())
            # Real Docker lists only running endpoints here — the reason the
            # sweeper cannot rely on inspect alone and must disconnect by name.
            running = {cid: {"Name": cid} for cid, st in info["containers"].items() if st == "running"}
            return done(stdout=json.dumps([{"Containers": running}]))
        if argv[0] == "ps":
            lines = []
            for info in self.net.values():
                for cid, st in info["containers"].items():
                    lines.append(f"{cid}|{cid}|{st}")
            return done(stdout="\n".join(lines))
        if argv[0] == "stop":
            info = self._by_container(argv[-1])
            if info is not None:
                info["containers"][argv[-1]] = "exited"
            return done()
        if argv[:2] == ["network", "disconnect"]:
            info = self.net.get(argv[2])
            if info is not None:
                info["containers"].pop(argv[-1], None)
            return done()
        if argv[0] == "rm":
            # `docker rm --force` drops the container and its endpoints with it;
            # a plain `rm` of a running one would fail, and nothing here does that.
            cid = argv[-1]
            for info in self.net.values():
                if cid in info["containers"] and info["containers"][cid] != "running":
                    del info["containers"][cid]
            return done()
        if argv[:2] == ["network", "rm"]:
            info = self.net.get(argv[-1])
            if info is None:
                return done(1, stderr="no such network")
            if info["containers"]:
                return done(1, stderr="network has active endpoints")
            del self.net[argv[-1]]
            return done()
        return done()

    def _by_container(self, cid):
        for info in self.net.values():
            if cid in info["containers"]:
                return info
        return None


def _install(monkeypatch, fake):
    monkeypatch.setattr(container_runtime, "docker", fake)
    return fake


def _boundary(run_id: str, letter: str, *, worker="running", relay="running", proxy="running"):
    """A network exactly as `ContainerBoundary.start()` leaves it.

    The sweeper removes containers by the names it derives from the network
    (``harness-<hex>`` and ``harness-<hex>-relay``), so the fixtures use those
    names as the container ids too — `docker stop`/`rm` accept either, and a fake
    that accepted invented ids would hide a name-derivation bug.
    """
    base = f"harness-{letter * 32}"
    containers = {}
    if worker is not None:
        containers[base] = worker
    if relay is not None:
        containers[f"{base}-relay"] = relay
    if proxy is not None:
        containers[PROXY] = proxy
    return f"{base}-net", {"run": run_id, "containers": containers}


def test_a_running_container_is_recognised_even_with_a_spaced_name(monkeypatch):
    # The state is the LAST piped field: a space-delimited listing put the check
    # on " running " and matched nothing, so a live run looked like a quiet
    # orphan. Pin the parser, because every refusal rule depends on it.
    def ps(*args, check=True):
        assert args[0] == "ps"
        return subprocess.CompletedProcess(
            list(args),
            0,
            stdout="c1|worker name with spaces|running\nc2|relay|exited\n",
            stderr="",
        )

    monkeypatch.setattr(container_runtime, "docker", ps)
    assert container_runtime._running_containers(["c1", "c2"]) == [("c1", "worker name with spaces")]


def test_a_settled_runs_boundary_is_reclaimed_end_to_end(monkeypatch):
    name, spec = _boundary("run-1", "a")
    fake = _install(monkeypatch, FakeDocker({name: spec}))
    report = container_runtime.sweep_orphan_boundaries(set(), {"run-1"})
    assert report["count"] == 1
    assert report["removed"][0]["runId"] == "run-1"
    # The worker + relay were still running: the detached client's corpse.
    assert report["containersStopped"] == 2
    assert name not in fake.net

    def first(*prefix):
        return next(
            i for i, c in enumerate(fake.calls) if c[: len(prefix)] == list(prefix)
        )

    # Order is load-bearing: an attached endpoint makes `network rm` fail.
    assert first("stop") < first("network", "disconnect") < first("network", "rm")
    # The shared egress proxy is attached ON PURPOSE and serves every run: it is
    # disconnected, never stopped — stopping it would take down its neighbours.
    assert all(c[0] != "stop" or c[-1] != PROXY for c in fake.calls)


def test_a_held_boundary_is_never_touched_even_when_settled(monkeypatch):
    name, spec = _boundary("run-2", "b")
    fake = _install(monkeypatch, FakeDocker({name: spec}))
    report = container_runtime.sweep_orphan_boundaries({"run-2"}, {"run-2"})
    assert report["removed"] == [] and report["kept"] == [name]
    assert name in fake.net
    destructive = [c for c in fake.calls if c[0] in {"rm", "stop"} or c[:2] == ["network", "rm"]]
    assert destructive == []


def test_an_unlabelled_network_is_not_ours_to_decide(monkeypatch):
    name, spec = _boundary("", "c")
    fake = _install(monkeypatch, FakeDocker({name: spec}))
    report = container_runtime.sweep_orphan_boundaries(set(), {"run-3"})
    assert report["kept"] == [name] and name in fake.net


def test_an_unknown_run_with_a_live_container_is_kept(monkeypatch):
    # No durable evidence (settled_run_ids=None) is the daemon's situation: the
    # weak rule must not stop a container that could belong to a peer's live run.
    name, spec = _boundary("run-4", "d")
    fake = _install(monkeypatch, FakeDocker({name: spec}))
    report = container_runtime.sweep_orphan_boundaries(set(), None)
    assert report["kept"] == [name] and name in fake.net


def test_the_weak_rule_still_reclaims_a_quiet_leftover_without_a_database(monkeypatch):
    name, spec = _boundary("run-5", "e", worker="exited", relay="exited", proxy="exited")
    fake = _install(monkeypatch, FakeDocker({name: spec}))
    report = container_runtime.sweep_orphan_boundaries(set(), None)
    assert report["count"] == 1 and name not in fake.net


def test_a_boundary_younger_than_the_grace_window_is_left_alone(monkeypatch):
    name, spec = _boundary("run-6", "f")
    fake = _install(monkeypatch, FakeDocker({name: spec}, created_ago=5.0))
    report = container_runtime.sweep_orphan_boundaries(set(), {"run-6"}, older_than_seconds=300)
    assert report["kept"] == [name] and name in fake.net


def test_a_daemon_that_cannot_be_listed_reports_failure_not_zero_orphans(monkeypatch):
    _install(monkeypatch, FakeDocker({}, listing_fails=True))
    report = container_runtime.sweep_orphan_boundaries(set(), {"run-7"})
    assert report["count"] == 0
    assert "Docker daemon" in report["error"]


def test_foreign_networks_are_not_matched_by_the_boundary_pattern(monkeypatch):
    foreign, spec = _boundary("run-8", "1")
    fake = _install(
        monkeypatch,
        FakeDocker(
            {
                "harness-bridge-net": {"run": "run-8", "containers": {}},
                **{foreign.replace("harness-", "harness-x", 1): spec},
            }
        ),
    )
    report = container_runtime.sweep_orphan_boundaries(set(), {"run-8"})
    assert report["removed"] == [] and "harness-bridge-net" in fake.net
