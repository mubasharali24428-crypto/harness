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
    InvalidTaskIdError,
    SandboxCommandTimeoutError,
    SecurityPolicyViolationError,
    TaskIdInUseError,
    sanitize_environment,
    validate_command,
    validate_path_edit,
    validate_task_id,
)

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
        self.storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
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
        if self.confinement == "off":
            # Explicit off is a development escape hatch, never a silent
            # production fallback: require an unmistakable opt-in and log it.
            allowed = os.environ.get("HARNESS_SANDBOX_ALLOW_UNCONFINED", "").strip().lower()
            if allowed not in {"1", "true", "yes"}:
                raise SecurityPolicyViolationError(
                    "confinement 'off' is disabled; set"
                    " HARNESS_SANDBOX_ALLOW_UNCONFINED=1 only for local development"
                )
            logger.warning(
                "sandbox confinement explicitly OFF (HARNESS_SANDBOX_ALLOW_UNCONFINED=1)"
            )
            return "off"
        if self.confinement == "container":
            raise SecurityPolicyViolationError(
                "confinement 'container' requires DockerSandboxRunner;"
                " LocalSandboxRunner cannot provide a container boundary"
            )
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
        elif sys.platform == "linux":
            # S-01: Landlock now denies reads of the task's forbidden paths
            # (the read ruleset is an allow-list), so it is a usable boundary
            # for pilot execution on Linux, not just a write guard.
            if confinement.landlock_supported():
                return "landlock"
        raise SecurityPolicyViolationError(
            "sandbox confinement 'auto' found no usable OS boundary on this host"
            f" (platform {sys.platform}); refusing to run unconstrained."
            " Set HARNESS_SANDBOX_CONFINEMENT=off with"
            " HARNESS_SANDBOX_ALLOW_UNCONFINED=1 only for local development,"
            " or run the container runner."
        )

    def _task_dir(self, task_id: str) -> Path:
        """The one place a taskId becomes a path (T16): validated, direct child of the root."""
        validate_task_id(task_id)
        task_dir = self.storage_root / task_id
        if task_dir.is_symlink() or task_dir.resolve().parent != self.storage_root:
            raise InvalidTaskIdError(f"taskId '{task_id}' does not resolve under the storage root.")
        return task_dir

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

        env = sanitize_environment(os.environ)
        # T7b: every temp-dir convention points at the task's private 0700 tmp.
        env.update(TMPDIR=task["tmpdir"], TMP=task["tmpdir"], TEMP=task["tmpdir"])
        common = dict(
            cwd=str(task["workspace"]),
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,  # S1-R03f: own process group for group-kill
        )
        mode = task.get("confinement", "off")
        if mode == "seatbelt":
            argv = confinement.seatbelt_command(command, Path(task["profile"]))
            return await asyncio.create_subprocess_exec(*argv, **common)
        kwargs: dict = {}
        if mode == "landlock":
            ws, tmp = task["workspace"], task["tmpdir"]
            # S-01: the Landlock read ruleset excludes the task's forbidden
            # paths, exactly like the Seatbelt profile does on macOS.
            kwargs["preexec_fn"] = lambda ws=ws, tmp=tmp: confinement.apply_landlock_in_child(
                Path(ws),
                self.allow_network,
                Path(tmp),
                list(task.get("forbidden_paths") or []),
            )
        return await asyncio.create_subprocess_shell(command, **common, **kwargs)

    def _refresh_seatbelt_profiles(self) -> None:
        """Rewrite every live seatbelt profile so sibling denies stay complete.

        A new task can appear after an older profile was compiled; each profile
        therefore denies every OTHER task directory currently on disk. Denying
        the shared parent is not an option (SBPL precedence would re-open the
        task's own forbidden paths via the re-allow rule).
        """
        task_dirs = [
            d.resolve()
            for d in self.storage_root.iterdir()
            if d.is_dir() and (d / "workspace").is_dir()
        ]
        for tid, live in self.active_tasks.items():
            if live.get("confinement") != "seatbelt":
                continue
            own = self._task_dir(tid).resolve()
            live["profile"] = str(
                confinement.write_seatbelt_profile(
                    live["workspace"],
                    self.allow_network,
                    own,
                    Path(live["tmpdir"]),
                    live.get("forbidden_paths"),
                    [d for d in task_dirs if d != own],
                )
            )

    def create_task(
        self,
        task_id: str,
        repo_ref: str,
        branch: str,
        forbidden_paths: list[str] | None = None,
        project_id: str | None = None,
        team_id: str | None = None,
        resume: bool = False,
    ) -> TaskHandle:
        task_dir = self._task_dir(task_id)  # T16: validates before any filesystem effect
        owner = (team_id or "").encode()
        owner_file = task_dir / ".owner"

        # T15: a taskId is a capability to a workspace. Never hand it to another
        # team, and never silently re-bind it for the same team.
        live = self.active_tasks.get(task_id)
        if live is not None:
            if not resume or (live["team_id"] or "").encode() != owner:
                raise TaskIdInUseError("taskId is already in use")
            return TaskHandle(
                taskId=task_id, containerId=f"local-{task_id}", branch=live["branch"]
            )
        try:
            task_dir.mkdir(mode=0o700)  # atomic claim of a fresh id
            owner_file.write_bytes(owner)
        except FileExistsError:
            # On disk but not live (runner restarted): only the recorded owner may
            # resume. No owner record (pre-T15 data) means nobody may.
            if not resume or not owner_file.is_file() or owner_file.read_bytes() != owner:
                raise TaskIdInUseError("taskId is already in use") from None
        task_dir.chmod(0o700)

        workspace = task_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        tmpdir = task_dir / "tmp"  # T7b: private, never the shared system temp
        tmpdir.mkdir(mode=0o700, exist_ok=True)
        tmpdir.chmod(0o700)

        self.active_tasks[task_id] = {
            "workspace": workspace,
            "tmpdir": str(tmpdir),
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
            self._refresh_seatbelt_profiles()

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
        task_dir = self._task_dir(handle.taskId)  # T16: never rmtree an unvalidated path
        task = self.active_tasks.pop(handle.taskId, None)
        if task:
            for proc in task.get("procs", ()):
                if proc.returncode is None:
                    self._kill(proc)  # S1-R03f: kills the whole process group
            if task_dir.exists():  # also removes the private tmp dir (T7b)
                shutil.rmtree(task_dir, ignore_errors=True)
            if self.active_tasks:
                self._refresh_seatbelt_profiles()
