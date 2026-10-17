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
    SecurityPolicyViolationError,
    sanitize_environment,
    validate_command,
    validate_path_edit,
)
from harness_sandbox.quota import ResourceGuard, ResourceQuota, rlimit_preexec

logger = logging.getLogger(__name__)


logger = logging.getLogger(__name__)


class LocalSandboxRunner:
    """Ephemeral isolated process runner managing tasks on disk."""

    def __init__(
        self,
        storage_root: Path | None = None,
        confinement: str = "auto",      # auto | seatbelt | landlock | off
        allow_network: bool = True,
        quota: ResourceQuota | None = None,
    ) -> None:
        self.quota = quota
        self.storage_root = (storage_root or config.storage_root).resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.confinement = confinement
        self.allow_network = allow_network
        self.active_tasks: dict[str, dict] = {}
        self._resolved_confinement: str | None = None

    def _resolve_confinement(self) -> str:
        # Resolve ONCE per runner: the probe can spawn a child, and 'auto'
        # degradation deserves one honest log line, not one per task.
        if self._resolved_confinement is None:
            self._resolved_confinement = self._resolve_confinement_once()
        return self._resolved_confinement

    def _resolve_confinement_once(self) -> str:
        if self.confinement != "auto":
            # S1-R03d: an EXPLICIT confinement request must never degrade to
            # unconstrained execution — fail closed at task creation instead.
            if self.confinement == "seatbelt" and not confinement.seatbelt_available():
                raise SecurityPolicyViolationError(
                    "confinement 'seatbelt' requested but unavailable;"
                    " refusing to run unconstrained"
                )
            if self.confinement == "landlock" and not confinement.landlock_supported():
                raise SecurityPolicyViolationError(
                    "confinement 'landlock' requested but unsupported;"
                    " refusing to run unconstrained"
                )
            return self.confinement
        if sys.platform == "darwin":
            if confinement.seatbelt_available():
                return "seatbelt"
            logger.warning("sandbox confinement 'auto': seatbelt unavailable — 'off'")
            return "off"
        if sys.platform == "linux":
            if confinement.landlock_supported():
                return "landlock"
            # M18-C9 (E2E-found): the version heuristic alone resolved to
            # 'landlock' inside the shipped compose image (Docker's default
            # seccomp blocks the syscalls) and every exec died in preexec.
            # After the probe fix this branch is the HONEST degrade: say so.
            logger.warning(
                "sandbox confinement 'auto': landlock not usable here (kernel"
                " or container policy) — running 'off'; set"
                " HARNESS_SANDBOX_CONFINEMENT to pin a mode explicitly"
            )
            return "off"
        logger.info("sandbox confinement 'auto': platform %s — 'off'", sys.platform)
        return "off"

    @staticmethod
    def _kill(proc) -> None:
        """S1-R03f: kill the whole process GROUP (grandchildren included)."""
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def _spawn(self, task: dict, command: str, stdout, stderr):
        """Single spawn point honoring the task's confinement mode."""
        import asyncio

        common = dict(
            cwd=str(task["workspace"]),
            env=sanitize_environment(os.environ),
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,  # S1-R03f: own process group for group-kill
        )
        mode = task.get("confinement", "off")
        rl = rlimit_preexec(self.quota) if self.quota else None
        if mode == "seatbelt":
            argv = confinement.seatbelt_command(command, Path(task["profile"]))
            proc = await asyncio.create_subprocess_exec(*argv, **common, preexec_fn=rl)
            return self._guard(proc, task)
        kwargs: dict = {}
        if mode == "landlock":
            ws = task["workspace"]

            def _pre(ws=ws) -> None:
                if rl:
                    rl()
                confinement.apply_landlock_in_child(Path(ws), self.allow_network)

            kwargs["preexec_fn"] = _pre
        elif rl:
            kwargs["preexec_fn"] = rl
        proc = await asyncio.create_subprocess_shell(command, **common, **kwargs)
        return self._guard(proc, task)

    def _guard(self, proc, task: dict):
        """Attach the polling quota guard (memory / processes / disk) to a spawned command."""
        if self.quota and (
            self.quota.memory_mb or self.quota.max_processes or self.quota.disk_mb
        ):
            guard = ResourceGuard(self.quota, Path(task["workspace"]))
            proc.quota_guard = guard
            proc.quota_task = asyncio.get_running_loop().create_task(
                guard.watch(proc, self._kill)
            )
        return proc

    @staticmethod
    def _raise_if_breached(proc) -> None:
        guard = getattr(proc, "quota_guard", None)
        if guard is not None and guard.violation is not None:
            raise guard.violation

    def create_task(
        self,
        task_id: str,
        repo_ref: str,
        branch: str,
        forbidden_paths: list[str] | None = None,
        project_id: str | None = None,
        team_id: str | None = None,
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
            "team_id": team_id,  # S1-R01d: owning team bound at create
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

        validate_command(command, task["forbidden_paths"], cwd=str(task["workspace"]))

        budget = config.default_timeout_seconds

        proc = await self._spawn(
            task, command, asyncio.subprocess.PIPE, asyncio.subprocess.STDOUT
        )
        task["procs"].append(proc)

        start = time.monotonic()

        def _timed_out() -> SandboxCommandTimeoutError:
            return SandboxCommandTimeoutError(
                f"Command exceeded {budget}s execution budget and was killed."
            )

        async def _stream() -> AsyncGenerator[str, None]:
            while True:
                # S1-R03f: the budget bounds EVERY read, not just post-EOF wait —
                # a hung or slowly-dripping command can no longer run forever.
                remaining = budget - (time.monotonic() - start)
                if remaining <= 0:
                    self._kill(proc)
                    await proc.wait()
                    raise _timed_out()
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except TimeoutError as exc:
                    self._kill(proc)
                    await proc.wait()
                    raise _timed_out() from exc
                if not line:
                    break
                yield line.decode("utf-8", errors="replace")

        try:
            async for line in _stream():
                yield line
            try:
                await asyncio.wait_for(
                    proc.wait(), timeout=max(0.0, budget - (time.monotonic() - start))
                )
                self._raise_if_breached(proc)
            except TimeoutError as exc:
                self._kill(proc)
                await proc.wait()
                raise _timed_out() from exc
        except asyncio.CancelledError:
            self._kill(proc)
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

        validate_command(command, task["forbidden_paths"], cwd=str(task["workspace"]))

        budget = config.default_timeout_seconds

        start_time = time.perf_counter()
        proc = await self._spawn(
            task, command, asyncio.subprocess.PIPE, asyncio.subprocess.PIPE
        )
        task["procs"].append(proc)

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=budget)
        except TimeoutError as exc:
            self._kill(proc)
            await proc.wait()
            raise SandboxCommandTimeoutError(
                f"Command exceeded {budget}s execution budget and was killed."
            ) from exc
        except asyncio.CancelledError:
            self._kill(proc)
            await proc.wait()
            raise
        self._raise_if_breached(proc)
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
            for proc in task.get("procs", ()):
                if proc.returncode is None:
                    self._kill(proc)  # S1-R03f: kills the whole process group
            task_dir = self.storage_root / handle.taskId
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)
