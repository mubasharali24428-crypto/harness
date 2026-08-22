from typing import Any

from fastapi import APIRouter, HTTPException, status
from harness_contracts.signatures import TaskHandle
from pydantic import BaseModel, Field

from harness_sandbox.output_parser import StructuredExecResult
from harness_sandbox.policy import SecurityPolicyViolationError
from harness_sandbox.service import SandboxService

router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])
_service = SandboxService()


def configure_sandbox(service: SandboxService) -> None:
    """Deploy-time wiring (Section 10): swap in a service with stream callbacks."""
    global _service
    _service = service


def get_sandbox_service() -> SandboxService:
    return _service


class CreateTaskRequest(BaseModel):
    taskId: str
    repoRef: str
    branch: str
    forbiddenPaths: list[str] = Field(default_factory=list)
    projectId: str | None = None


class ExecCommandRequest(BaseModel):
    command: str


class ApplyEditRequest(BaseModel):
    path: str
    diff: str


@router.post("/tasks", response_model=TaskHandle, status_code=status.HTTP_201_CREATED)
def create_task(req: CreateTaskRequest) -> TaskHandle:
    return _service.run_task(
        task_id=req.taskId,
        repo_ref=req.repoRef,
        branch=req.branch,
        forbidden_paths=req.forbiddenPaths,
        project_id=req.projectId,
    )


@router.post("/tasks/{taskId}/exec", response_model=StructuredExecResult)
async def exec_command(taskId: str, req: ExecCommandRequest) -> StructuredExecResult:
    handle = TaskHandle(taskId=taskId, containerId=f"local-{taskId}", branch="main")
    try:
        return await _service.exec_structured(handle, req.command)
    except SecurityPolicyViolationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "SECURITY_POLICY_VIOLATION", "message": str(e)}},
        ) from e
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "TASK_NOT_FOUND", "message": str(e)}},
        ) from e


@router.post("/tasks/{taskId}/edits", status_code=status.HTTP_200_OK)
def apply_edit(taskId: str, req: ApplyEditRequest) -> dict[str, Any]:
    handle = TaskHandle(taskId=taskId, containerId=f"local-{taskId}", branch="main")
    try:
        _service.apply_edit(handle, req.path, req.diff)
        return {"status": "ok", "path": req.path}
    except SecurityPolicyViolationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "SECURITY_POLICY_VIOLATION", "message": str(e)}},
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "DIFF_APPLY_ERROR", "message": str(e)}},
        ) from e


@router.post("/tasks/{taskId}/destroy", status_code=status.HTTP_200_OK)
def destroy_task(taskId: str) -> dict[str, str]:
    handle = TaskHandle(taskId=taskId, containerId=f"local-{taskId}", branch="main")
    _service.destroy(handle)
    return {"status": "destroyed", "taskId": taskId}
