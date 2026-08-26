from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from harness_contracts.roles import Principal, can
from harness_contracts.signatures import TaskHandle
from pydantic import BaseModel, Field

from harness_sandbox.checkpoints import (
    CheckpointError,
    CheckpointNotFoundError,
    CheckpointRepositoryNotFoundError,
    InvalidShaError,
)
from harness_sandbox.output_parser import StructuredExecResult
from harness_sandbox.policy import SecurityPolicyViolationError
from harness_sandbox.service import SandboxService

router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])
_service = SandboxService()


def configure_sandbox(service: SandboxService) -> None:
    """Deploy-time wiring (Section 10): swap in a service with stream callbacks."""
    global _service
    _service = service


async def get_principal(request: Request) -> Principal:
    """Auth resolution via app.state.get_principal; fail closed (D6/P0.6)."""
    dep = getattr(request.app.state, "get_principal", None)
    if dep is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "AUTH_NOT_CONFIGURED", "message": "auth not wired"}},
        )
    return await dep(request)


async def require_writer(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """Executing code is a write action: viewers/guests are denied."""
    if not can(principal, "write"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "ROLE_DENIED", "message": "role may not write"}},
        )
    return principal


Writer = Annotated[Principal, Depends(require_writer)]


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
def create_task(req: CreateTaskRequest, principal: Writer) -> TaskHandle:
    return _service.run_task(
        task_id=req.taskId,
        repo_ref=req.repoRef,
        branch=req.branch,
        forbidden_paths=req.forbiddenPaths,
        project_id=req.projectId,
    )


@router.post("/tasks/{taskId}/exec", response_model=StructuredExecResult)
async def exec_command(
    taskId: str, req: ExecCommandRequest, principal: Writer
) -> StructuredExecResult:
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
def apply_edit(taskId: str, req: ApplyEditRequest, principal: Writer) -> dict[str, Any]:
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
def destroy_task(taskId: str, principal: Writer) -> dict[str, str]:
    handle = TaskHandle(taskId=taskId, containerId=f"local-{taskId}", branch="main")
    _service.destroy(handle)
    return {"status": "destroyed", "taskId": taskId}


# ---------------------------------------------------------------------------
# M12-G9: checkpoint read surface. Snapshots themselves are taken by the
# edit flow (SandboxService.apply_edit → CheckpointManager); these endpoints
# only list and diff what already exists, so they are read actions.
# ---------------------------------------------------------------------------


def _checkpoint_error(e: CheckpointError) -> HTTPException:
    status_map = {
        InvalidShaError: status.HTTP_400_BAD_REQUEST,
        CheckpointNotFoundError: status.HTTP_404_NOT_FOUND,
        CheckpointRepositoryNotFoundError: status.HTTP_404_NOT_FOUND,
    }
    return HTTPException(
        status_code=status_map.get(type(e), status.HTTP_500_INTERNAL_SERVER_ERROR),
        detail={"error": {"code": e.code, "message": str(e)}},
    )


def get_reader_principal(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """Listing/diffing snapshots is a read action — any authenticated role."""
    return principal


Reader = Annotated[Principal, Depends(get_reader_principal)]


@router.get("/{projectId}/checkpoints")
def list_checkpoints(projectId: str, principal: Reader) -> dict:
    """Snapshots for every workspace of this project, newest first."""
    try:
        items = _service.checkpoints.list_checkpoints(projectId)
    except CheckpointError as e:
        raise _checkpoint_error(e) from e
    # Pagination convention (api-conventions.md): ?limit=&cursor= → {items, nextCursor}.
    limit = 50
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        next_cursor = items[-1]["sha"]
    return {"projectId": projectId, "items": items, "nextCursor": next_cursor}


@router.get("/{projectId}/checkpoints/{sha}/diff")
def checkpoint_diff(projectId: str, sha: str, principal: Reader) -> Response:
    """Raw unified diff introduced by one snapshot commit."""
    try:
        diff_text = _service.checkpoints.get_diff(projectId, sha)
    except CheckpointError as e:
        raise _checkpoint_error(e) from e
    return Response(content=diff_text, media_type="text/plain; charset=utf-8")
