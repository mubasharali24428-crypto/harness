import asyncio
import logging
import os
import shutil
import sys
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from harness_contracts.signatures import TaskHandle

from harness_sandbox import confinement
from harness_sandbox.config import config
from harness_sandbox.diff_engine import apply_unified_diff
from harness_sandbox.output_parser import StructuredExecResult, parse_exec_output
from harness_sandbox.policy import (
    SandboxCommandTimeoutError,
    sanitize_environment,
    validate_command,
    validate_path_edit,
)

logger = logging.getLogger(__name__)


logger = logging.getLogger(__name__)


class LocalSandboxRunner:
    """Ephemeral isolated process runner managing tasks on disk."""

    def __init__(
        self,
        storage_root: Path | None = None,
        confinement: str = "auto",      # auto | seatbelt | landlock | off
        allow_network: bool = True,
    ) -> None:
        self.storage_root = (storage_root or config.storage_root).resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.confinement = confinement
        self.allow_network = allow_network
        self.active_tasks: dict[str, dict] = {}

    def _resolve_confinement(self) -> str:
        if self.confinement != "auto":
            if self.confinement == "seatbelt" and not confinement.seatbelt_available():
                logger.warning("seatbelt requested but unavailable; running unconstrained")
                return "off"
            if self.confinement == "landlock" and not confinement.landlock_supported():
                logger.warning("landlock requested but unsupported; running unconstrained")
                return "off"
            return self.confinement
        if sys.platform == "darwin":
            return "seatbelt" if confinement.seatbelt_available() else "off"
        if sys.platform == "linux":
            return "landlock" if confinement.landlock_supported() else "off"
        return "off"

    async def _spawn(self, task: dict, command: str, stdout, stderr):
        """Single spawn point honoring the task's confinement mode."""
        import asyncio

        common = dict(
            cwd=str(task["workspace"]),
            env=sanitize_environment(os.environ),
            stdout=stdout,
            stderr=stderr,
        )
        mode = task.get("confinement", "off")
        if mode == "seatbelt":
            argv = confinement.seatbelt_command(command, Path(task["profile"]))
            return await asyncio.create_subprocess_exec(*argv, **common)
        kwargs: dict = {}
        if mode == "landlock":
            ws = task["workspace"]
            kwargs["preexec_fn"] = (
                lambda ws=ws: confinement.apply_landlock_in_child(Path(ws), self.allow_network)
            )
        return await asyncio.create_subprocess_shell(command, **common, **kwargs)

    def create_task(
        self,
        task_id: str,
        repo_ref: str,
        branch: str,
        forbidden_paths: list[str] | None = None,
        project_id: str | None = None,
    ) -> TaskHandle:
        workspace = self.storage_root / task_id / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        self.active_tasks[task_id] = {
            "workspace": workspace,
            "repo_ref": repo_ref,
            "branch": branch,
            "forbidden_paths": forbidden_paths or [],
            "created_at": time.time(),
            "procs": [],
            "project_id": project_id,
        }
        mode = self._resolve_confinement()
        self.active_tasks[task_id]["confinement"] = mode
        if mode == "seatbelt":
            self.active_tasks[task_id]["profile"] = str(
                confinement.write_seatbelt_profile(
                    workspace, self.allow_network, self.storage_root / task_id
                )
            )

        return TaskHandle(
            taskId=task_id,
            containerId=f"local-{task_id}",
            branch=branch,
        )

    async def exec(
        self,
        handle: TaskHandle,
        command: str,
    ) -> AsyncGenerator[str, None]:
        task = self.active_tasks.get(handle.taskId)
        if not task:
            raise RuntimeError(f"Task '{handle.taskId}' not found or destroyed.")

        validate_command(command, task["forbidden_paths"])

        budget = config.default_timeout_seconds

        proc = await self._spawn(
            task, command, asyncio.subprocess.PIPE, asyncio.subprocess.STDOUT
        )
        task["procs"].append(proc)

        async def _stream() -> AsyncGenerator[str, None]:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line.decode("utf-8", errors="replace")

        try:
            async for line in _stream():
                yield line
            try:
                await asyncio.wait_for(proc.wait(), timeout=budget)
            except TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise SandboxCommandTimeoutError(
                    f"Command exceeded {budget}s execution budget and was killed."
                ) from exc
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise

    async def exec_structured(
        self,
        handle: TaskHandle,
        command: str,
    ) -> StructuredExecResult:
        task = self.active_tasks.get(handle.taskId)
        if not task:
            raise RuntimeError(f"Task '{handle.taskId}' not found or destroyed.")

        validate_command(command, task["forbidden_paths"])

        budget = config.default_timeout_seconds

        start_time = time.perf_counter()
        proc = await self._spawn(
            task, command, asyncio.subprocess.PIPE, asyncio.subprocess.PIPE
        )
        task["procs"].append(proc)

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=budget)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise SandboxCommandTimeoutError(
                f"Command exceeded {budget}s execution budget and was killed."
            ) from exc
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        return parse_exec_output(
            command=command,
            exit_code=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )

    def apply_edit(self, handle: TaskHandle, path: str, diff: str) -> None:
        task = self.active_tasks.get(handle.taskId)
        if not task:
            raise RuntimeError(f"Task '{handle.taskId}' not found or destroyed.")

        validate_path_edit(path, task["forbidden_paths"])
        apply_unified_diff(
            workspace_dir=task["workspace"],
            target_path=path,
            diff_text=diff,
            forbidden_paths=task["forbidden_paths"],
        )

    def destroy(self, handle: TaskHandle) -> None:
        task = self.active_tasks.pop(handle.taskId, None)
        if task:
            import signal

            for proc in task.get("procs", ()):
                if proc.returncode is None:
                    try:
                        proc.send_signal(signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            task_dir = self.storage_root / handle.taskId
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)
