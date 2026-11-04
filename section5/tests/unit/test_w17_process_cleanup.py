"""W17: a process-group member must not outlive its session, however the leader ended.

The leader is the language server (or browser). Real servers fork workers (node,
renderers) into the same process group. If the leader exits or crashes first, a
kill guarded by "leader still running" never reaches them.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from harness_sandbox.lsp_client import LspError, LspSession

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "lsp_fixture_server.py"
# Fork a long-lived worker into our process group, record its pid, then become the fixture server.
LAUNCHER = (
    "import os, subprocess, sys; "
    "p = subprocess.Popen(['sleep', '300']); "
    "open(sys.argv[1], 'w').write(str(p.pid)); "
    "os.execv(sys.executable, [sys.executable, '-I', sys.argv[2]])"
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it is dead for our purposes.
    stat = os.popen(f"ps -o stat= -p {pid}").read().strip()
    return bool(stat) and not stat.startswith("Z")


def _wait_dead(pid: int, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


@pytest.fixture()
def worker_pid_file(tmp_path):
    return tmp_path / "worker.pid"


def _session(tmp_path, pid_file) -> LspSession:
    return LspSession([sys.executable, "-c", LAUNCHER, str(pid_file), str(FIXTURE)],
                      cwd=tmp_path, timeout=5.0)


def _worker(pid_file) -> int:
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            return int(pid_file.read_text())
        time.sleep(0.05)
    raise AssertionError("launcher never recorded the worker pid")


def test_graceful_shutdown_does_not_leave_a_group_member_behind(tmp_path, worker_pid_file):
    session = _session(tmp_path, worker_pid_file)
    session.initialize()
    worker = _worker(worker_pid_file)
    try:
        session.close()
        assert session.returncode is not None
        assert _wait_dead(worker), "worker forked by the server survived a clean close()"
    finally:
        if _alive(worker):
            os.kill(worker, signal.SIGKILL)


def test_crashed_leader_does_not_leave_a_group_member_behind(tmp_path, worker_pid_file):
    session = _session(tmp_path, worker_pid_file)
    session.initialize()
    worker = _worker(worker_pid_file)
    try:
        os.kill(session.pid, signal.SIGKILL)  # the server crashes; its worker is orphaned
        with pytest.raises((LspError, OSError)):
            session.request("test/cwd", {})
        session.close()
        assert _wait_dead(worker), "worker survived after its leader crashed and the session closed"
    finally:
        if _alive(worker):
            os.kill(worker, signal.SIGKILL)


def _browser_module():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "src" / "harness_sandbox" / "browser_session.py"
    spec = importlib.util.spec_from_file_location("w17_browser_session", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["w17_browser_session"] = module
    spec.loader.exec_module(module)
    return module


def test_browser_close_reaps_helpers_left_after_the_main_process_exits(tmp_path):
    import subprocess

    browser = _browser_module()
    session = browser.BrowserSession(str(tmp_path), chromium="/bin/true")
    session._tmp = str(tmp_path / "profile-dir")
    os.makedirs(session._tmp)
    pid_file = tmp_path / "helper.pid"
    # Main process forks a helper into its own session/group, records it, and exits at once.
    session._proc = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys; "
         "p = subprocess.Popen(['sleep', '300']); open(sys.argv[1], 'w').write(str(p.pid))",
         str(pid_file)],
        start_new_session=True,
    )
    helper = _worker(pid_file)
    session._proc.wait()  # leader is gone; helper keeps the group alive
    try:
        session.close()
        assert _wait_dead(helper), "helper outlived BrowserSession.close()"
        assert not Path(session._tmp).exists()
    finally:
        if _alive(helper):
            os.kill(helper, signal.SIGKILL)


def test_one_shot_browser_run_reaps_helpers_that_detached_from_its_pipes(tmp_path):
    from harness_sandbox import browser_gate

    pid_file = tmp_path / "gate-helper.pid"
    code = ("import subprocess, sys; "
            "p = subprocess.Popen(['sleep', '300'], stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL); open(sys.argv[1], 'w').write(str(p.pid))")
    exit_code, _out, _err, timed_out = browser_gate._run_chromium(
        [sys.executable, "-c", code, str(pid_file)], timeout=10, proxy=None)
    helper = _worker(pid_file)
    try:
        assert (exit_code, timed_out) == (0, False)
        assert _wait_dead(helper), "helper outlived a completed one-shot browser run"
    finally:
        if _alive(helper):
            os.kill(helper, signal.SIGKILL)
