from __future__ import annotations

import inspect
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from harness_contracts.roles import Principal, can
from harness_contracts.signatures import TaskHandle
from pydantic import BaseModel, Field

from harness_sandbox.checkpoints import (
    CheckpointConflictError,
    CheckpointError,
    CheckpointNotFoundError,
    CheckpointRepositoryNotFoundError,
    CheckpointSelectionError,
    InvalidShaError,
)
from harness_sandbox.output_parser import StructuredExecResult
from harness_sandbox.policy import (
    InvalidTaskIdError,
    SecurityPolicyViolationError,
    TaskIdInUseError,
)
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


async def _lookup_project_team(project_id: str, request: Request) -> str | None:
    """Owning team of a project via app.state.project_team_lookup (S1-R01d).

    Fail closed: an unwired lookup is a 503, not an open door. Deploy wiring
    lives in deploy/runners/services.py (app_sandbox).
    """
    lookup = getattr(request.app.state, "project_team_lookup", None)
    if lookup is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "PROJECT_LOOKUP_NOT_CONFIGURED",
                    "message": "project team lookup not wired",
                }
            },
        )
    team = lookup(project_id)
    if inspect.isawaitable(team):
        team = await team
    return team


def _project_not_found(project_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": {
                "code": "PROJECT_NOT_FOUND",
                "message": f"unknown project '{project_id}'",
            }
        },
    )


def _task_visible(task_id: str, principal: Principal) -> bool:
    """True only when an active task exists and belongs to the principal's team.

    S1-R01d: invisible tasks read exactly like missing ones — callers keep
    their existing not-found answers, so foreign task ids leak nothing.
    """
    task = _service.runner.active_tasks.get(task_id)
    if task is None:
        return False
    return task.get("team_id") == principal.teamId


class CreateTaskRequest(BaseModel):
    taskId: str
    repoRef: str
    branch: str
    forbiddenPaths: list[str] = Field(default_factory=list)
    projectId: str | None = None
    # T15: reusing a taskId is refused unless the owning team explicitly resumes it.
    resume: bool = False


class ExecCommandRequest(BaseModel):
    command: str


class ApplyEditRequest(BaseModel):
    path: str
    diff: str


@router.post("/tasks", response_model=TaskHandle, status_code=status.HTTP_201_CREATED)
async def create_task(
    req: CreateTaskRequest, request: Request, principal: Writer
) -> TaskHandle:
    if req.projectId is not None:
        team = await _lookup_project_team(req.projectId, request)
        if team is None or team != principal.teamId:
            raise _project_not_found(req.projectId)
    try:
        return _service.run_task(
            task_id=req.taskId,
            repo_ref=req.repoRef,
            branch=req.branch,
            forbidden_paths=req.forbiddenPaths,
            project_id=req.projectId,
            team_id=principal.teamId,
            resume=req.resume,
        )
    except InvalidTaskIdError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_TASK_ID", "message": str(e)}},
        ) from e
    except TaskIdInUseError as e:
        # Same answer for "yours, no resume" and "someone else's": no ownership oracle.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "TASK_ID_IN_USE", "message": str(e)}},
        ) from e


@router.post("/tasks/{taskId}/exec", response_model=StructuredExecResult)
async def exec_command(
    taskId: str, req: ExecCommandRequest, principal: Writer
) -> StructuredExecResult:
    handle = TaskHandle(taskId=taskId, containerId=f"local-{taskId}", branch="main")
    try:
        if not _task_visible(taskId, principal):
            raise RuntimeError(f"Task '{taskId}' not found or destroyed.")
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
        if not _task_visible(taskId, principal):
            raise RuntimeError(f"Task '{taskId}' not found or destroyed.")
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
    # S1-R01d: foreign tasks read as absent — no destroy, same "destroyed" ack.
    if _task_visible(taskId, principal):
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
        CheckpointSelectionError: status.HTTP_422_UNPROCESSABLE_CONTENT,
        CheckpointConflictError: status.HTTP_409_CONFLICT,
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


async def _require_checkpoint_project(
    project_id: str, request: Request, principal: Principal
) -> None:
    """S1-R01d: checkpoints are only readable for the principal's own team.

    Unknown *and* foreign projects answer with the same honest
    CHECKPOINT_REPOSITORY_NOT_FOUND envelope — no existence oracle.
    """
    team = await _lookup_project_team(project_id, request)
    if team is None or team != principal.teamId:
        raise _checkpoint_error(
            CheckpointRepositoryNotFoundError(
                f"no checkpoint workspace for project '{project_id}'"
            )
        )


@router.get("/{projectId}/checkpoints")
async def list_checkpoints(projectId: str, request: Request, principal: Reader) -> dict:
    """Snapshots for every workspace of this project, newest first."""
    await _require_checkpoint_project(projectId, request, principal)
    try:
        items = _service.checkpoints.list_checkpoints(projectId)
    except CheckpointRepositoryNotFoundError:
        # Ownership was checked above. A valid project with no snapshots is
        # an empty collection; unknown/foreign projects still return 404.
        items = []
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
async def checkpoint_diff(
    projectId: str, sha: str, request: Request, principal: Reader
) -> Response:
    """Raw unified diff introduced by one snapshot commit."""
    await _require_checkpoint_project(projectId, request, principal)
    try:
        diff_text = _service.checkpoints.get_diff(projectId, sha)
    except CheckpointError as e:
        raise _checkpoint_error(e) from e
    return Response(content=diff_text, media_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# C16: hunk-level review. The server parses the snapshot's own diff and the
# caller only names (path, hunk index) pairs, so no client text ever reaches
# git apply. Both writes require the writer role and team ownership.
# ---------------------------------------------------------------------------


class HunkRef(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    index: int = Field(ge=0)


class RevertHunksRequest(BaseModel):
    hunks: list[HunkRef] = Field(min_length=1, max_length=500)


@router.get("/{projectId}/checkpoints/{sha}/hunks")
async def checkpoint_hunks(
    projectId: str, sha: str, request: Request, principal: Reader
) -> dict:
    """Files and hunks of one snapshot, for the visual review surface."""
    await _require_checkpoint_project(projectId, request, principal)
    try:
        files = _service.checkpoints.list_hunks(projectId, sha)
    except CheckpointError as e:
        raise _checkpoint_error(e) from e
    return {"projectId": projectId, "sha": sha, "files": files}


@router.post("/{projectId}/checkpoints/{sha}/revert", status_code=status.HTTP_200_OK)
async def checkpoint_revert(
    projectId: str,
    sha: str,
    req: RevertHunksRequest,
    request: Request,
    principal: Writer,
) -> dict:
    """Reverse-apply the selected hunks; records a new checkpoint on success."""
    await _require_checkpoint_project(projectId, request, principal)
    try:
        out = _service.checkpoints.revert_hunks(
            projectId, sha, [(h.path, h.index) for h in req.hunks]
        )
    except CheckpointError as e:
        raise _checkpoint_error(e) from e
    return {"status": "reverted", **out}


@router.post("/{projectId}/checkpoints/{sha}/restore", status_code=status.HTTP_200_OK)
async def checkpoint_restore(
    projectId: str, sha: str, request: Request, principal: Writer
) -> dict:
    """Reset the workspace tree to this snapshot; records a new checkpoint."""
    await _require_checkpoint_project(projectId, request, principal)
    try:
        out = _service.checkpoints.restore(projectId, sha)
    except CheckpointError as e:
        raise _checkpoint_error(e) from e
    return {"status": "restored", **out}
