"""P0 launch policy and truthful cancellation regression tests (no daemon needed)."""

import subprocess
from unittest.mock import Mock

import pytest

from harness_orchestrator import confine, container_runtime
from harness_orchestrator.engine import DeepSeekHarnessWrapper


def test_container_argv(tmp_path):
    ws, session = tmp_path / "workspace", tmp_path / "session"
    ws.mkdir()
    session.mkdir()
    argv = confine.wrap_command(
        ["/host/venv/python", "-m", "harness_orchestrator.engine_worker"],
        mode="container",
        roots=[str(tmp_path)],
        workspace=str(ws),
        session_dir=str(session),
        tmp_dir=str(tmp_path),
        container_name="test-worker",
        network="private",
        env={"DEEPSEEK_API_KEY": "secret"},
    )
    assert argv[:4] == ["docker", "run", "--rm", "--interactive"]
    assert argv.count("--mount") == 2
    assert "secret" not in " ".join(argv)
    assert "/host/venv/python" not in argv
    for flag, value in [
        ("--network", "private"),
        ("--pids-limit", "512"),
        ("--memory", "1g"),
        ("--cpus", "1"),
        ("--security-opt", "no-new-privileges"),
    ]:
        assert argv[argv.index(flag) + 1] == value
    assert "--read-only" in argv and "--cap-drop=ALL" in argv
    assert argv[argv.index("--user") + 1].split(":")[0] != "0"
    assert argv[argv.index("--tmpfs") + 1].startswith("/tmp:rw,exec,nosuid,nodev,")


def test_container_rejects_missing_boundary_and_symlink(tmp_path):
    args = dict(
        mode="container",
        roots=[],
        workspace=str(tmp_path),
        session_dir=str(tmp_path),
        tmp_dir=str(tmp_path),
    )
    with pytest.raises(confine.ConfinementUnavailableError):
        confine.wrap_command(["python"], **args)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    args["workspace"] = str(link)
    with pytest.raises(ValueError):
        confine.wrap_command(["python"], container_name="x", network="y", **args)


@pytest.mark.parametrize("endpoint", ["file:///etc/passwd", "http://u:p@host", "http://"])
def test_endpoint_rejected(endpoint):
    with pytest.raises(ValueError):
        container_runtime.ContainerBoundary(endpoint)


def test_private_network_and_fixed_relay(monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append(a)
        return subprocess.CompletedProcess(a, 0, "", "")

    monkeypatch.setattr(container_runtime, "docker", fake)
    boundary = container_runtime.ContainerBoundary("http://127.0.0.1:11434/v1")
    assert boundary.start() == "http://model:8080/v1"
    assert calls[0] == ("network", "create", "--internal", boundary.network)
    # W4: the proxy joins the run network as the only peer with a route out.
    assert calls[1][:4] == (
        "network", "connect", "--alias", container_runtime.EGRESS_PROXY_ALIAS
    )
    assert calls[1][-1] == "harness-sandbox-egress-1"
    assert boundary.web_proxy_url == "http://harness-egress:3129"
    create = calls[2]
    assert "host.docker.internal" in create
    assert create[-3:] == ("http", "host.docker.internal", "11434")
    assert not any("--publish" in call for call in calls)


def test_missing_egress_proxy_leaves_web_fetch_without_a_route(monkeypatch):
    def fake(*a, **k):
        if a[:2] == ("network", "connect"):
            return subprocess.CompletedProcess(a, 1, "", "no such container")
        return subprocess.CompletedProcess(a, 0, "", "")

    monkeypatch.setattr(container_runtime, "docker", fake)
    boundary = container_runtime.ContainerBoundary("http://127.0.0.1:11434/v1")
    assert boundary.start() == "http://model:8080/v1"
    assert boundary.web_proxy_url is None  # denial still works; an allowed fetch fails closed


def test_cancel_does_not_confirm_before_launcher_exits(monkeypatch):
    calls = []
    proc = Mock()
    proc.poll.side_effect = [None, 0]
    monkeypatch.setattr(container_runtime.time, "sleep", lambda _: None)

    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(container_runtime, "docker", run)
    boundary = container_runtime.ContainerBoundary("http://localhost:1234")
    assert boundary.kill(proc)
    assert sum(c[0] == "rm" for c in calls) == 2


def test_cancel_uses_container_confirmation(monkeypatch):
    wrapper = DeepSeekHarnessWrapper(live_mode="dry")
    boundary = Mock()
    boundary.kill.return_value = False
    wrapper._containers["run"] = boundary
    wrapper._procs["run"] = Mock()
    monkeypatch.setattr("os.killpg", lambda *a: pytest.fail("container must not use killpg"))
    assert wrapper.cancel("run") is False
    boundary.kill.return_value = True
    assert wrapper.cancel("run") is True


def test_launcher_exit_does_not_hide_unconfirmed_container():
    wrapper = DeepSeekHarnessWrapper(live_mode="dry")
    proc, boundary = Mock(), Mock()
    proc.poll.return_value = 1
    boundary.confirmed_gone.return_value = False
    wrapper._procs["run"] = proc
    wrapper._containers["run"] = boundary
    assert wrapper.is_worker_running("run")
    boundary.confirmed_gone.return_value = True
    assert not wrapper.is_worker_running("run")


def test_daemon_loss_never_confirms_stop(monkeypatch):
    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired("docker", 30)

    monkeypatch.setattr(container_runtime, "docker", unavailable)
    boundary = container_runtime.ContainerBoundary("http://localhost:1234")
    assert not boundary.kill()
    assert not boundary.confirmed_gone()
    boundary.close()


def test_missing_daemon_fails_closed_without_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "container")
    monkeypatch.delenv("HARNESS_ENGINE_WORKER_MODULE", raising=False)

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("docker unavailable")

    monkeypatch.setattr(container_runtime, "docker", unavailable)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "runs"),
        session_root=str(tmp_path / "sessions"),
        isolated=True,
        live_mode="dry",
    )
    result = wrapper._run_live_events("hello", "run", "m")
    assert result[1] == "error" and "unavailable" in result[0]


def test_close_disconnects_the_egress_proxy_before_removing_the_network(monkeypatch):
    """A run network with an attached container cannot be deleted, and W4 attaches the
    shared egress proxy to it on purpose — so the proxy must leave first. Getting this
    wrong leaked one network per run until Docker's whole address pool was exhausted
    and no run could start at all."""
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args, check: bool = True):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")

    monkeypatch.setattr(container_runtime, "docker", fake_docker)
    monkeypatch.setenv(container_runtime.EGRESS_PROXY_CONTAINER_ENV, "harness-sandbox-egress-1")
    boundary = container_runtime.ContainerBoundary("https://api.example.test")
    url = boundary._attach_egress_proxy()
    assert url and boundary.egress_proxy == "harness-sandbox-egress-1"

    boundary.close()
    sequence = [c for c in calls if c[0] == "network"]
    assert sequence == [
        ("network", "connect", "--alias", container_runtime.EGRESS_PROXY_ALIAS,
         boundary.network, "harness-sandbox-egress-1"),
        ("network", "disconnect", boundary.network, "harness-sandbox-egress-1"),
        ("network", "rm", boundary.network),
    ], sequence


def test_a_run_without_an_attached_proxy_closes_without_a_disconnect(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args, check: bool = True):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(list(args), 1, stdout="", stderr="no such container")

    monkeypatch.setattr(container_runtime, "docker", fake_docker)
    boundary = container_runtime.ContainerBoundary("https://api.example.test")
    assert boundary._attach_egress_proxy() is None  # bare local dev: no proxy to bound
    boundary.close()
    assert not [c for c in calls if c[0] == "network" and c[1] == "disconnect"]
    assert ("network", "rm", boundary.network) in calls
