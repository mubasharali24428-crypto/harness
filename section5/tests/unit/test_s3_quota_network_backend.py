"""S3: quotas (cpu/memory/procs/disk), network modes, backend fail-closed."""
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from harness_sandbox.backends import (
    DockerSandboxRunner,
    build_docker_argv,
    docker_available,
    select_backend,
)
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.network import (
    BrokeredFetcher,
    NetworkMode,
    UnrestrictedGrant,
    process_network_allowed,
)
from harness_sandbox.policy import SecurityPolicyViolationError
from harness_sandbox.quota import ResourceQuota, ResourceQuotaExceededError
from harness_sandbox.service import SandboxService


def runner(tmp_path, quota):
    # confinement "off": quota behaviour is what is under test, not seatbelt.
    return LocalSandboxRunner(storage_root=tmp_path, confinement="off", quota=quota)


async def run(r, cmd, task="t1"):
    handle = r.create_task(task, "repo", "main") if task not in r.active_tasks else None
    handle = handle or type("H", (), {"taskId": task})()
    return await r.exec_structured(handle, cmd)


async def test_memory_quota_kills_the_command(tmp_path):
    r = runner(tmp_path, ResourceQuota(memory_mb=80))
    cmd = 'python3 -c "b=bytearray(400*1024*1024); import time; time.sleep(10)"'
    t = time.monotonic()
    with pytest.raises(ResourceQuotaExceededError) as e:
        await run(r, cmd)
    assert e.value.resource_name == "memory_mb"
    assert time.monotonic() - t < 8  # killed long before the 10 s sleep


async def test_process_quota_stops_fan_out(tmp_path):
    r = runner(tmp_path, ResourceQuota(max_processes=8))
    cmd = (
        'python3 -c "import subprocess,time; '
        "[subprocess.Popen(['sleep','8']) for _ in range(40)]; time.sleep(8)\""
    )
    with pytest.raises(ResourceQuotaExceededError) as e:
        await run(r, cmd)
    assert e.value.resource_name == "processes"
    # the whole group is gone: no leftover sleeps from this task's group
    left = subprocess.run(["pgrep", "-f", "sleep 8"], capture_output=True, text=True).stdout
    assert left.strip() == ""


async def test_disk_quota_stops_incremental_writes(tmp_path):
    r = runner(tmp_path, ResourceQuota(disk_mb=6))
    cmd = (
        "python3 -c \"import time; [(open('f%d'%i,'wb').write(b'0'*2097152), time.sleep(0.05))"
        ' for i in range(100)]"'
    )
    with pytest.raises(ResourceQuotaExceededError) as e:
        await run(r, cmd)
    assert e.value.resource_name == "disk_mb"


async def test_cpu_rlimit_and_file_size_rlimit(tmp_path):
    r = runner(tmp_path, ResourceQuota(cpu_seconds=1, max_file_mb=1))
    res = await run(r, 'python3 -c "while True: pass"', "cpu")
    assert res.exit_code != 0  # SIGXCPU from the kernel, not our guard
    res = await run(r, 'python3 -c "open(\'x\',\'wb\').write(b\'0\'*5000000)"', "fsz")
    assert res.exit_code != 0


async def test_no_quota_means_unchanged_behaviour(tmp_path):
    r = runner(tmp_path, None)
    assert (await run(r, 'python3 -c "print(1)"')).exit_code == 0


def test_quota_rejects_nonpositive():
    with pytest.raises(ValueError):
        ResourceQuota(memory_mb=0)


# ---- network modes -----------------------------------------------------------------
def test_process_network_defaults_closed_and_unrestricted_needs_a_grant():
    assert process_network_allowed(NetworkMode.DISABLED, "t", None) is False
    assert process_network_allowed(NetworkMode.ALLOWLISTED, "t", None) is False
    with pytest.raises(SecurityPolicyViolationError):
        process_network_allowed(NetworkMode.UNRESTRICTED, "t", None)
    g = UnrestrictedGrant("t", "alice", expires_at=100)
    assert process_network_allowed(NetworkMode.UNRESTRICTED, "t", g, now=50) is True
    for bad in (UnrestrictedGrant("other", "alice", 100), UnrestrictedGrant("t", "", 100),
                UnrestrictedGrant("t", "alice", 10)):
        with pytest.raises(SecurityPolicyViolationError):
            process_network_allowed(NetworkMode.UNRESTRICTED, "t", bad, now=50)


async def test_unrestricted_network_does_not_lift_forbidden_paths(tmp_path):
    svc = SandboxService(runner=LocalSandboxRunner(storage_root=tmp_path, confinement="off",
                                                   allow_network=True))
    h = svc.run_task("t9", "repo", "main", forbidden_paths=["infra/prod"])
    with pytest.raises(SecurityPolicyViolationError):
        await svc.exec_structured(h, "cat infra/prod/secrets.tf")


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/ok":
            self.send_response(200); self.end_headers(); self.wfile.write(b"hi")  # noqa: E702
        elif self.path == "/to-evil":
            self.send_response(302)  # noqa: E702
            self.send_header("Location", "http://evil.test/x")
            self.end_headers()
        elif self.path == "/loop":
            self.send_response(302); self.send_header("Location", "/loop"); self.end_headers()  # noqa: E702

    def log_message(self, *a):
        pass


@pytest.fixture
def web():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_brokered_fetch_allowlist_dns_and_redirects(web):
    allow = lambda h: h in ("127.0.0.1", "good.test")  # noqa: E731
    f = BrokeredFetcher(allowed=allow, allow_private=True)
    assert f.get(f"{web}/ok") == (200, b"hi")
    with pytest.raises(SecurityPolicyViolationError, match="allowlist"):
        f.get(f"{web}/to-evil")                      # redirect to a host off the allowlist
    with pytest.raises(SecurityPolicyViolationError, match="redirects"):
        f.get(f"{web}/loop")
    with pytest.raises(SecurityPolicyViolationError):
        f.get("ftp://127.0.0.1/x")
    # DNS rebinding: an allowlisted name that resolves to a private address is refused.
    rebinding = BrokeredFetcher(allowed=allow, resolve=lambda h: ["10.0.0.5"])
    with pytest.raises(SecurityPolicyViolationError, match="non-public"):
        rebinding.get("http://good.test/x")
    # and a hop that re-resolves to private after a clean first hop is also caught
    answers = iter([["93.184.216.34"], ["169.254.169.254"]])
    seq = BrokeredFetcher(
        allowed=allow, resolve=lambda h: next(answers),
        open_url=lambda u: (302, {"Location": "http://good.test/meta"}, b"") if u.endswith("/a")
        else (200, {}, b"secret"),
    )
    with pytest.raises(SecurityPolicyViolationError, match="non-public"):
        seq.get("http://good.test/a")


# ---- backends ----------------------------------------------------------------------
def _down(argv):
    return subprocess.CompletedProcess(argv, 1, "", "cannot connect")


def _up(argv):
    return subprocess.CompletedProcess(argv, 0, "29.0\n", "")


def test_backend_selection_fails_closed(tmp_path):
    assert docker_available(_down)[0] is False
    with pytest.raises(SecurityPolicyViolationError, match="unavailable"):
        select_backend("docker", run=_down, storage_root=tmp_path)
    r = select_backend("docker", run=_up, storage_root=tmp_path)
    assert isinstance(r, DockerSandboxRunner)
    with pytest.raises(ValueError):
        select_backend("kubernetes", run=_up)
    # auto with docker down must not silently run unconfined on the host
    local = select_backend("auto", run=_down, storage_root=tmp_path, require_isolation=False)
    assert type(local) is LocalSandboxRunner
    r = DockerSandboxRunner(storage_root=tmp_path, run=_down)
    with pytest.raises(SecurityPolicyViolationError):
        r.create_task("d1", "repo", "main")


def test_docker_argv_is_locked_down(tmp_path):
    argv = build_docker_argv(name="n", image="img", workspace=tmp_path, command="pytest -q",
                             allow_network=False,
                             quota=ResourceQuota(memory_mb=512, max_processes=64))
    joined = " ".join(argv)
    for must in ("--network none", "--cap-drop ALL", "no-new-privileges", "--read-only",
                 "--memory 512m", "--pids-limit 64", "--user 65534:65534"):
        assert must in joined
    assert argv[-3:] == ["sh", "-c", "pytest -q"]
    assert "network" not in " ".join(build_docker_argv(
        name="n", image="i", workspace=tmp_path, command="x", allow_network=True, quota=None))
    # no host secrets leak into the container env
    assert "DEEPSEEK" not in joined and "AWS_" not in joined


# ---- guard internals pinned exactly (added after mutation analysis showed gaps) -----------
def test_group_stats_counts_only_the_requested_group():
    import os
    import signal

    from harness_sandbox.quota import group_stats

    p = subprocess.Popen(["sleep", "5"], start_new_session=True)
    try:
        count, rss = group_stats(os.getpgid(p.pid))
        assert count == 1 and rss > 0
        assert group_stats(2_000_000_000) == (0, 0)
    finally:
        os.kill(p.pid, signal.SIGKILL)
        p.wait()


def test_dir_size_is_exact(tmp_path):
    from harness_sandbox.quota import dir_size_bytes

    assert dir_size_bytes(tmp_path) == 0
    (tmp_path / "a").write_bytes(b"123")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "b").write_bytes(b"45")
    assert dir_size_bytes(tmp_path) == 5


async def _guard(monkeypatch, quota, ws, stats):
    import asyncio
    import os

    import harness_sandbox.quota as q

    class P:
        pid = os.getpid()
        returncode = None

    monkeypatch.setattr(q, "group_stats", lambda pgid: stats)
    guard = q.ResourceGuard(quota, ws, interval=0.01)
    task = asyncio.create_task(guard.watch(P, lambda p: setattr(p, "returncode", -9)))
    await asyncio.sleep(0.1)
    P.returncode = 0
    await task
    return guard.violation


async def test_guard_thresholds_are_strict_greater_than(monkeypatch, tmp_path):
    kib = 1024
    ok = await _guard(monkeypatch, ResourceQuota(memory_mb=100), tmp_path, (1, 100 * kib))
    assert ok is None                                     # exactly at the limit is allowed
    over = await _guard(monkeypatch, ResourceQuota(memory_mb=100), tmp_path, (1, 100 * kib + 1))
    assert over is not None and over.resource_name == "memory_mb"
    assert await _guard(monkeypatch, ResourceQuota(max_processes=3), tmp_path, (3, 0)) is None
    over = await _guard(monkeypatch, ResourceQuota(max_processes=3), tmp_path, (4, 0))
    assert over is not None and over.resource_name == "processes"


async def test_guard_disk_threshold_uses_mebibytes(monkeypatch, tmp_path):
    (tmp_path / "f").write_bytes(b"0" * (2 * 1024 * 1024))
    assert await _guard(monkeypatch, ResourceQuota(disk_mb=2), tmp_path, (1, 0)) is None
    (tmp_path / "g").write_bytes(b"0")
    over = await _guard(monkeypatch, ResourceQuota(disk_mb=2), tmp_path, (1, 0))
    assert over is not None and over.resource_name == "disk_mb"


def test_fetch_connects_to_the_validated_ip_not_a_second_dns_answer(web, monkeypatch):
    """TOCTOU rebinding: after the check, a fresh lookup must never be consulted."""
    import socket

    real = socket.getaddrinfo

    def boom(host, *a, **k):
        if host == "good.test":
            raise AssertionError("second DNS resolution of the name")
        return real(host, *a, **k)  # literal-IP connects are fine

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    f = BrokeredFetcher(allowed=lambda h: h == "good.test", resolve=lambda h: ["127.0.0.1"],
                        allow_private=True)
    port = web.rsplit(":", 1)[1]
    assert f.get(f"http://good.test:{port}/ok") == (200, b"hi")


def test_redirect_location_header_is_case_insensitive():
    hops = iter([(302, {"location": "http://evil.test/x"}, b""), (200, {}, b"x")])
    f = BrokeredFetcher(allowed=lambda h: h == "good.test", resolve=lambda h: ["93.184.216.34"],
                        open_url=lambda u: next(hops))
    with pytest.raises(SecurityPolicyViolationError, match="allowlist"):
        f.get("http://good.test/a")  # lowercase header must still be followed AND re-checked
