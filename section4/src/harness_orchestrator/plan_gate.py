"""W1-C1 — Plan-gate orchestrator API (C-005 backend half).

Own APIRouter implementing the verbatim W1-C1↔W1-C2 pair contract:

    POST /api/v1/orchestrator/plans            {goal}   → 201 {planId, plan}
    POST /api/v1/orchestrator/plans/{id}/approve        → 200 {runId}
    POST /api/v1/orchestrator/plans/{id}/reject         → 202 (audited)
    errors: error_body() envelope

State transitions go ONLY through state_machine vocabulary (AgentStateMachine
methods; that file is W1-D1's — read, never write). Persistence reuses the
existing OrchestratorService seams (_persist_plan / _persist_plan_status) and
the S1 event bus via the service's own _emit bridge, so plan events land in
agent_events exactly like the rest of Section 4.

Audit seam: W1-A2's audit helper does not exist yet in a
harness_contracts-compliant location, so this module defines a local thin
wrapper (``_audit_emit``) writing the frozen ``audit_log`` table directly.
FLAGGED FOR ATLAS: swap to W1-A2's symbol at merge coordination with W1-A2.

Wire models are camelCase per api-conventions (planId, runId, …).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from harness_contracts.api import error_body
from harness_contracts.roles import Principal, Role
from pydantic import BaseModel, Field

from harness_orchestrator.planner import PlanNotApprovedError
from harness_orchestrator.service import OrchestratorService, PlanAlreadyDecidedError
from harness_orchestrator.state_machine import AgentState, InvalidStateTransitionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["plan-gate"])

# M12-F2: frozen error code for a lost optimistic-concurrency race on a plan
# decision. Both losers (second approver / second rejector) map here.
PLAN_ALREADY_DECIDED_CODE = "PLAN_ALREADY_DECIDED"


def _http_plan_already_decided(detail_message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=error_body(PLAN_ALREADY_DECIDED_CODE, detail_message),
    )


def get_plan_gate_service() -> OrchestratorService:
    """Same deploy-time wiring seam as api.py: swapped by configure_plan_gate()."""
    return _require_service()


_service: OrchestratorService | None = None


def configure_plan_gate(service: OrchestratorService) -> None:
    """Deploy-time wiring (Section 10): share the orchestrator's service."""
    global _service
    _service = service


def _require_service() -> OrchestratorService:
    if _service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_body("SERVICE_NOT_CONFIGURED", "plan gate not wired"),
        )
    return _service


# ---------------------------------------------------------------------------
# Wire models (camelCase, mirrored field-by-field by W1-C2's TS client)
# ---------------------------------------------------------------------------


class ProposePlanGateRequest(BaseModel):
    goal: str = Field(min_length=1)


class PlanOut(BaseModel):
    """Mirror of AgentPlan on the wire (camelCase)."""

    goal: str
    filesLikelyToChange: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    estimatedCostUsd: float = 0.0
    forbiddenPaths: list[str] = Field(default_factory=list)
    expectedResult: str


class PlanProposedResponse(BaseModel):
    planId: str
    plan: PlanOut


class PlanActionResponse(BaseModel):
    runId: str


# ---------------------------------------------------------------------------
# Audit seam (local thin wrapper — see FLAG FOR ATLAS in module docstring)
# ---------------------------------------------------------------------------

AUDIT_ACTION_PLAN_REJECTED = "plan.rejected"  # frozen append-only action name


async def _audit_emit(
    session_factory,
    *,
    action: str,
    actor_member_id: str | None,
    target_type: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Thin wrapper over the audit seam. Best-effort: audit failure must never
    fail the orchestration action itself (matches S4's persistence posture).

    When the session factory is None (bare unit mode) there is no audit store;
    callers record intent instead (see reject endpoint's audited flag).
    """
    from sqlalchemy import text

    payload_json = "{}" if not detail else json.dumps(detail, default=str)
    try:
        async with session_factory() as db:
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action,"
                    " target_type, target_id, detail, ts)"
                    " VALUES (:actor, 'human', :action, :ttype, :tid, :detail, :ts)"
                ),
                {
                    "actor": actor_member_id,
                    "action": action,
                    "ttype": target_type,
                    "tid": target_id,
                    "detail": payload_json,
                    "ts": datetime.now(UTC),
                },
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — audit must never break orchestration
        logger.exception("audit emit failed for %s/%s", target_type, target_id)


# ---------------------------------------------------------------------------
# Role gates (frozen contracts vocabulary only)
# ---------------------------------------------------------------------------


async def _plan_gate_principal(request: Request) -> Principal:
    """Resolve identity through the shared auth dependency (no wiring = 503)."""
    dep = getattr(request.app.state, "get_principal", None)
    if dep is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_body("AUTH_NOT_CONFIGURED", "auth not wired"),
        )
    return await dep(request)


RequirePrincipal = Annotated[Principal, Depends(_plan_gate_principal)]


class _RoleDeniedError(Exception):
    """Internal: mirrors authz.RoleDeniedError without importing it."""


def _require_role(principal: Principal, allowed: frozenset[Role]) -> None:
    if principal.role not in allowed:
        raise _RoleDeniedError()


def _http_for_role(exc: _RoleDeniedError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=error_body("ROLE_DENIED", "role may not perform this action"),
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

_PROPOSE_ALLOWED = frozenset({Role.ADMIN, Role.ENGINEER})
_DECIDE_ALLOWED = frozenset({Role.ADMIN, Role.ENGINEER})


@router.post("/plans", status_code=status.HTTP_201_CREATED)
async def propose_plan(req: ProposePlanGateRequest, principal: RequirePrincipal) -> dict:
    """{goal} → 201 {planId, plan}. Mints a fresh run + plan, leaves the run at
    AWAITING_PLAN_APPROVAL via sm.plan_proposed() (state_machine vocabulary)."""
    service = _require_service()
    try:
        _require_role(principal, _PROPOSE_ALLOWED)
    except _RoleDeniedError as e:
        raise _http_for_role(e) from e
    try:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        run_data = await service._ensure_run(run_id)
        sm = run_data["state_machine"]
        run_data["goal"] = req.goal
        sm.start_planning()
        plan_id, plan = service.planner.create_plan(goal=req.goal)
        run_data["plan_id"] = plan_id
        await service._persist_plan(run_id, plan_id, plan, status="proposed")
        sm.plan_proposed(plan_id)
        return PlanProposedResponse(planId=plan_id, plan=PlanOut(**plan.model_dump())).model_dump()
    except InvalidStateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_body("INVALID_STATE", str(e)),
        ) from e


@router.post("/plans/{planId}/approve", status_code=status.HTTP_200_OK)
async def approve_plan(planId: str, principal: RequirePrincipal) -> dict:
    """`/plans/{id}/approve` → 200 {runId}. admin/engineer only. Idempotent.

    M12-F2: the durable decision is a compare-and-swap on agent_plans.version.
    The CAS runs BEFORE any in-memory mutation, so a loser of the race leaves
    planner/state-machine/memory untouched and gets 409 PLAN_ALREADY_DECIDED
    instead of both callers believing they won (last-write-wins eliminated).
    A sequential re-approve still short-circuits to the same runId.
    """
    service = _require_service()
    try:
        _require_role(principal, _DECIDE_ALLOWED)
    except _RoleDeniedError as e:
        raise _http_for_role(e) from e
    located = await _locate_plan(service, planId)
    if located is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_body("PLAN_NOT_FOUND", f"Plan '{planId}' not found."),
        )
    run_id, run_data = located
    sm = run_data["state_machine"]
    already_approved = bool(sm.plan_approved)
    try:
        if not already_approved:
            # Durable first: exactly one concurrent approve can flip version
            # 0 → 1; everyone else loses and must not touch memory.
            await service._persist_plan_status(
                run_id, planId, "approved", expected_version=0
            )
            service.planner.approve_plan(planId)
            sm.approve_plan(planId, principal.userId)
        return {"runId": run_id}
    except PlanAlreadyDecidedError as e:
        # Another principal's approve or reject landed first — this caller
        # lost the race. Memory deliberately left untouched.
        raise _http_plan_already_decided(str(e)) from e
    except InvalidStateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_body("INVALID_STATE", str(e)),
        ) from e


@router.post("/plans/{planId}/reject", status_code=status.HTTP_202_ACCEPTED)
async def reject_plan(planId: str, principal: RequirePrincipal) -> dict:
    """`/plans/{id}/reject` → 202. Persisted AND audited. admin/engineer only.

    M12-F2: same CAS-before-memory discipline as approve. The decision window
    is AWAITING_PLAN_APPROVAL — a plan that already moved (approved, rejected,
    executing) rejects with 409 PLAN_ALREADY_DECIDED, so reject-after-reject
    and mixed approve/reject races resolve to exactly one winner.
    """
    service = _require_service()
    try:
        _require_role(principal, _DECIDE_ALLOWED)
    except _RoleDeniedError as e:
        raise _http_for_role(e) from e
    located = await _locate_plan(service, planId)
    if located is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_body("PLAN_NOT_FOUND", f"Plan '{planId}' not found."),
        )
    run_id, run_data = located
    sm = run_data["state_machine"]
    if sm.state is not AgentState.AWAITING_PLAN_APPROVAL:
        raise _http_plan_already_decided(
            f"Plan '{planId}' was already decided"
            f" (run state '{sm.state.value}'); rejection window closed."
        )
    # M12-F2: the durable mirror is the tiebreaker. A restart-rehydrated run
    # boots IDLE, so the in-memory window above cannot be trusted alone — a
    # plan whose persisted status is still 'proposed' stays decidable.
    # Stores whose agent_plans predates 0013 (no version column yet) simply
    # yield no window information here; the CAS below degrades the same way,
    # matching the house "participates when the column exists" posture.
    factory = getattr(service, "session_factory", None)
    if factory is not None:
        from sqlalchemy import text

        row = None
        try:
            async with factory() as db:
                row = (
                    await db.execute(
                        text("SELECT status, version FROM agent_plans WHERE id = :p"),
                        {"p": planId},
                    )
                ).first()
        except Exception:  # noqa: BLE001 — window probe must never crash the route
            logger.exception("plan-decision window probe failed for %s", planId)
        if row is not None and (row.status != "proposed" or row.version != 0):
            raise _http_plan_already_decided(
                f"Plan '{planId}' was already decided"
                f" (persisted status '{row.status}'); rejection window closed."
            )
    try:
        # Durable first: exactly one concurrent decision can move version
        # 0 → 1; the loser sees zero rows updated and stops here.
        await service._persist_plan_status(run_id, planId, "rejected", expected_version=0)
        service.planner.reject_plan(planId)
        sm.reject_plan(planId, principal.userId)  # → IDLE per state machine
        await _audit_emit(
            getattr(service, "session_factory", None),
            action=AUDIT_ACTION_PLAN_REJECTED,
            actor_member_id=principal.userId,
            target_type="plan",
            target_id=planId,
            detail={"runId": run_id},
        )
    except PlanAlreadyDecidedError as e:
        # Winner kept its decision; memory untouched by this loser.
        raise _http_plan_already_decided(str(e)) from e
    except InvalidStateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_body("INVALID_STATE", str(e)),
        ) from e
    return {
        "status": "rejected",
        "audited": True,
        "planId": planId,
        "runId": run_id,
    }


@router.post("/runs/{runId}/start", status_code=status.HTTP_200_OK)
async def start_run(runId: str, principal: RequirePrincipal) -> dict:
    """propose→approve→startRun walk; leaves the run EXECUTING per contract note."""
    service = _require_service()
    try:
        _require_role(principal, _PROPOSE_ALLOWED)
    except _RoleDeniedError as e:
        raise _http_for_role(e) from e
    run_data = await service._ensure_run(runId)
    sm = run_data["state_machine"]
    if sm.state is AgentState.EXECUTING:
        return {"runId": runId, "state": str(AgentState.EXECUTING.value)}
    try:
        await service.start_execution(runId)
    except PlanNotApprovedError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_body("PLAN_NOT_APPROVED", str(e)),
        ) from e
    except InvalidStateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_body("INVALID_STATE", str(e)),
        ) from e
    return {"runId": runId, "state": str(sm.state.value)}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _locate_plan(
    service: OrchestratorService, plan_id: str
) -> tuple[str, dict[str, Any]] | None:
    """Find the run owning plan_id. In-memory registry first (covers runs minted
    by /plans), then agent_plans mirror when a DB is wired."""
    for run_id, run_data in service.runs.items():
        if run_data.get("plan_id") == plan_id:
            return run_id, run_data
    factory = getattr(service, "session_factory", None)
    if factory is None:
        return None
    from sqlalchemy import text

    try:
        async with factory() as db:
            row = (
                await db.execute(
                    text("SELECT id, run_id FROM agent_runs WHERE id IN"
                         " (SELECT run_id FROM agent_plans WHERE id = :p)"),
                    {"p": plan_id},
                )
            ).first()
    except Exception:  # noqa: BLE001 — lookup must never crash the route
        return None
    if row is None:
        return None
    run_data = await service._ensure_run(row.run_id, project_id=row.id)
    return row.run_id, run_data
