"""S3: sandbox backend selection that fails closed, and a real Docker runner."""
from __future__ import annotations

import itertools
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from harness_sandbox.config import config
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.policy import SecurityPolicyViolationError, sanitize_environment
from harness_sandbox.quota import ResourceQuota

Run = Callable[[list[str]], subprocess.CompletedProcess]


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=10)


def docker_available(run: Run = _run) -> tuple[bool, str]:
    try:
        r = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"docker not usable: {e}"
    if r.returncode != 0:
        return False, "docker daemon unreachable"
    return True, r.stdout.strip()


def build_docker_argv(
    *, name: str, image: str, workspace: Path, command: str, allow_network: bool,
    quota: ResourceQuota | None,
) -> list[str]:
    q = quota or ResourceQuota()
    argv = [
        "docker", "run", "--rm", "--init", "--name", name,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
        "--read-only", "--tmpfs", "/tmp:rw,size=64m",  # noqa: S108 - container tmpfs
        "-v", f"{workspace}:/workspace:rw", "-w", "/workspace",
        "--memory", f"{q.memory_mb or config.max_memory_mb}m",
        "--cpus", str(config.max_cpus),
        "--pids-limit", str(q.max_processes or 256),
    ]
    if not allow_network:
        argv += ["--network", "none"]
    if q.cpu_seconds:
        argv += ["--ulimit", f"cpu={q.cpu_seconds}"]
    if q.max_file_mb:
        argv += ["--ulimit", f"fsize={q.max_file_mb * 1024 * 1024}"]
    for k, v in sanitize_environment({"PATH": "/usr/local/bin:/usr/bin:/bin"}).items():
        argv += ["-e", f"{k}={v}"]
    return [*argv, image, "sh", "-c", command]


class DockerSandboxRunner(LocalSandboxRunner):
    """Ephemeral container per command. Fails closed when the daemon is unavailable;
    never silently degrades to host execution."""

    def __init__(self, *args, image: str | None = None, run: Run = _run, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.image = image or config.default_docker_image
        self._run = run
        self._seq = itertools.count()

    def create_task(self, *args, **kwargs):
        ok, detail = docker_available(self._run)
        if not ok:
            raise SecurityPolicyViolationError(f"docker backend unavailable: {detail}")
        return super().create_task(*args, **kwargs)

    async def _spawn(self, task: dict, command: str, stdout, stderr):
        import asyncio

        name = f"harness-{os.getpid()}-{next(self._seq)}"
        argv = build_docker_argv(
            name=name, image=self.image, workspace=Path(task["workspace"]),
            command=command, allow_network=self.allow_network, quota=self.quota,
        )
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=stdout, stderr=stderr, start_new_session=True
        )
        proc.container_name = name  # type: ignore[attr-defined]
        return proc

    def _kill(self, proc) -> None:  # killing the docker CLI does not stop the container
        name = getattr(proc, "container_name", None)
        if name:
            try:
                self._run(["docker", "kill", name])
            except (OSError, subprocess.SubprocessError):
                pass
        LocalSandboxRunner._kill(proc)


def select_backend(
    requested: str, *, require_isolation: bool = True, run: Run = _run, **runner_kwargs
) -> LocalSandboxRunner:
    """requested: 'docker' | 'local' | 'auto'.

    'auto' prefers docker when the daemon is up. If isolation is required and the
    chosen backend cannot provide it, raise instead of degrading to the host.
    """
    if requested not in ("docker", "local", "auto"):
        raise ValueError(f"unknown backend '{requested}'")
    if requested in ("docker", "auto"):
        ok, detail = docker_available(run)
        if ok:
            return DockerSandboxRunner(run=run, **runner_kwargs)
        if requested == "docker":
            raise SecurityPolicyViolationError(f"docker backend unavailable: {detail}")
    runner = LocalSandboxRunner(**runner_kwargs)
    if require_isolation and runner._resolve_confinement() == "off":
        raise SecurityPolicyViolationError(
            "no isolating backend available (docker down, local confinement off)"
        )
    return runner
