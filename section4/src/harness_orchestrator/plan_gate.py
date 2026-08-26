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

M17-B4 (MAC2-F2): the decision routes are TEAM-SCOPED now. approve/reject
(and start_run, which drives the same runs) prove the caller's team owns the
plan's project before any state change: same join discipline as
authz.authorize_run (agent_runs.project_id → projects.team_id, frozen table
names only). M13-G5a no-oracle law: a foreign team's plan answers EXACTLY
what an unknown plan id answers — 404 NOT_FOUND with an indistinguishable
envelope — so ids cannot be probed for existence across teams. Role gates
stay first (viewer/guest keep their uniform 403 for every id). M18-C1
posture pass: bare-unit mode no longer silently passes unattributed plans —
without a session factory every decision route denies with 403
AUTHZ_UNAVAILABLE unless ``HARNESS_AUTHZ_TEST_MODE=1`` is explicitly set at
app construction (legacy unit suites set it in ``tests/conftest.py``);
DB-backed runs whose attribution cannot be PROVEN (no project binding,
dangling projects row, store probe failure) deny the same way. Only a
confirmed same-team attribution allows.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from harness_contracts.api import error_body
from harness_contracts.roles import Principal, Role
from harness_contracts.timeutil import db_now
from pydantic import BaseModel, Field

from harness_orchestrator.authz import authz_test_mode
from harness_orchestrator.planner import PlanNotApprovedError
from harness_orchestrator.service import OrchestratorService, PlanAlreadyDecidedError
from harness_orchestrator.state_machine import AgentState, InvalidStateTransitionError  # noqa: E402

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


class PlanRejectedResponse(BaseModel):
    """202 body of /plans/{planId}/reject (persisted AND audited)."""

    status: Literal["rejected"]
    audited: bool
    planId: str
    runId: str


class StartRunResponse(BaseModel):
    """200 body of /runs/{runId}/start (plan-gate variant returns state)."""

    runId: str
    state: str


class _ErrorDetail(BaseModel):
    """M13-G5a: frozen contract error entry for responses={} documentation."""

    code: str
    message: str


class _ErrorEnvelope(BaseModel):
    error: _ErrorDetail


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
                    "ts": db_now(),
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


def _http_plan_not_found() -> HTTPException:
    """The ONE 404 shape for every unknown OR foreign plan (M17-B4 no-oracle).

    Deliberately NOT the legacy PLAN_NOT_FOUND code: that named the resource's
    existence class, and api.py's run routes already collapse foreign probes
    into plain NOT_FOUND. A foreign team probing plan ids gets byte-identical
    answers for "exists elsewhere" and "never existed".
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=error_body("NOT_FOUND", "plan not found"),
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

_PROPOSE_ALLOWED = frozenset({Role.ADMIN, Role.ENGINEER})
_DECIDE_ALLOWED = frozenset({Role.ADMIN, Role.ENGINEER})


@router.post(
    "/plans",
    status_code=status.HTTP_201_CREATED,
    response_model=PlanProposedResponse,
    responses={
        400: {"model": _ErrorEnvelope, "description": "INVALID_STATE"},
        403: {"model": _ErrorEnvelope, "description": "ROLE_DENIED"},
    },
)
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
        # M17-B4: attribute the run to the proposer's team at mint time, so
        # later decisions are team-scoped even in bare-unit mode where there
        # is no projects table to join against.
        run_data["teamId"] = principal.teamId
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


@router.post(
    "/plans/{planId}/approve",
    status_code=status.HTTP_200_OK,
    response_model=PlanActionResponse,
    responses={
        403: {"model": _ErrorEnvelope, "description": "ROLE_DENIED"},
        404: {"model": _ErrorEnvelope, "description": "PLAN_NOT_FOUND"},
        409: {"model": _ErrorEnvelope, "description": "PLAN_ALREADY_DECIDED / INVALID_STATE"},
    },
)
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
    run_id, run_data = await _locate_scoped_plan(service, planId, principal)
    sm = run_data["state_machine"]
    # M19-N1 durable-first window: the mirror row decides, so a replica whose
    # RAM entry was evicted (or which never saw the decision happen) still
    # answers idempotently instead of racing an already-decided plan.
    durable_decision = await service._plan_decision_row(planId)
    if durable_decision is not None and durable_decision["status"] == "approved":
        return {"runId": run_id}
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


@router.post(
    "/plans/{planId}/reject",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PlanRejectedResponse,
    responses={
        403: {"model": _ErrorEnvelope, "description": "ROLE_DENIED"},
        404: {"model": _ErrorEnvelope, "description": "PLAN_NOT_FOUND"},
        409: {"model": _ErrorEnvelope, "description": "PLAN_ALREADY_DECIDED / INVALID_STATE"},
    },
)
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
    run_id, run_data = await _locate_scoped_plan(service, planId, principal)
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


@router.post(
    "/runs/{runId}/start",
    status_code=status.HTTP_200_OK,
    response_model=StartRunResponse,
    responses={
        400: {"model": _ErrorEnvelope, "description": "INVALID_STATE"},
        403: {"model": _ErrorEnvelope, "description": "ROLE_DENIED"},
        409: {"model": _ErrorEnvelope, "description": "PLAN_NOT_APPROVED"},
    },
)
async def start_run(runId: str, principal: RequirePrincipal) -> dict:
    """propose→approve→startRun walk; leaves the run EXECUTING per contract note."""
    service = _require_service()
    try:
        _require_role(principal, _PROPOSE_ALLOWED)
    except _RoleDeniedError as e:
        raise _http_for_role(e) from e
    # M17-B4: scoped resolution — a DB-backed foreign/unknown run id must not
    # be seeded into memory before the team check fires.
    run_data = await _resolve_scoped_run(service, runId, principal)
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
    by /plans), then agent_plans mirror when a DB is wired.

    M17-B4 repair (found while team-scoping): the DB branch previously ran
    ``SELECT id, run_id FROM agent_runs`` — but agent_runs has NO run_id
    column (0001: id, project_id, brief_id, …; 0023's rebuild adds only
    pr_id), so the statement threw on every dialect, the blanket except
    swallowed it, and the fallback answered None forever while looking like a
    legitimate miss. The projection now names real columns and returns
    ``(run_id, project_id)`` so callers can scope on the owning project.
    """
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
                    text(
                        "SELECT r.id AS run_id, r.project_id AS project_id"
                        " FROM agent_runs r"
                        " WHERE r.id IN"
                        " (SELECT p.run_id FROM agent_plans p WHERE p.id = :plan)"
                    ),
                    {"plan": plan_id},
                )
            ).first()
    except Exception:  # noqa: BLE001 — lookup must never crash the route
        logger.exception("agent_plans mirror lookup failed for %s", plan_id)
        return None
    if row is not None:
        run_data = await service._ensure_run(row.run_id, project_id=row.project_id)
        return row.run_id, run_data
    # M19-N1: terminal runs are evicted from ``service.runs``, so the memory
    # scan above can miss a REAL plan whose run reached DONE. The mirror row
    # itself names its owning run — locate through it even when no
    # ``agent_runs`` anchor resolves (plan_gate-minted runs legitimately
    # carry none).
    try:
        async with factory() as db:
            mirror_row = (
                await db.execute(
                    text("SELECT run_id FROM agent_plans WHERE id = :plan"),
                    {"plan": plan_id},
                )
            ).first()
    except Exception:  # noqa: BLE001 — lookup must never crash the route
        logger.exception("agent_plans direct lookup failed for %s", plan_id)
        return None
    if mirror_row is None:
        return None
    run_data = await service._ensure_run(mirror_row.run_id)
    return mirror_row.run_id, run_data


async def _locate_scoped_plan(
    service: OrchestratorService, plan_id: str, principal: Principal
) -> tuple[str, dict[str, Any]]:
    """M17-B4 (MAC2-F2) — _locate_plan plus TEAM OWNERSHIP enforcement.

    The decision routes must never act across the tenant boundary: once the
    owning run resolves, its bound project's team is compared against the
    principal's via :func:`_deny_if_foreign_project` (same 404 either way).
    """
    located = await _locate_plan(service, plan_id)
    if located is None:
        raise _http_plan_not_found()
    await _deny_if_foreign_project(service, located[1], principal)
    return located


def _http_authz_unavailable(reason: str) -> HTTPException:
    """M18-C1 fail-closed denial for an unevaluable ownership check.

    Deliberately NOT a 404: no-oracle law governs probes for resources that
    exist in another team; an UNVERIFIABLE authorization is a wiring or
    data-integrity fault, surfaced as 403 AUTHZ_UNAVAILABLE so deployments
    cannot silently ship with attribution gaps. ``reason`` names the gap.
    """
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=error_body(
            "AUTHZ_UNAVAILABLE", f"authorization unavailable ({reason})"
        ),
    )


async def _deny_unattributed(service: OrchestratorService) -> None:
    """M18-C1: deny when NOTHING can attribute this run to any team.

    Bare-unit mode (no session factory) has no witness at all and used to
    pass silently — now it denies unless ``HARNESS_AUTHZ_TEST_MODE=1`` was
    explicitly set at app construction (legacy unit suites set it in
    tests/conftest.py).
    """
    if getattr(service, "session_factory", None) is None:
        if authz_test_mode():
            return
        raise _http_authz_unavailable("unwired_session_factory")


async def _deny_if_foreign_project(
    service: OrchestratorService, run_data: dict[str, Any], principal: Principal
) -> None:
    """Team-ownership gate for the decision routes — FAIL-CLOSED.

    A CONFIRMED cross-team attribution denies with the SAME 404 an unknown
    plan gets (M13-G5a no-oracle law). An UNPROVABLE attribution denies too
    since M18-C1, with 403 AUTHZ_UNAVAILABLE instead of the old silent pass:
      - bare-unit mode (no session factory): only ``HARNESS_AUTHZ_TEST_MODE=1``
        keeps the legacy allow (unit suites);
      - no project binding on a DB-backed run → 403 AUTHZ_UNAVAILABLE
        (``no_project``);
      - projects row missing for the bound id (deleted/orphaned project) →
        403 AUTHZ_UNAVAILABLE (``dangling_project``);
      - store probe failure → logged, then 403 AUTHZ_UNAVAILABLE
        (``store_probe_failed``) — a degraded store must never widen access.

    Attribution witnesses, checked in order:
      1. ``projects.team_id`` via agent_runs.project_id — the DB join, same
         discipline as authz.authorize_run (frozen table names only). A
         dangling projects row denies uniformly (no stamp-based bypass).
      2. ``run_data["teamId"]`` — stamped by propose_plan at mint time;
         consulted ONLY when the run carries no project binding at all
         (plan_gate-proposed runs legitimately don't).
    """
    factory = getattr(service, "session_factory", None)
    if factory is None:
        # Bare-unit mode: the mint-time stamp is the only possible witness,
        # and only behind the explicit test flag.
        await _deny_unattributed(service)
        if run_data.get("teamId") not in (None, principal.teamId):
            raise _http_plan_not_found()
        return

    from sqlalchemy import text

    project_id: str = run_data.get("projectId") or ""
    if project_id:
        try:
            async with factory() as db:
                row = (
                    await db.execute(
                        text("SELECT team_id FROM projects WHERE id = :p"),
                        {"p": project_id},
                    )
                ).first()
        except Exception:  # noqa: BLE001 — probe must never crash the route
            logger.exception(
                "plan-gate ownership probe failed for project %s", project_id
            )
            # M18-C1 fail-closed: a store problem used to degrade THIS request
            # to "no ownership information" and pass; it now denies loudly.
            raise _http_authz_unavailable("store_probe_failed") from None
        if row is None:
            # Dangling binding: the store says this project does not exist.
            # Nothing may resurrect ownership of an unresolvable binding
            # (uniform with authz.authorize_run — no stamp-based bypass).
            raise _http_authz_unavailable("dangling_project")
        if row.team_id == principal.teamId:
            return
        raise _http_plan_not_found()  # confirmed foreign team (no-oracle)

    # No project binding: the mint-time stamp is the last remaining witness
    # (plan_gate-proposed runs legitimately carry no projectId).
    if run_data.get("teamId") != principal.teamId:
        reason = "no_project"
        if run_data.get("teamId") is not None:
            # A stamp naming ANOTHER team is a confirmed cross-team
            # attribution: answer the no-oracle 404, not a wiring fault.
            raise _http_plan_not_found()
        raise _http_authz_unavailable(reason)


async def _resolve_scoped_run(
    service: OrchestratorService, run_id: str, principal: Principal
) -> dict[str, Any]:
    """start_run's twin of _locate_scoped_plan: resolve BY RUN ID with team
    enforcement. Unlike _ensure_run it never SEEDS an unattributed DB-backed
    run into memory — an unknown-or-foreign id must not leave a writable
    state machine behind (the pre-fix mint-then-authorize shape)."""

    async def _scoped_ensure() -> dict[str, Any]:
        if run_id in service.runs:
            return service.runs[run_id]
        factory = getattr(service, "session_factory", None)
        row = None
        if factory is not None:
            from sqlalchemy import text

            async with factory() as db:
                row = (
                    await db.execute(
                        text(
                            "SELECT r.id AS run_id, r.project_id AS project_id"
                            " FROM agent_runs r WHERE r.id = :r"
                        ),
                        {"r": run_id},
                    )
                ).first()
        if row is None:
            # Genuinely unknown (or bare unit mode): historical behavior.
            return await service._ensure_run(run_id)
        return await service._ensure_run(row.run_id, project_id=row.project_id)

    run_data = await _scoped_ensure()
    await _deny_if_foreign_project(service, run_data, principal)
    return run_data
