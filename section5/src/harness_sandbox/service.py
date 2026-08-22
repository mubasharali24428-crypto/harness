from collections.abc import Callable
from typing import Any

from harness_contracts.events import EventType
from harness_contracts.signatures import Sandbox, TaskHandle

from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.output_parser import StructuredExecResult


class SandboxService(Sandbox):
    """Section 5 Sandbox Execution Service implementing the Sandbox Protocol.

    M2: when ``on_stream`` is provided, model-visible work is published as
    contract events (terminal_output per streamed line / structured snapshot,
    diff_progress after each applied edit). Payloads carry taskId + projectId so
    the S1 bus can route them; without a callback the service behaves exactly as
    before.
    """

    def __init__(
        self,
        runner: LocalSandboxRunner | None = None,
        on_stream: Callable[[EventType, dict[str, Any]], None] | None = None,
    ) -> None:
        self.runner = runner or LocalSandboxRunner()
        self.on_stream = on_stream

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
    ) -> TaskHandle:
        return self.runner.create_task(
            task_id=task_id,
            repo_ref=repo_ref,
            branch=branch,
            forbidden_paths=forbidden_paths,
            project_id=project_id,
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
        self.runner.apply_edit(handle, path, diff)
        self._emit(EventType.DIFF_PROGRESS, handle, path=path)

    def destroy(self, handle: TaskHandle) -> None:
        self.runner.destroy(handle)
