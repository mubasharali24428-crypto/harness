"""Opt-in P0 live evidence. Build worker and isolation-tests targets first.

HARNESS_P0_DOCKER=1 section4/.venv/bin/python -m pytest \
    section4/tests/integration/test_container_p0.py -v
Add HARNESS_P0_LIVE=1 for real Ollama hello-world (no stored credentials).
"""

import asyncio
import concurrent.futures
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from harness_orchestrator import confine
from harness_orchestrator.container_runtime import ContainerBoundary
from harness_orchestrator.engine import DeepSeekHarnessWrapper

pytestmark = pytest.mark.skipif(os.getenv("HARNESS_P0_DOCKER") != "1", reason="opt-in Docker")


@pytest.fixture
def boundary(tmp_path):
    b = ContainerBoundary("http://127.0.0.1:11434/v1")
    b.start()
    ws, session = tmp_path / "ws", tmp_path / "session"
    ws.mkdir()
    session.mkdir()
    try:
        yield b, ws, session
    finally:
        b.kill()
        b.close()


def probe(boundary, code):
    b, ws, session = boundary
    argv = confine.wrap_command(
        ["python", "-c", code],
        mode="container",
        roots=[str(ws.parent)],
        workspace=str(ws),
        session_dir=str(session),
        tmp_dir=str(ws.parent),
        container_name=b.name,
        network=b.network,
        image=b.image,
    )
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    "code",
    [
        "import os; assert os.getuid() != 0",
        "from pathlib import Path; Path('own').write_text('ok')",
        "from pathlib import Path; Path('/tmp/own').write_text('ok')",
        "open('/root-denied','w')",  # expected denial below
        "open('/etc/denied','w')",
        "open('/var/denied','w')",
        "import os; assert not os.path.exists('/var/run/docker.sock')",
        # Operator env must not leak through the boundary (AWS credentials,
        # HARNESS_ operator config). Exempt only the four HARNESS_*_GATE paths
        # the worker image itself bakes in (section4/worker.Dockerfile) and
        # engine.py reads — image contract, not leakage.
        "import os; assert not any("
        "k.startswith('AWS_') or (k.startswith('HARNESS_') and k not in "
        "{'HARNESS_SHELL_GATE','HARNESS_WEB_GATE','HARNESS_BROWSER_GATE',"
        "'HARNESS_IMAGE_GATE'}) for k in os.environ)",
        "s=open('/proc/self/status').read(); assert 'CapEff:\\t0000000000000000' in s",
        "s=open('/proc/self/status').read(); assert 'NoNewPrivs:\\t1' in s",
        "import socket; s=socket.socket(); s.settimeout(1); s.connect(('1.1.1.1',443))",
        "import socket; s=socket.socket(); s.settimeout(1); "
        "s.connect(('host.docker.internal',11434))",
    ],
)
def test_kernel_boundary(boundary, code):
    if code.startswith("open(") or "s.connect" in code:
        code = "try:\n " + code + "\nexcept OSError:\n pass\nelse:\n raise AssertionError('escape')"
    probe(boundary, code)


@pytest.fixture
def wrapper(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "container")
    monkeypatch.setenv("HARNESS_ENGINE_IMAGE", "harness-engine-worker:p0-tests")
    monkeypatch.setenv("HARNESS_ENGINE_WORKER_MODULE", "standin_worker")
    return DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "runs"),
        session_root=str(tmp_path / "sessions"),
        live_mode="dry",
        isolated=True,
    )


def test_two_concurrent_runs(wrapper):
    for sid in ("run-a", "run-b"):
        ws = Path(wrapper.run_workspace(sid))
        ws.mkdir(parents=True)
        (ws / "secret.txt").write_text(sid)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda pair: wrapper._run_live_events("probe:" + pair[1], pair[0], "m"),
                [("run-a", "run-b"), ("run-b", "run-a")],
            )
        )
    assert all(r[1] == "completed" for r in results), results
    for result in results:
        data = json.loads(result[0])
        assert data["write_own"] == "ALLOWED"
        assert all(data[k].startswith("DENIED") for k in ("read_other", "write_other"))
    for sid in ("run-a", "run-b"):
        ws = Path(wrapper.run_workspace(sid))
        assert (ws / "secret.txt").read_text() == sid
        assert not (ws / "pwn.txt").exists()


def test_sibling_read_write_and_session_hidden(wrapper):
    other = Path(wrapper.run_workspace("run-other"))
    other.mkdir(parents=True)
    (other / "secret.txt").write_text("secret")
    result = wrapper._run_live_events("probe", "run-a", "m")
    assert result[1] == "completed", result
    data = json.loads(result[0])
    assert data["write_own"] == "ALLOWED"
    assert all(data[k].startswith("DENIED") for k in ("read_other", "write_other", "write_outside"))
    assert not (other / "pwn.txt").exists()


def test_stop_kills_container_and_children(wrapper, monkeypatch):
    from unittest.mock import AsyncMock

    from harness_orchestrator import api
    from harness_orchestrator.service import OrchestratorService

    service = OrchestratorService(engine=wrapper)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(wrapper._run_live_events("slow", "run-stop", "m"))
    )
    thread.start()
    try:
        pidfile = Path(wrapper.run_workspace("run-stop")) / "child.pid"
        deadline = time.monotonic() + 45
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pidfile.exists(), result
        name = wrapper._containers["run-stop"].name
        assert not service.engine_quiesced("run-stop")
        monkeypatch.setattr(api, "_service", service)
        monkeypatch.setattr(api, "emit_run_lifecycle", AsyncMock())
        stop = service.stop_agent
        monkeypatch.setattr(service, "stop_agent", AsyncMock())
        # A pending/unconfirmed kill must remain stopping in the actual response builder.
        assert asyncio.run(api.stop_agent("run-stop", None))["status"] == "stopping"
        monkeypatch.setattr(service, "stop_agent", stop)
        run = asyncio.run(service._ensure_run("run-stop"))
        asyncio.run(service._ensure_baseline(run))
        assert asyncio.run(api.stop_agent("run-stop", None))["status"] == "stopped"
        thread.join(15)
        assert not thread.is_alive() and result[0][1] == "cancelled"
        gone = subprocess.run(["docker", "inspect", name], capture_output=True)
        assert gone.returncode != 0
        assert service.engine_quiesced("run-stop")  # API may now report stopped
        assert wrapper.cancel("run-stop")
    finally:
        wrapper.cancel("run-stop")
        thread.join(15)


def test_model_relay_only(boundary):
    probe(
        boundary,
        "import urllib.request; "
        "r=urllib.request.urlopen('http://model:8080/api/tags', timeout=10); "
        "assert r.status == 200",
    )
    # Test the numeric host address as well: a DNS failure alone is not egress isolation.
    relay = boundary[0].relay
    ip = subprocess.check_output(
        [
            "docker",
            "exec",
            relay,
            "python",
            "-c",
            "import socket; print(socket.gethostbyname('host.docker.internal'))",
        ],
        text=True,
    ).strip()
    probe(
        boundary,
        "import socket; s=socket.socket(); s.settimeout(1)\n"
        f"try: s.connect(({ip!r},11434))\n"
        "except OSError: pass\nelse: raise AssertionError('direct host egress')",
    )


def test_real_gate_stays_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ENGINE_WORKER_MODULE", raising=False)
    for mode in ("auto", "required", "seatbelt"):
        monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", mode)
        wrapper = DeepSeekHarnessWrapper(
            workspace_dir=str(tmp_path), live_mode="dry", isolated=True
        )
        result = wrapper._run_live_events("hello", "run", "m")
        assert result[1] == "error" and "=off" in result[0]


@pytest.mark.skipif(os.getenv("HARNESS_P0_LIVE") != "1", reason="opt-in real local model")
def test_real_hello_world(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "container")
    monkeypatch.delenv("HARNESS_ENGINE_WORKER_MODULE", raising=False)
    monkeypatch.delenv("HARNESS_ENGINE_IMAGE", raising=False)
    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "runs"),
        session_root=str(tmp_path / "sessions"),
        api_key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        live_mode="dry",
        isolated=True,
    )
    result = wrapper._run_live_events(
        "Create hello.txt containing exactly 'task0 ok' using your shell tool, "
        "then read it back. Do not merely describe the command.",
        "hello",
        "qwen3.5:9b",
    )
    assert result[1] == "completed", result[:3]
    assert (Path(wrapper.run_workspace("hello")) / "hello.txt").read_text().strip() == "task0 ok"
