"""Phase 4: no silent unconfined fallback."""

import pytest

from harness_sandbox.docker_runner import DockerSandboxRunner
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.policy import SecurityPolicyViolationError


def test_explicit_off_requires_env_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_SANDBOX_ALLOW_UNCONFINED", raising=False)
    runner = LocalSandboxRunner(storage_root=tmp_path, confinement="off")
    with pytest.raises(SecurityPolicyViolationError, match="HARNESS_SANDBOX_ALLOW_UNCONFINED"):
        runner._resolve_confinement()


def test_docker_runner_selects_container_boundary_without_host_fallback(tmp_path):
    runner = DockerSandboxRunner(storage_root=tmp_path / "tasks", client=object())
    assert runner._resolve_confinement_once() == "container"
