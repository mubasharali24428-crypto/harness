"""Per-task Docker boundary for Section 5 sandbox execution.

The Docker runner deliberately keeps the task/workspace policy in
``LocalSandboxRunner`` and changes only the command boundary: every task gets
its own long-lived container, a read-only root filesystem, private writable
workspace/tmp mounts, dropped capabilities, resource limits, and an explicit
network mode. The Docker daemon and image are deployment dependencies; neither
is silently substituted with host execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import docker
    from docker.errors import DockerException, NotFound
except ModuleNotFoundError:  # optional until SANDBOX_USE_DOCKER=true
    docker = None

    class DockerException(Exception):
        """Placeholder preserving the fail-closed error path without the SDK."""

    class NotFound(DockerException):
        """Placeholder for the Docker SDK's missing-container error."""
from harness_contracts.signatures import TaskHandle

from harness_sandbox.config import config
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.output_parser import StructuredExecResult, parse_exec_output
from harness_sandbox.policy import (
    SandboxCommandTimeoutError,
    SecurityPolicyViolationError,
    validate_command,
)

logger = logging.getLogger(__name__)

_CONTAINER_WORKSPACE = "/workspace"
_CONTAINER_TMP = "/tmp/harness-task"
_KEEPALIVE = ["/bin/sh", "-c", "while :; do sleep 3600; done"]


def _next_or_none(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None


class DockerSandboxRunner(LocalSandboxRunner):
    """Run each sandbox task in its own Docker container.

    ``LocalSandboxRunner`` remains the source of truth for task IDs, ownership,
    workspace path guards, command validation, edits, and private tmp paths.
    This subclass never falls back to local subprocess execution. If Docker,
    the configured image, or the container boundary is unavailable, task
    creation fails closed.
    """

    def __init__(
        self,
        storage_root: Path | None = None,
        *,
        image: str | None = None,
        # Network is disabled unless deployment code explicitly opts in. An
        # enabled network is confined to the allow-list egress proxy
        # (SANDBOX_EGRESS_NETWORK / SANDBOX_EGRESS_PROXY_URL); without both,
        # task creation fails closed.
        allow_network: bool = False,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            storage_root=storage_root,
            confinement="container",
            allow_network=allow_network,
        )
        # ``None`` means the local developer default; an explicit empty value
        # is preserved so deployment wiring with no reviewed image fails closed.
        self.image = config.default_docker_image if image is None else image
        self._client = client

    def _resolve_confinement_once(self) -> str:
        return "container"

    def _docker(self):
        if self._client is not None:
            return self._client
        if docker is None:
            raise SecurityPolicyViolationError(
                "DockerSandboxRunner requires the Docker SDK; refusing to run on the host"
            )
        try:
            client = docker.from_env()
            client.ping()
        except (DockerException, OSError) as exc:
            raise SecurityPolicyViolationError(
                "DockerSandboxRunner requires a reachable Docker daemon; refusing"
                " to run on the host"
            ) from exc
        self._client = client
        return client

    @staticmethod
    def _safe_container_path(path: str) -> str | None:
        """Return a normalized container path for a workspace-relative target."""
        clean = path.strip()
        if not clean or "\x00" in clean:
            return None
        candidate = Path(clean)
        if candidate.is_absolute() or ".." in candidate.parts:
            return None
        return f"{_CONTAINER_WORKSPACE}/{candidate.as_posix()}"

    def _forbidden_mounts(
        self, workspace: Path, tmpdir: Path, forbidden_paths: list[str]
    ) -> tuple[dict, dict]:
        """Mask forbidden workspace paths inside the container.

        Command and edit validators remain the first line of defense. These
        nested mounts provide a second line for reads performed indirectly by
        an allowed interpreter. Existing forbidden files are replaced by a
        read-only empty-file view; directories and future paths are masked by
        a read-only empty tmpfs.
        """
        volumes: dict[str, dict[str, str]] = {}
        tmpfs: dict[str, str] = {}
        workspace = workspace.resolve()
        for index, raw in enumerate(forbidden_paths):
            target = (workspace / raw).resolve()
            try:
                target.relative_to(workspace)
            except ValueError:
                continue
            container_target = self._safe_container_path(str(target.relative_to(workspace)))
            if container_target is None or container_target == _CONTAINER_WORKSPACE:
                continue
            if target.is_file():
                mask = tmpdir / f".forbidden-mask-{index}"
                mask.touch(exist_ok=True)
                volumes[str(mask)] = {"bind": container_target, "mode": "ro"}
            else:
                tmpfs[container_target] = "ro,nosuid,nodev,noexec,size=1m"
        return volumes, tmpfs

    def _container_environment(self) -> dict[str, str]:
        from harness_sandbox.policy import sanitize_environment

        env = sanitize_environment(os.environ)
        env.update(
            {
                "HOME": _CONTAINER_TMP,
                "TMPDIR": _CONTAINER_TMP,
                "TMP": _CONTAINER_TMP,
                "TEMP": _CONTAINER_TMP,
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            }
        )
        if self.allow_network:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env[name] = config.egress_proxy_url
        return env

    def _network_mode(self) -> str:
        if not self.allow_network:
            return "none"
        if not (config.egress_network and config.egress_proxy_url):
            raise SecurityPolicyViolationError(
                "sandbox network requires SANDBOX_EGRESS_NETWORK and"
                " SANDBOX_EGRESS_PROXY_URL (allow-list proxy); refusing unfiltered network"
            )
        return config.egress_network

    def _container_kwargs(self, task: dict) -> dict[str, Any]:
        workspace = Path(task["workspace"]).resolve()
        tmpdir = Path(task["tmpdir"]).resolve()
        forbidden_volumes, forbidden_tmpfs = self._forbidden_mounts(
            workspace, tmpdir, list(task.get("forbidden_paths") or [])
        )
        volumes: dict[str, dict[str, str]] = {
            str(workspace): {"bind": _CONTAINER_WORKSPACE, "mode": "rw"},
            str(tmpdir): {"bind": _CONTAINER_TMP, "mode": "rw"},
        }
        volumes.update(forbidden_volumes)
        return {
            "command": _KEEPALIVE,
            "detach": True,
            "name": f"harness-sandbox-{uuid.uuid4().hex}",
            "environment": self._container_environment(),
            "working_dir": _CONTAINER_WORKSPACE,
            "volumes": volumes,
            "tmpfs": forbidden_tmpfs,
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "pids_limit": 128,
            "mem_limit": f"{config.max_memory_mb}m",
            "nano_cpus": int(config.max_cpus * 1_000_000_000),
            "network_mode": self._network_mode(),
            "user": f"{os.getuid()}:{os.getgid()}",
            "labels": {"com.harness.sandbox.task_id": str(task["task_id"])},
        }

    def _start_container(self, task: dict) -> Any:
        client = self._docker()
        try:
            client.images.get(self.image)
            container = client.containers.run(self.image, **self._container_kwargs(task))
        except (DockerException, OSError) as exc:
            raise SecurityPolicyViolationError(
                f"unable to start Docker sandbox image '{self.image}'; refusing to run"
                " on the host"
            ) from exc
        task["container"] = container
        task["container_id"] = container.id
        return container

    def create_task(self, *args, **kwargs) -> TaskHandle:
        task_id = kwargs.get("task_id") or (args[0] if args else None)
        if task_id is None:
            raise TypeError("task_id is required")
        handle = super().create_task(*args, **kwargs)
        task = self.active_tasks[task_id]
        task["task_id"] = task_id
        if task.get("container") is not None:
            return TaskHandle(
                taskId=task_id, containerId=task["container_id"], branch=handle.branch
            )
        try:
            container = self._start_container(task)
        except Exception:
            super().destroy(handle)
            raise
        return TaskHandle(taskId=task_id, containerId=container.id, branch=handle.branch)

    def _task_container(self, handle: TaskHandle):
        task = self.active_tasks.get(handle.taskId)
        if not task or task.get("container") is None:
            raise RuntimeError(f"Task '{handle.taskId}' not found or container is unavailable.")
        return task, task["container"]

    def _exec_command(self, command: str, budget: int) -> list[str]:
        wrapped = f"exec timeout --signal=KILL {budget}s /bin/sh -lc {shlex.quote(command)}"
        return ["/bin/sh", "-lc", wrapped]

    def _exec_kwargs(self, task: dict, command: str, budget: int, *, stream: bool) -> dict:
        return {
            "cmd": self._exec_command(command, budget),
            "workdir": _CONTAINER_WORKSPACE,
            "environment": self._container_environment(),
            "stdout": True,
            "stderr": True,
            "stdin": False,
            "tty": False,
            "stream": stream,
            "demux": False,
        }

    async def _restart_container(self, task: dict) -> None:
        old = task.get("container")
        if old is not None:
            try:
                await asyncio.to_thread(old.remove, force=True)
            except NotFound:
                pass
        task.pop("container", None)
        task.pop("container_id", None)
        self._start_container(task)

    @staticmethod
    def _decode_chunk(chunk: Any) -> str:
        if isinstance(chunk, tuple):
            chunk = b"".join(part for part in chunk if part)
        if isinstance(chunk, bytes):
            return chunk.decode("utf-8", errors="replace")
        return str(chunk)

    def _check_post_launch_forbidden(self, task: dict) -> None:
        """G6: masks were computed at container launch; files that APPEARED
        under a forbidden path afterwards are otherwise unmasked for the rest
        of the task. A hash-of-paths snapshot would race; failing closed when
        a forbidden target now exists where none did at launch is cheap and
        conservative (the run stops; the operator sees why)."""
        workspace = Path(task["workspace"]).resolve()
        for raw in task.get("forbidden_paths") or []:
            target = (workspace / raw).resolve()
            try:
                target.relative_to(workspace)
            except ValueError:
                continue
            if target.exists():
                raise SecurityPolicyViolationError(
                    f"forbidden path '{raw}' exists on disk at exec time; launch-time "
                    "masking cannot protect it — refusing to execute"
                )

    async def exec(self, handle: TaskHandle, command: str):
        task, container = self._task_container(handle)
        validate_command(command, task["forbidden_paths"], cwd=str(task["workspace"]))
        self._check_post_launch_forbidden(task)
        budget = config.default_timeout_seconds
        started = time.monotonic()
        result = await asyncio.to_thread(
            container.exec_run, **self._exec_kwargs(task, command, budget, stream=True)
        )
        iterator = iter(result.output)
        while True:
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                await self._restart_container(task)
                raise SandboxCommandTimeoutError(
                    f"Command exceeded {budget}s execution budget and was killed."
                )
            try:
                chunk = await asyncio.wait_for(
                    asyncio.to_thread(_next_or_none, iterator), timeout=remaining
                )
            except TimeoutError as exc:
                await self._restart_container(task)
                raise SandboxCommandTimeoutError(
                    f"Command exceeded {budget}s execution budget and was killed."
                ) from exc
            if chunk is None:
                break
            yield self._decode_chunk(chunk)

    async def exec_structured(
        self, handle: TaskHandle, command: str
    ) -> StructuredExecResult:
        task, container = self._task_container(handle)
        validate_command(command, task["forbidden_paths"], cwd=str(task["workspace"]))
        self._check_post_launch_forbidden(task)
        budget = config.default_timeout_seconds
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    container.exec_run, **self._exec_kwargs(task, command, budget, stream=False)
                ),
                timeout=budget + 5,
            )
        except TimeoutError as exc:
            await self._restart_container(task)
            raise SandboxCommandTimeoutError(
                f"Command exceeded {budget}s execution budget and was killed."
            ) from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        return parse_exec_output(
            command=command,
            exit_code=result.exit_code or 0,
            stdout=self._decode_chunk(result.output),
            stderr="",
            duration_ms=duration_ms,
        )

    def destroy(self, handle: TaskHandle) -> None:
        task, container = self._task_container(handle)
        try:
            container.remove(force=True)
        except NotFound:
            pass
        except (DockerException, OSError) as exc:
            logger.error("failed to remove sandbox container for %s", handle.taskId, exc_info=True)
            raise SecurityPolicyViolationError(
                f"could not confirm Docker sandbox removal for task '{handle.taskId}'"
            ) from exc
        task.pop("container", None)
        super().destroy(handle)
