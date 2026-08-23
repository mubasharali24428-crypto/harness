from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from harness_contracts.roles import Principal
from pydantic import BaseModel

from harness_orchestrator.authz import (
    OwnershipDeniedError,
    RoleDeniedError,
    authorize_run,
    require_role,
)
from harness_orchestrator.planner import PlanNotApprovedError
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import InvalidStateTransitionError

router = APIRouter(prefix="/api/v1/orchestrator", tags=["orchestrator"])
_service = OrchestratorService()


def get_orchestrator_service() -> OrchestratorService:
    return _service


def configure_orchestrator(service: OrchestratorService) -> None:
    """Deploy-time wiring (Section 10): swap in a service with bus + DB factory."""
    global _service
    _service = service


async def get_principal(request: Request) -> Principal:
    """Auth resolution via app.state.get_principal, wired by the deploy assembly.

    Mirrors api-conventions.md §Auth context: every request must resolve to a
    principal BEFORE any handler logic runs. No wiring = hard failure, never an
    open endpoint.
    """
    dep = getattr(request.app.state, "get_principal", None)
    if dep is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "AUTH_NOT_CONFIGURED", "message": "auth not wired"}},
        )
    return await dep(request)


RequirePrincipal = Annotated[Principal, Depends(get_principal)]


def _authz_dep(action):
    """D5 (P0.5): one dependency = identity + team ownership + role permission.

    ``action`` is a frozen TeamScopedAction ("read" for GETs, "write" for run
    control, "approve" for plan approve/reject). The DB-backed ownership check
    is a no-op when no session factory is wired (bare unit-test mode), matching
    how rehydration already degrades without a DB.
    """

    async def guard(runId: str, principal: RequirePrincipal) -> Principal:
        try:
            await authorize_run(_service, runId, principal)
            require_role(principal, action)
        except OwnershipDeniedError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "TEAM_OWNERSHIP",
                        "message": "run does not belong to your team",
                    }
                },
            ) from e
        except RoleDeniedError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "ROLE_DENIED", "message": f"role may not {action}"}},
            ) from e
        return principal

    return guard


RunReader = Annotated[Principal, Depends(_authz_dep("read"))]
RunWriter = Annotated[Principal, Depends(_authz_dep("write"))]
PlanApprover = Annotated[Principal, Depends(_authz_dep("approve"))]


class PlanActionRequest(BaseModel):
    planId: str


class HumanEditRequest(BaseModel):
    path: str
    content: str


class DoneSignalRequest(BaseModel):
    diffText: str = ""


class ProposePlanRequest(BaseModel):
    goal: str | None = None
    projectId: str | None = None


async def _project_belongs_to_team(project_id: str, principal: Principal) -> bool:
    """True when the project row exists AND is owned by the principal's team."""
    factory = getattr(_service, "session_factory", None)
    if project_id is None or factory is None:
        return True  # unbindable / bare unit mode: DB-backed check not applicable
    from sqlalchemy import text

    async with factory() as db:
        row = (
            await db.execute(
                text("SELECT team_id FROM projects WHERE id = :p"), {"p": project_id}
            )
        ).first()
    if row is None:
        return False  # unknown projectId: never let a propose mint it into this team
    return row.team_id == principal.teamId


@router.post("/runs/{runId}/plan", status_code=status.HTTP_201_CREATED)
async def propose_plan(
    runId: str, req: ProposePlanRequest | None = None, principal: RequirePrincipal = None
) -> dict[str, str]:
    try:
        goal = req.goal if req else None
        project_id = req.projectId if req else None
        # D5: proposing BINDS the run to projectId — reject another team's project.
        if project_id is not None and not await _project_belongs_to_team(project_id, principal):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "TEAM_OWNERSHIP",
                        "message": "project does not belong to your team",
                    }
                },
            )
        plan_id = await _service.propose_plan(runId, goal=goal, project_id=project_id)
        return {"runId": runId, "planId": plan_id}
    except InvalidStateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_STATE", "message": str(e)}},
        ) from e


@router.post("/runs/{runId}/plan/approve", status_code=status.HTTP_200_OK)
async def approve_plan(runId: str, req: PlanActionRequest, actor: PlanApprover) -> dict:
    # Identity comes from the session token, NEVER from the request body —
    # forgeable approver IDs would poison the audit trail.
    await _service.approve_plan(runId, req.planId, actor.userId)
    return {"runId": runId, "planId": req.planId, "status": "approved"}


@router.post("/runs/{runId}/plan/reject", status_code=status.HTTP_200_OK)
async def reject_plan(runId: str, req: PlanActionRequest, actor: PlanApprover) -> dict:
    try:
        await _service.reject_plan(runId, req.planId, actor.userId)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "PLAN_MISMATCH", "message": str(e)}},
        ) from e
    return {"runId": runId, "planId": req.planId, "status": "rejected"}


@router.post("/runs/{runId}/start", status_code=status.HTTP_200_OK)
async def start_execution(runId: str, actor: RunWriter) -> dict[str, str]:
    try:
        await _service.start_execution(runId)
        return {"runId": runId, "status": "executing"}
    except PlanNotApprovedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "PLAN_NOT_APPROVED", "message": str(e)}},
        ) from e
    except InvalidStateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_STATE", "message": str(e)}},
        ) from e


@router.post("/runs/{runId}/pause", status_code=status.HTTP_200_OK)
async def pause_agent(runId: str, actor: RunWriter) -> dict[str, str]:
    await _service.pause_agent(runId)
    return {"runId": runId, "status": "paused"}


@router.post("/runs/{runId}/resume", status_code=status.HTTP_200_OK)
async def resume_agent(runId: str, actor: RunWriter) -> dict[str, str]:
    await _service.resume_agent(runId)
    return {"runId": runId, "status": "executing"}


@router.post("/runs/{runId}/stop", status_code=status.HTTP_200_OK)
async def stop_agent(runId: str, actor: RunWriter) -> dict[str, str]:
    await _service.stop_agent(runId)
    return {"runId": runId, "status": "stopped"}


@router.post("/runs/{runId}/human-edit", status_code=status.HTTP_200_OK)
async def submit_human_edit(
    runId: str, req: HumanEditRequest, actor: RunWriter
) -> dict[str, str]:
    await _service.submit_human_edit(runId, req.path, req.content)
    return {"runId": runId, "status": "paused_on_human_edit", "path": req.path}


@router.post("/runs/{runId}/done-signal", status_code=status.HTTP_200_OK)
async def signal_done(runId: str, req: DoneSignalRequest, actor: RunReader) -> dict:
    findings = await _service.signal_done(runId, diff_text=req.diffText)
    dump = findings.model_dump(by_alias=True)
    return {"runId": runId, "findings": dump}


@router.get("/runs/{runId}/engine", status_code=status.HTTP_200_OK)
async def get_engine_result(runId: str, reader: RunReader) -> dict[str, Any]:
    return await _service.get_engine_result(runId)


@router.get("/runs/{runId}/status", status_code=status.HTTP_200_OK)
async def get_run_status(runId: str, reader: RunReader) -> dict[str, Any]:
    return await _service.get_status(runId)


@router.get("/runs/{runId}/plan", status_code=status.HTTP_200_OK)
async def get_plan(runId: str, reader: RunReader) -> dict[str, Any]:
    try:
        plan = await _service.get_plan(runId)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "PLAN_NOT_FOUND", "message": str(e)}},
        ) from e
    return plan.model_dump()
