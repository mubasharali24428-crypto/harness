"""M18-C9 (E2E-found): Landlock selection must PROBE, not assume.

The old ``landlock_supported()`` was a kernel-version heuristic. Inside the
shipped compose image (Docker default seccomp blocks ``landlock_add_rule``)
the version says 6.x, 'auto' resolved to 'landlock', and every sandbox exec
died in ``preexec_fn`` with ``OSError: landlock_add_rule failed`` — a 500 on
``POST /tasks/{id}/exec`` on real deployments while every test lane stayed
green (they ran on macOS/bare-metal Linux where the syscalls work).

These tests simulate the Linux host hermetically.  Landlock remains available
for the narrower write-boundary probe, but Option B deliberately refuses all
Linux pilot execution because forbidden-path reads are not yet denied.
"""

import sys

import pytest

from harness_sandbox import confinement
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.policy import SecurityPolicyViolationError


@pytest.fixture
def linux_env(monkeypatch):
    """Simulate a Linux 6.x host regardless of the machine running tests."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(confinement, "platform_release", lambda: "6.8.0-generic")
    monkeypatch.setattr(confinement, "_LANDLOCK_PROBE", None)
    yield


def test_auto_fails_closed_when_probe_fails(tmp_path, monkeypatch, linux_env):
    monkeypatch.setattr(confinement, "_probe_landlock_syscalls", lambda timeout=10.0: False)
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks")
    with pytest.raises(SecurityPolicyViolationError, match="Linux sandbox execution is disabled"):
        runner._resolve_confinement()


def test_explicit_landlock_fails_closed_when_probe_fails(tmp_path, monkeypatch, linux_env):
    monkeypatch.setattr(confinement, "_probe_landlock_syscalls", lambda timeout=10.0: False)
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement="landlock")
    with pytest.raises(SecurityPolicyViolationError):
        runner.create_task("t1", "o/r", "m")


def test_auto_picks_landlock_when_probe_passes(tmp_path, monkeypatch, linux_env):
    monkeypatch.setattr(confinement, "_probe_landlock_syscalls", lambda timeout=10.0: True)
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks")
    with pytest.raises(SecurityPolicyViolationError, match="forbidden paths"):
        runner._resolve_confinement()


def test_probe_runs_at_most_once_per_process(monkeypatch, linux_env):
    calls = {"n": 0}

    def spy(timeout=10.0):
        calls["n"] += 1
        return True

    monkeypatch.setattr(confinement, "_probe_landlock_syscalls", spy)
    assert confinement.landlock_supported() is True
    assert confinement.landlock_supported() is True
    assert calls["n"] == 1


def test_linux_auto_does_not_claim_read_confinement(tmp_path, linux_env):
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks")
    with pytest.raises(SecurityPolicyViolationError, match="does not deny reads"):
        runner._resolve_confinement()
