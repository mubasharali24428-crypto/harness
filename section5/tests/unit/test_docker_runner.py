from __future__ import annotations

from types import SimpleNamespace

import pytest
from docker.errors import DockerException

from harness_sandbox.docker_runner import DockerSandboxRunner
from harness_sandbox.policy import SecurityPolicyViolationError


class FakeContainer:
    def __init__(self) -> None:
        self.id = "container-123"
        self.removed = False
        self.exec_calls: list[dict] = []

    def exec_run(self, **kwargs):
        self.exec_calls.append(kwargs)
        output = b"hello\n"
        if kwargs["stream"]:
            output = iter([output])
        return SimpleNamespace(exit_code=0, output=output)

    def remove(self, *, force: bool):
        assert force is True
        self.removed = True


class FakeImages:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def get(self, image: str):
        if self.error:
            raise self.error
        return SimpleNamespace(tags=[image])


class FakeContainers:
    def __init__(self) -> None:
        self.last: FakeContainer | None = None
        self.kwargs: dict | None = None

    def run(self, image: str, **kwargs):
        self.kwargs = {"image": image, **kwargs}
        self.last = FakeContainer()
        return self.last


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.images = FakeImages(error)
        self.containers = FakeContainers()


def test_create_task_applies_container_boundary_and_masks_forbidden_paths(tmp_path, monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("HARNESS_DATABASE_URL", "postgresql://secret")
    runner = DockerSandboxRunner(
        storage_root=tmp_path / "tasks",
        image="worker:test",
        allow_network=False,
        client=client,
    )
    handle = runner.create_task(
        "t1", "org/repo", "main", forbidden_paths=["payroll"], team_id="team-a"
    )

    assert handle.containerId == "container-123"
    kwargs = client.containers.kwargs
    assert kwargs is not None
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["network_mode"] == "none"
    assert kwargs["pids_limit"] == 128
    assert kwargs["tmpfs"]["/workspace/payroll"].startswith("ro,")
    assert "/workspace" in {mount["bind"] for mount in kwargs["volumes"].values()}
    assert "HARNESS_DATABASE_URL" not in kwargs["environment"]

    runner.destroy(handle)
    assert client.containers.last is not None
    assert client.containers.last.removed is True
    assert "t1" not in runner.active_tasks


@pytest.mark.asyncio
async def test_exec_uses_container_workdir_and_sanitized_command(tmp_path):
    client = FakeClient()
    runner = DockerSandboxRunner(storage_root=tmp_path / "tasks", client=client)
    handle = runner.create_task("t1", "org/repo", "main")

    assert client.containers.kwargs is not None
    assert client.containers.kwargs["network_mode"] == "none"

    result = await runner.exec_structured(handle, "pwd")

    assert result.status == "passed"
    assert result.exit_code == 0
    call = client.containers.last.exec_calls[0]
    assert call["workdir"] == "/workspace"
    assert call["cmd"][0:2] == ["/bin/sh", "-lc"]
    assert "timeout" in call["cmd"][2]
    runner.destroy(handle)


def test_container_unavailability_fails_closed_and_cleans_workspace(tmp_path):
    runner = DockerSandboxRunner(
        storage_root=tmp_path / "tasks",
        client=FakeClient(DockerException("image unavailable")),
    )

    with pytest.raises(SecurityPolicyViolationError, match="unable to start Docker sandbox"):
        runner.create_task("t1", "org/repo", "main")

    assert not (tmp_path / "tasks" / "t1").exists()
    assert not runner.active_tasks


def test_each_existing_forbidden_file_gets_a_distinct_mask(tmp_path):
    workspace = tmp_path / "workspace"
    tmpdir = tmp_path / "tmp"
    workspace.mkdir()
    tmpdir.mkdir()
    (workspace / "a.txt").write_text("a")
    (workspace / "b.txt").write_text("b")
    runner = DockerSandboxRunner(storage_root=tmp_path / "tasks", client=FakeClient())

    volumes, tmpfs = runner._forbidden_mounts(
        workspace, tmpdir, ["a.txt", "b.txt", "future-dir"]
    )

    assert len(volumes) == 2
    assert {mount["bind"] for mount in volumes.values()} == {
        "/workspace/a.txt",
        "/workspace/b.txt",
    }
    assert "/workspace/future-dir" in tmpfs


def test_allow_network_without_proxy_config_fails_closed(tmp_path, monkeypatch):
    from harness_sandbox.config import config

    monkeypatch.setattr(config, "egress_network", "")
    monkeypatch.setattr(config, "egress_proxy_url", "")
    runner = DockerSandboxRunner(
        storage_root=tmp_path / "tasks", image="w:t", allow_network=True, client=FakeClient()
    )
    with pytest.raises(SecurityPolicyViolationError):
        runner.create_task("t1", "org/repo", "main")


def test_allow_network_uses_internal_egress_network_and_proxy(tmp_path, monkeypatch):
    from harness_sandbox.config import config

    monkeypatch.setattr(config, "egress_network", "harness_sandbox_egress")
    monkeypatch.setattr(config, "egress_proxy_url", "http://sandbox-egress:3128")
    client = FakeClient()
    runner = DockerSandboxRunner(
        storage_root=tmp_path / "tasks", image="w:t", allow_network=True, client=client
    )
    runner.create_task("t1", "org/repo", "main")
    kwargs = client.containers.kwargs
    assert kwargs["network_mode"] == "harness_sandbox_egress"
    assert kwargs["environment"]["HTTPS_PROXY"] == "http://sandbox-egress:3128"


@pytest.mark.asyncio
async def test_egress_proxy_denies_unlisted_hosts_and_non_connect():
    import asyncio

    from harness_sandbox.egress_proxy import handle

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def ask(line: bytes) -> bytes:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(line + b"\r\n\r\n")
        data = await r.read(100)
        w.close()
        return data

    assert (await ask(b"CONNECT evil.example:443 HTTP/1.1")).startswith(b"HTTP/1.1 403")
    assert (await ask(b"CONNECT pypi.org:22 HTTP/1.1")).startswith(b"HTTP/1.1 403")
    assert (await ask(b"GET http://pypi.org/ HTTP/1.1")).startswith(b"HTTP/1.1 405")
    server.close()
