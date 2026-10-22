from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harness_contracts.events import EventType
from harness_contracts.signatures import Sandbox, TaskHandle

from harness_sandbox.checkpoints import CheckpointError, CheckpointManager
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.output_parser import StructuredExecResult

logger = logging.getLogger(__name__)


class SandboxService(Sandbox):
    """Section 5 Sandbox Execution Service implementing the Sandbox Protocol.

    M2: when ``on_stream`` is provided, model-visible work is published as
    contract events (terminal_output per streamed line / structured snapshot,
    diff_progress after each applied edit). Payloads carry taskId + projectId so
    the S1 bus can route them; without a callback the service behaves exactly as
    before.

    M12-G9: every edit batch is bracketed by git snapshots in the task
    workspace — ``checkpoint: pre-edit <ts>`` before the diff lands,
    ``checkpoint: post-edit step-N`` after. Failures are logged telemetry loss,
    never edit failures.
    """

    def __init__(
        self,
        runner: LocalSandboxRunner | None = None,
        on_stream: Callable[[EventType, dict[str, Any]], None] | None = None,
        checkpoints: CheckpointManager | None = None,
    ) -> None:
        self.runner = runner or LocalSandboxRunner()
        self.on_stream = on_stream
        self.checkpoints = checkpoints or CheckpointManager(root=self.runner.storage_root)
        self._edit_steps: dict[str, int] = {}

    def _emit(self, event_type: EventType, handle: TaskHandle, **extra: Any) -> None:
        if self.on_stream is None:
            return
        task = self.runner.active_tasks.get(handle.taskId)
        project_id = task.get("project_id") if task else None
        try:
            self.on_stream(
                event_type,
                {"taskId": handle.taskId, "projectId": project_id, **extra},
            )
        except Exception:  # noqa: BLE001 — telemetry must never break execution
            pass

    def run_task(
        self,
        task_id: str,
        repo_ref: str,
        branch: str,
        forbidden_paths: list[str],
        project_id: str | None = None,
        team_id: str | None = None,
        resume: bool = False,
    ) -> TaskHandle:
        return self.runner.create_task(
            task_id=task_id,
            repo_ref=repo_ref,
            branch=branch,
            forbidden_paths=forbidden_paths,
            project_id=project_id,
            team_id=team_id,
            resume=resume,
        )

    def exec(self, handle: TaskHandle, command: str) -> Any:
        """Returns an async generator of output lines, streaming terminal_output
        events as they arrive. Call-compatible with the previous pass-through."""

        async def _gen():
            if self.on_stream is not None:
                task = self.runner.active_tasks.get(handle.taskId)
                project_id = task.get("project_id") if task else None
            else:
                project_id = None
            async for line in self.runner.exec(handle, command):
                if self.on_stream is not None:
                    try:
                        self.on_stream(
                            EventType.TERMINAL_OUTPUT,
                            {"taskId": handle.taskId, "projectId": project_id, "line": line},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                yield line

        return _gen()

    async def exec_structured(self, handle: TaskHandle, command: str) -> StructuredExecResult:
        result = await self.runner.exec_structured(handle, command)
        if self.on_stream is not None:
            task = self.runner.active_tasks.get(handle.taskId)
            project_id = task.get("project_id") if task else None
            try:
                self.on_stream(
                    EventType.TERMINAL_OUTPUT,
                    {
                        "taskId": handle.taskId,
                        "projectId": project_id,
                        "line": result.summary or result.status,
                    },
                )
            except Exception:  # noqa: BLE001 — telemetry must never break execution
                pass
        return result

    def apply_edit(self, handle: TaskHandle, path: str, diff: str) -> None:
        task = self.runner.active_tasks.get(handle.taskId)
        if not task:
            raise RuntimeError(f"Task '{handle.taskId}' not found or destroyed.")

        # M12-G9: bracket the edit batch with git snapshots. The pre-edit
        # snapshot captures the workspace exactly as the agent found it; the
        # post-edit one lands after the diff applies. Checkpoint failures are
        # telemetry loss — they must never fail the edit itself.
        project_id = task.get("project_id")
        workspace: Path = task["workspace"]
        step_key = f"{handle.taskId}:{path}"
        step = self._edit_steps.get(step_key, 0) + 1
        try:
            self.checkpoints.snapshot(
                handle.taskId, workspace, kind="pre-edit", project_id=project_id
            )
        except CheckpointError:
            logger.warning(
                "pre-edit checkpoint failed for task %s (continuing with edit)",
                handle.taskId,
                exc_info=True,
            )

        self.runner.apply_edit(handle, path, diff)
        self._edit_steps[step_key] = step

        post_sha: str | None = None
        try:
            post_sha = self.checkpoints.snapshot(
                handle.taskId,
                workspace,
                kind="post-edit",
                step=step,
                project_id=project_id,
            )
        except CheckpointError:
            logger.warning(
                "post-edit checkpoint failed for task %s", handle.taskId, exc_info=True
            )

        if post_sha:
            self._emit(
                EventType.DIFF_PROGRESS,
                handle,
                path=path,
                checkpoint={"kind": "post-edit", "step": step, "sha": post_sha},
            )
        self._emit(EventType.DIFF_PROGRESS, handle, path=path)

    def destroy(self, handle: TaskHandle) -> None:
        self.runner.destroy(handle)
