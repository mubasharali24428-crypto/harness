"""M07: the orchestrator gets its confined worker from the worker daemon, not from a socket.

The daemon runs in-process here with the stand-in worker and ``off`` confinement, so these
tests cover the protocol and the ownership rules - the container itself is covered by the
live compose drill (scripts/e2e/m07_daemon_drill.py).
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from harness_orchestrator import confine, worker_daemon
from harness_orchestrator.engine import DeepSeekHarnessWrapper

HERE = Path(__file__).resolve().parent
SECTION4 = HERE.parents[1]
TOKEN = "daemon-token-for-tests"
KEY = "sk-realistic-0123456789abcdef"


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_WORKER_MODULE", "standin_worker")
    monkeypatch.setenv("PYTHONPATH", f"{HERE}:{SECTION4}/src:{SECTION4.parent}/contracts/src")
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "off")
    monkeypatch.setenv("HARNESS_WORKER_DAEMON_TOKEN", TOKEN)
    monkeypatch.setenv("DSH_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("DSH_SESSION_ROOT", str(tmp_path / "sess"))
    server = worker_daemon.serve("127.0.0.1:0")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("HARNESS_WORKER_DAEMON_URL", url)
    yield url, tmp_path
    server.shutdown()


def _orchestrator(tmp_path):
    return DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"), session_root=str(tmp_path / "sess"),
        api_key=KEY, base_url="http://127.0.0.1:9/v1", model="deepseek-chat",
        live_mode="live", isolated=True,
    )


def test_a_run_goes_through_the_daemon_and_streams(daemon, monkeypatch):
    url, tmp_path = daemon
    built = []
    real = worker_daemon._wrapper
    monkeypatch.setattr(
        worker_daemon, "_wrapper", lambda sid, body: built.append(sid) or real(sid, body)
    )
    engine = _orchestrator(tmp_path)
    assert engine.daemon_url == url
    seen = []
    text, status, *_ = engine._run_live_events("progress", "run-d1", "m", on_progress=seen.append)
    assert (text, status) == ("streamed", "completed")
    assert seen == [{"phase": "call", "tool": "read", "callId": "c1"}]
    text, status, *_ = engine._run_live_events("write:out.txt", "run-d2", "m")
    assert status == "completed"
    assert (tmp_path / "ws" / "run-d2" / "out.txt").read_text() == "written by agent\n"
    assert built == ["run-d1", "run-d2"]  # the daemon launched both, not this process


def test_cancel_and_running_are_answered_by_the_daemon(daemon):
    _, tmp_path = daemon
    engine = _orchestrator(tmp_path)
    box = {}
    runner = threading.Thread(
        target=lambda: box.update(r=engine._run_live_events("slow", "run-slow", "m"))
    )
    runner.start()
    for _ in range(300):
        if engine.is_worker_running("run-slow"):
            break
        time.sleep(0.02)
    assert engine.is_worker_running("run-slow") is True
    assert engine.cancel("run-slow") is True
    runner.join(timeout=30)
    assert box["r"][1] == "cancelled"
    assert engine.is_worker_running("run-slow") is False


def test_every_call_needs_the_token(daemon):
    url, _ = daemon
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
        req = urllib.request.Request(f"{url}/runs/run-x", headers=headers)
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 401


def test_the_daemon_takes_no_paths_from_the_request(daemon):
    """Only the run id addresses a directory, and it is validated like the orchestrator's."""
    url, tmp_path = daemon
    body = json.dumps({"goal": "write:x.txt", "workspace_root": "/etc"}).encode()
    for path in ("/runs/..%2Fescape", "/runs/../x", "/runs/a/b"):
        req = urllib.request.Request(url + path, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 404
    engine = _orchestrator(tmp_path)
    assert engine._run_live_events("write:x.txt", "run-p", "m")[1] == "completed"
    assert (tmp_path / "ws" / "run-p" / "x.txt").exists()


def test_daemon_loss_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKER_DAEMON_URL", "http://127.0.0.1:9")
    engine = _orchestrator(tmp_path)
    text, status, *_ = engine._run_live_events("write:x.txt", "run-l", "m")
    assert status == "error" and "worker daemon" in text and KEY not in text
    assert engine.cancel("run-l") is False  # unreachable is not confirmation
    assert engine.is_worker_running("run-l") is True  # unknown is treated as alive


def test_daemon_refuses_to_start_without_a_token(monkeypatch):
    monkeypatch.delenv("HARNESS_WORKER_DAEMON_TOKEN", raising=False)
    assert worker_daemon.main() == 2


def test_runs_volume_mounts_replace_binds_under_the_runs_root(tmp_path, monkeypatch):
    # The subpath form is the FALLBACK: it is what a deployment gets when the
    # engine's own filesystem is not reachable from here. It is also the form
    # that made every container-mode run unwritable (Docker re-creates the
    # subpath as root:root at mount time), so it stays pinned as a fallback and
    # nothing more — see the bind test below.
    root = tmp_path.resolve()
    ws, sess = root / "workspace" / "run-v", root / "sessions" / "run-v"
    (ws / "src").mkdir(parents=True)
    sess.mkdir(parents=True)
    monkeypatch.setenv("HARNESS_RUNS_VOLUME", "harness_runs")
    monkeypatch.setenv("HARNESS_RUNS_ROOT", str(root))
    monkeypatch.setenv("HARNESS_RUNS_BIND", "0")
    monkeypatch.setattr(confine, "_MOUNTPOINT_CACHE", {})
    argv = confine.wrap_command(
        ["python", "-m", "w"], mode="container", roots=[], workspace=str(ws),
        session_dir=str(sess), tmp_dir=str(root), container_name="n", network="p",
        scope_paths=["src"],
    )
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    assert mounts == [
        f"type=volume,src=harness_runs,dst={ws},volume-subpath=workspace/run-v,readonly=true",
        f"type=volume,src=harness_runs,dst={sess},volume-subpath=sessions/run-v,readonly=false",
        f"type=volume,src=harness_runs,dst={ws / 'src'},"
        "volume-subpath=workspace/run-v/src,readonly=false",
    ]


def test_runs_volume_mounts_bind_the_volumes_own_directory_when_resolvable(
    tmp_path, monkeypatch
):
    """The preferred form keeps the ownership handover intact.

    A bind of `<Mountpoint>/workspace/<run>` points at the same bytes the volume
    holds, but Docker does not reset its ownership or mode — which is the only
    reason a worker running as 10001 can write inside a root-launched stack.
    """
    root = tmp_path.resolve()
    ws, sess = root / "workspace" / "run-b", root / "sessions" / "run-b"
    ws.mkdir(parents=True)
    sess.mkdir(parents=True)
    monkeypatch.setenv("HARNESS_RUNS_VOLUME", "harness_runs")
    monkeypatch.setenv("HARNESS_RUNS_ROOT", str(root))
    monkeypatch.delenv("HARNESS_RUNS_BIND", raising=False)
    monkeypatch.setattr(confine, "_MOUNTPOINT_CACHE", {"harness_runs": "/mnt/harness_runs"})
    argv = confine.wrap_command(
        ["python", "-m", "w"], mode="container", roots=[], workspace=str(ws),
        session_dir=str(sess), tmp_dir=str(root), container_name="n", network="p",
    )
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    assert mounts == [
        f"type=bind,src=/mnt/harness_runs/workspace/run-b,dst={ws},readonly=false",
        f"type=bind,src=/mnt/harness_runs/sessions/run-b,dst={sess},readonly=false",
    ]
    assert not any("volume-subpath" in m for m in mounts)


def test_an_unresolvable_volume_mountpoint_falls_back_instead_of_failing(monkeypatch):
    monkeypatch.setenv("HARNESS_RUNS_VOLUME", "absent-volume")
    monkeypatch.delenv("HARNESS_RUNS_BIND", raising=False)
    monkeypatch.setattr(confine, "_MOUNTPOINT_CACHE", {})
    monkeypatch.setattr(
        confine.subprocess,
        "run",
        lambda *a, **k: confine.subprocess.CompletedProcess(list(a), 1, stdout="", stderr="no"),
    )
    assert confine.volume_mountpoint("absent-volume") is None
    # ... and the answer is cached: one Docker failure is not one call per mount.
    def explode(*args, **kwargs):
        raise AssertionError("volume_mountpoint must not ask Docker twice")

    monkeypatch.setattr(confine.subprocess, "run", explode)
    assert confine.volume_mountpoint("absent-volume") is None


def test_a_root_launcher_runs_the_worker_as_the_workspace_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 0)
    st = tmp_path.stat()
    assert confine._worker_user(str(tmp_path)) == f"{st.st_uid}:{st.st_gid}"
    monkeypatch.setattr(os, "stat", lambda p: os.stat_result((0,) * 10))
    assert confine._worker_user(str(tmp_path)) == "10001:10001"  # never root


def test_sweep_takes_settlement_from_the_caller_but_never_a_held_run(daemon, monkeypatch):
    """W22: a SIGKILLed daemon's worker is stopped only on the orchestrator's settled proof."""
    from harness_orchestrator import container_runtime

    url, _ = daemon
    seen = {}
    monkeypatch.setattr(
        container_runtime, "labelled_boundary_runs", lambda: {"harness-a-net": "run-a"}
    )
    monkeypatch.setattr(
        container_runtime,
        "sweep_orphan_boundaries",
        lambda held, settled, **kw: seen.update(held=held, settled=settled, **kw) or {"count": 0},
    )

    def call(method, path, body=None, token=TOKEN):
        request = urllib.request.Request(
            url + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=5) as reply:
            return json.loads(reply.read())

    worker_daemon._RUNS["run-held"] = None
    try:
        assert call("GET", "/boundaries") == {"runs": ["run-a"]}
        call("POST", "/sweep", {"settledRunIds": ["run-a", "run-held"], "olderThanSeconds": 300})
        assert seen["settled"] == {"run-a"}, "a run this daemon still holds is never settled"
        assert "run-held" in seen["held"] and seen["older_than_seconds"] == 300
        with pytest.raises(urllib.error.HTTPError) as denied:
            call("POST", "/sweep", {"settledRunIds": [], "olderThanSeconds": 0}, token="wrong")
        assert denied.value.code == 401
    finally:
        worker_daemon._RUNS.pop("run-held", None)
