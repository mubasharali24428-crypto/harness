import bisect
import logging
import os
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from harness_contracts.limits import (
    DONE_DIFF_TEXT_MAX,
    HUMAN_EDIT_CONTENT_MAX,
    HUMAN_EDIT_PATH_MAX,
    PLAN_ACTION_PLAN_ID_MAX,
    PROPOSE_GOAL_MAX,
    PROPOSE_PROJECT_ID_MAX,
)
from harness_contracts.roles import Principal
from pydantic import BaseModel, Field
from starlette.datastructures import Headers, MutableHeaders
from starlette.routing import Match

from harness_orchestrator import domain_metrics
from harness_orchestrator.authz import (
    AUDIT_ACTION_CANCEL_CONFIRMED,
    AUDIT_ACTION_CANCEL_REQUESTED,
    AUDIT_ACTION_RUN_PAUSED,
    AUDIT_ACTION_RUN_RESUMED,
    AUDIT_ACTION_RUN_STARTED,
    AUDIT_ACTION_RUN_STOPPED,
    AuthzUnavailableError,
    OwnershipDeniedError,
    RoleDeniedError,
    authorize_run,
    emit_run_lifecycle,
    require_role,
)
from harness_orchestrator.logging_utils import (  # noqa: E402
    REQUEST_ID_HEADER,
    reset_request_id,
    set_request_id,
)
from harness_orchestrator.model_client import ModelsServiceRouter  # noqa: E402
from harness_orchestrator.planner import PlanNotApprovedError  # noqa: E402
from harness_orchestrator.service import (  # noqa: E402
    OrchestratorService,
    PlanAlreadyDecidedError,
    RunNotFoundError,
)  # noqa: E402
from harness_orchestrator.signals import (  # noqa: E402
    DoneSignalRejectedError,
    EmptyDiffTextError,
    RunNotLiveForDoneSignalError,
)
from harness_orchestrator.state_machine import InvalidStateTransitionError  # noqa: E402

router = APIRouter(prefix="/api/v1/orchestrator", tags=["orchestrator"])
_service = OrchestratorService()

# --------------------------------------------------------------------------
# Wave 8 node A: observability (request ids, access log, /metrics).
# --------------------------------------------------------------------------

_obs_logger = logging.getLogger("harness.orchestrator.request")

_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# Synthetic-probe / self-scrape route templates excluded from the availability
# SLI (docs/runbooks/slo-and-capacity.md: never treat /health alone as
# availability). Kept identical to the assembly copy in deploy/runners/observability.
_PROBE_PATHS = frozenset({"/health", "/health?deep=1", "/ready", "/metrics"})


class MetricsCollector:
    """Minimal Prometheus text-format collector (no prometheus_client dep).

    Counters: ``http_requests_total{method,path,status}``.
    Histogram: ``http_request_duration_seconds`` (method/path labels,
    standard buckets). Raw per-bucket counts are stored and cumulative
    bucket values are computed at render time. All mutation happens under a
    threading.Lock so worker threads + request tasks can't tear a series.

    IMPORTANT: ``path`` labels MUST be route TEMPLATES (e.g.
    ``/api/v1/orchestrator/runs/{runId}/status``), never raw ids — otherwise
    one run id per label value explodes cardinality without bound.
    """

    def __init__(self, buckets: tuple[float, ...] = _DEFAULT_BUCKETS) -> None:
        self._lock = threading.Lock()
        self._buckets = buckets
        self._counts: dict[tuple[str, str, str], int] = {}
        # (method, path) -> per-bucket observation counts
        self._hist_raw: dict[tuple[str, str], list[int]] = {}
        self._hist_sum: dict[tuple[str, str], float] = {}
        # W17 Stage-2: availability-eligible rollup (ok / error). The raw
        # http_requests_total{status} is present, but the API-availability SLI
        # must be measured on the ELIGIBLE set: caller 4xx, synthetic probes and a
        # declared maintenance window are excluded (slo-and-capacity.md). Not a
        # substitute for the owner's decision to adopt a threshold on it.
        self._eligible = {"ok": 0, "error": 0}

    @staticmethod
    def _classify_eligible(path: str, status_code: int | str) -> str | None:
        """'ok' | 'error' | None (None = not eligible, excluded)."""
        if path in _PROBE_PATHS:
            return None
        if os.environ.get("HARNESS_MAINTENANCE_MODE", "").strip().lower() in {"1", "true", "on"}:
            return None
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            return None
        if 200 <= code < 400:
            return "ok"
        if 400 <= code < 500:
            return None  # caller error
        if code >= 500:
            return "error"
        return None  # 1xx

    def observe(
        self, *, method: str, path: str, status_code: int | str, duration_s: float
    ) -> None:
        ckey = (method, path, str(status_code))
        hkey = (method, path)
        idx = bisect.bisect_right(self._buckets, duration_s)
        outcome = self._classify_eligible(path, status_code)
        with self._lock:
            self._counts[ckey] = self._counts.get(ckey, 0) + 1
            raw = self._hist_raw.setdefault(hkey, [0] * len(self._buckets))
            if 0 <= idx < len(raw):
                raw[idx] += 1
            self._hist_sum[hkey] = self._hist_sum.get(hkey, 0.0) + duration_s
            if outcome is not None:
                self._eligible[outcome] += 1

    def render(self) -> str:
        """Prometheus text format (version 0.0.4), deterministic ordering."""
        with self._lock:
            counters = sorted(self._counts.items())
            hist_raw = {k: list(v) for k, v in self._hist_raw.items()}
            hist_sum = dict(self._hist_sum)
            eligible = dict(self._eligible)

        lines = [
            "# HELP http_requests_total Total HTTP requests handled.",
            "# TYPE http_requests_total counter",
        ]
        for (method, path, status_label), count in counters:
            lines.append(
                f'http_requests_total{{method="{_escape_label(method)}",'
                f'path="{_escape_label(path)}",status="{_escape_label(status_label)}"}}'
                f" {count}"
            )

        # Availability SLI denominator; both outcomes always rendered so a
        # PromQL ok/(ok+error) is stable at zero traffic.
        lines.append(
            "# HELP harness_eligible_requests_total Availability-eligible HTTP "
            "requests, excluding caller 4xx, synthetic probes and declared maintenance."
        )
        lines.append("# TYPE harness_eligible_requests_total counter")
        lines.append(f'harness_eligible_requests_total{{outcome="ok"}} {eligible["ok"]}')
        lines.append(f'harness_eligible_requests_total{{outcome="error"}} {eligible["error"]}')

        lines.append(
            "# HELP http_request_duration_seconds "
            "HTTP request duration in seconds."
        )
        lines.append("# TYPE http_request_duration_seconds histogram")
        for (method, path), raw in sorted(hist_raw.items()):
            labels = f'method="{_escape_label(method)}",path="{_escape_label(path)}"'
            cumulative = 0
            for bucket, n in zip(self._buckets, raw, strict=True):
                cumulative += n
                lines.append(
                    f"http_request_duration_seconds_bucket{{{labels},"
                    f'le="{bucket:g}"}} {cumulative}'
                )
            total = sum(raw)
            lines.append(
                f'http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {total}'
            )
            lines.append(
                f"http_request_duration_seconds_sum{{{labels}}} "
                f"{hist_sum.get((method, path), 0.0)!r}"
            )
            lines.append(f"http_request_duration_seconds_count{{{labels}}} {total}")
        return "\n".join(lines) + "\n" + domain_metrics.render()


METRICS = MetricsCollector()


def _route_template(scope: dict) -> str:
    """Label-safe path: matched route TEMPLATE or the constant "unmatched".

    FastAPI publishes the matched APIRoute as scope["route"]; its .path is the
    full template including the router prefix. Raw request paths are NEVER
    used as labels (unbounded cardinality: one run id == one new series).
    """
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return str(template)
    # Defensive fallback for routers that don't publish scope["route"]:
    # re-match against the app's routes.
    from_app_routes = getattr(scope.get("app"), "routes", None) or ()
    for candidate in from_app_routes:
        try:
            match, _ = candidate.matches(scope)
        except Exception:  # noqa: BLE001 — telemetry must never raise
            continue
        if match is Match.FULL:
            return str(getattr(candidate, "path", "unmatched"))
    return "unmatched"


class RequestContextMiddleware:
    """Pure-ASGI middleware: X-Request-ID propagation + access log + metrics.

    Deliberately OUTSIDE auth so every response carries X-Request-ID and
    every request produces exactly one INFO JSON line — including 404s,
    401s and handler crashes. Inbound ``X-Request-ID`` is echoed back;
    otherwise a 12-hex-char id is minted.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":  # lifespan/websocket pass through untouched
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(REQUEST_ID_HEADER)
        rid = inbound or uuid.uuid4().hex[:12]
        token = set_request_id(rid)
        started = time.perf_counter()
        seen_status = 500

        async def send_wrapped(message) -> None:
            nonlocal seen_status
            if message["type"] == "http.response.start":
                seen_status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            dur_ms = round((time.perf_counter() - started) * 1000.0, 2)
            path_label = _route_template(scope)
            method = scope.get("method", "-")
            METRICS.observe(
                method=method,
                path=path_label,
                status_code=seen_status,
                duration_s=dur_ms / 1000.0,
            )
            _obs_logger.info(
                "http request",
                extra={
                    "request_id": rid,
                    "method": method,
                    "path": path_label,
                    "status_code": seen_status,
                    "duration_ms": dur_ms,
                },
            )
            reset_request_id(token)


@router.get("/metrics", include_in_schema=False)
async def orchestrator_metrics() -> Response:
    """Prometheus scrape target — public like /health (no principal required)."""
    return Response(
        content=METRICS.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


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


# (principal, run id, "METHOD route") of the collab request in flight, set by the
# audited guard so denials raised later (guard or collab layer) can be attributed.
_denial_ctx: ContextVar[tuple[Principal, str, str] | None] = ContextVar("_denial_ctx", default=None)


async def _audit_guard_denial(audit: bool, code: str, http_status: int) -> None:
    ctx = _denial_ctx.get()
    if audit and ctx is not None:
        from harness_orchestrator import collab as _c

        await _c.audit_denial(
            getattr(_service, "session_factory", None), ctx[0], ctx[1], ctx[2], code, http_status
        )


def _authz_dep(action, audit=False):
    """D5 (P0.5): one dependency = identity + team ownership + role permission.

    ``action`` is a frozen TeamScopedAction ("read" for GETs, "write" for run
    control, "approve" for plan approve/reject). M18-C1: the DB-backed
    ownership check is fail-closed — an unevaluable check (no session factory
    outside test mode, no project binding, dangling projects row) denies with
    403 AUTHZ_UNAVAILABLE instead of silently allowing.
    """

    async def guard(request: Request, runId: str, principal: RequirePrincipal) -> Principal:
        try:
            # Role gate FIRST (M18-C1): viewer/guest keep their uniform
            # ROLE_DENIED for every id, even when the ownership half would
            # answer AUTHZ_UNAVAILABLE — no new oracle for low-priv roles.
            if audit:
                route = request.scope.get("route")
                path = getattr(route, "path", request.url.path)
                _denial_ctx.set((principal, runId, f"{request.method} {path}"))
            require_role(principal, action)
            await authorize_run(_service, runId, principal)
        except AuthzUnavailableError as e:
            await _audit_guard_denial(audit, "AUTHZ_UNAVAILABLE", 403)
            # M18-C1 fail-closed: "couldn't prove ownership" is an explicit,
            # audited denial — distinct from both 404 (unknown/foreign) and
            # ROLE_DENIED so operators can see the wiring/attribution gap.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "AUTHZ_UNAVAILABLE",
                        "message": f"authorization unavailable ({e.reason})",
                    }
                },
            ) from e
        except OwnershipDeniedError as e:
            await _audit_guard_denial(audit, "NOT_FOUND", 404)
            # M13-G5a no-oracle law: a foreign team's run answers EXACTLY what
            # an unknown run id answers — 404 NOT_FOUND, same envelope. A 403
            # here would confirm the run exists across the tenant boundary.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "run not found",
                    }
                },
            ) from e
        except RoleDeniedError as e:
            await _audit_guard_denial(audit, "ROLE_DENIED", 403)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "ROLE_DENIED", "message": f"role may not {action}"}},
            ) from e
        return principal

    return guard


RunReader = Annotated[Principal, Depends(_authz_dep("read"))]
# Control verbs (start/pause/resume/stop/human-edit, plan approve/reject) audit their
# denials like the collaboration routes do (part 11.010): a refused stop is evidence.
RunWriter = Annotated[Principal, Depends(_authz_dep("write", audit=True))]
PlanApprover = Annotated[Principal, Depends(_authz_dep("approve", audit=True))]


class PlanActionRequest(BaseModel):
    # M12-G3: bounded ids (limits are additive; see harness_identity.limits).
    planId: str = Field(max_length=PLAN_ACTION_PLAN_ID_MAX)


# ---------------------------------------------------------------------------
# M13-G5a OpenAPI typing islands — concrete response schemas for the
# highest-traffic orchestrator routes.
# ---------------------------------------------------------------------------


class RunIdStatusResponse(BaseModel):
    """Shared shape of the run-control verbs (start/pause/resume/stop/...)."""

    runId: str
    status: str


class RunStateResponse(BaseModel):
    """Shape of plan-gate startRun and the resume verb (state, not status)."""

    runId: str
    state: str


class ProposePlanResponse(BaseModel):
    runId: str
    planId: str


class PlanDecisionResponse(BaseModel):
    runId: str
    planId: str
    status: Literal["approved", "rejected"]


class ReviewFindingsOut(BaseModel):
    """Mirror of harness_orchestrator.reviewer.ReviewFindings (camelCase wire)."""

    passed: bool
    modelUsed: str | None = None
    syntaxOk: bool = True
    conflictsDetected: bool = False
    briefAligned: bool = True
    summary: str = ""
    issues: list[str] = Field(default_factory=list)


class DoneSignalResponse(BaseModel):
    runId: str
    findings: ReviewFindingsOut


class RunStatusResponse(BaseModel):
    """M13-G5a typing island: the run aggregate read (camelCase wire shape)."""

    runId: str
    state: str
    planId: str | None = None
    planApproved: bool = False
    isHumanEditing: bool = False
    executing: bool = False
    engineMode: str | None = None
    prId: str | None = None
    lifecycle: dict | None = None
    workspace: dict | None = None


class AggregateStepOut(BaseModel):
    """One ordered step of the W4-C2 aggregate (agent_events summary row)."""

    seq: int
    type: str
    ts: datetime
    reason: str | None = None
    # 2026-11-13 (U2 timeline): the few payload facts the run page shows, secret-scrubbed and
    # clamped server-side, so the page no longer downloads the whole project's event replay.
    detail: dict[str, str | int | bool] | None = None


class BudgetDegradedOut(BaseModel):
    """M18-C2 — operator-visible budget-gate health for this run.

    ``degraded=true`` means at least one budget decision on this run was
    made FAIL-CLOSED because the money store was unreachable (denial shaped
    like an outage, never silently opened). ``warned=true`` (M23-B) means at
    least one ALLOWED decision crossed the near-cap warning threshold — the
    operator sees the walk toward the cap before the hard stop. Absent/false
    = healthy / far from the cap.
    """

    degraded: bool = False
    warned: bool = False


class UpgradeCtaOut(BaseModel):
    """M22-M2 — structured upgrade call-to-action on a tier cap hit.

    Emitted when a TIER cap (e.g. free runs/day) refused this run: ``tier``
    names the plan that capped it, ``cap``/``used`` quantify the hit,
    ``capField`` says which limit (wire spelling), and ``upgradeTo``/``cta``
    are what the UI renders as the upgrade action. ``upgradeTo`` is null at
    the top tier (nothing to sell; CTA degrades to contact-the-operator).
    """

    tier: str
    cap: int
    capField: str
    used: int
    upgradeTo: str | None = None
    cta: dict = Field(default_factory=dict)


class RunAggregateResponse(BaseModel):
    """M14-W4 F3a — the FROZEN pair-contract W4-C2 aggregate shape.

    ``costUsd``/``prId``/``url``/``steps`` are explicit-null when their source
    does not exist yet (no ledger rows, no PR opened, …); the detail page (F3b)
    degrades honestly per missing field. ``costUsd`` is exact scale-6 money.
    """

    runId: str
    state: str
    costUsd: float | None = None
    # W27 (additive): the plan's estimate, so a client can show actual against estimated
    estimatedCostUsd: float | None = None
    prId: str | None = None
    url: str | None = None
    steps: list[AggregateStepOut] = Field(default_factory=list)
    # Base status fields stay part of the aggregate (superset of /status).
    planId: str | None = None
    planApproved: bool = False
    isHumanEditing: bool = False
    executing: bool = False
    engineMode: str | None = None
    budget: BudgetDegradedOut = Field(default_factory=BudgetDegradedOut)
    # M22-M2 (additive): structured upgrade CTA; explicit null = no cap hit.
    upgrade: UpgradeCtaOut | None = None
    # Part 10 (additive): honest lifecycle {phase, cancelRequested, cancelConfirmed,
    # mutationPossible}; see harness_orchestrator.lifecycle.
    lifecycle: dict | None = None
    # Part 09.010 (additive): {id, fingerprint, isolated, isolation}; no paths, no secrets.
    workspace: dict | None = None
    # W15 (additive): the run's durable routing decision
    # {enabled, class, model, source, reason?} from the `model_routed` event, or
    # None when the run never emitted one (pre-W15 run, or no attempt started).
    # Read from the event store, so any replica answers it identically.
    routing: dict | None = None


class ErrorDetail(BaseModel):
    """One error entry of the FROZEN contract envelope (ERROR_BODY_EXAMPLE)."""

    code: str
    message: str


class ErrorEnvelope(BaseModel):
    """The frozen contract error shape for OpenAPI responses={} docs."""

    error: ErrorDetail


class HumanEditRequest(BaseModel):
    # M12-G3: bounded edit payloads — a multi-MB body used to reach the
    # handler/DB unbounded (limits are additive; see harness_identity.limits).
    path: str = Field(max_length=HUMAN_EDIT_PATH_MAX)
    content: str = Field(max_length=HUMAN_EDIT_CONTENT_MAX)


class DoneSignalRequest(BaseModel):
    # M12-G3: bounded evidence diff (additive; see harness_identity.limits).
    diffText: str = Field(default="", max_length=DONE_DIFF_TEXT_MAX)


class ProposePlanRequest(BaseModel):
    # M12-G3: bounded goal text + project id (additive; see limits).
    goal: str | None = Field(default=None, max_length=PROPOSE_GOAL_MAX)
    projectId: str | None = Field(default=None, max_length=PROPOSE_PROJECT_ID_MAX)


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


async def _run_belongs_to_team(run_id: str, principal: Principal) -> bool:
    """True when an EXISTING run is already owned by the principal's team.

    A run that has no durable row yet is a legitimate new run (propose creates
    it) and is allowed to proceed only after its projectId was verified. A run
    whose row exists but whose project is foreign/dangling is denied, so a
    propose can never rebind another tenant's run into the caller's team.
    """
    factory = getattr(_service, "session_factory", None)
    if factory is None:
        return True  # bare unit mode (conftest sets HARNESS_AUTHZ_TEST_MODE)
    from sqlalchemy import text

    async with factory() as db:
        run = (
            await db.execute(
                text("SELECT project_id FROM agent_runs WHERE id = :r"), {"r": run_id}
            )
        ).first()
        if run is None:
            return True
        if not run.project_id:
            return False
        team = (
            await db.execute(
                text("SELECT team_id FROM projects WHERE id = :p"),
                {"p": run.project_id},
            )
        ).first()
    return team is not None and team.team_id == principal.teamId


@router.post(
    "/runs/{runId}/plan",
    status_code=status.HTTP_201_CREATED,
    response_model=ProposePlanResponse,
    responses={
        400: {"model": ErrorEnvelope, "description": "INVALID_STATE"},
        404: {"model": ErrorEnvelope, "description": "foreign/unknown projectId (no-oracle)"},
    },
)
async def propose_plan(
    runId: str, principal: RequirePrincipal, req: ProposePlanRequest | None = None
) -> dict[str, str]:
    try:
        goal = req.goal if req else None
        project_id = req.projectId if req else None
        # D5 (audit): a plan must bind to an explicit, caller-owned project.
        # Omitting projectId used to let any caller mint/act on an unattributed
        # run; there is no legitimate HTTP propose without a target project.
        if project_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "project not found"}},
            )
        # D5: proposing BINDS the run to projectId — reject another team's project.
        if not await _project_belongs_to_team(project_id, principal):
            # M13-G5a no-oracle law: foreign/unknown project ids are
            # indistinguishable — 404 NOT_FOUND, never a 403 confirming the
            # project exists in another team.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "project not found",
                    }
                },
            )
        if not await _run_belongs_to_team(runId, principal):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "run not found"}},
            )
        plan_id = await _service.propose_plan(runId, goal=goal, project_id=project_id)
        return {"runId": runId, "planId": plan_id}
    except InvalidStateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_STATE", "message": str(e)}},
        ) from e


@router.post(
    "/runs/{runId}/plan/approve",
    status_code=status.HTTP_200_OK,
    response_model=PlanDecisionResponse,
    responses={
        403: {"model": ErrorEnvelope, "description": "role may not approve"},
        404: {"model": ErrorEnvelope, "description": "unknown or foreign run (no-oracle)"},
    },
)
async def approve_plan(runId: str, req: PlanActionRequest, actor: PlanApprover) -> dict:
    # Identity comes from the session token, NEVER from the request body —
    # forgeable approver IDs would poison the audit trail.
    try:
        await _service.approve_plan(runId, req.planId, actor.userId)
    except PlanAlreadyDecidedError as e:
        # M19-N1: the durable CAS is now the service-level decision gate, so
        # a lost decision race answers the same frozen 409 envelope the
        # plan-gate routes have answered since M12-F2.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "PLAN_ALREADY_DECIDED", "message": str(e)}},
        ) from e
    return {"runId": runId, "planId": req.planId, "status": "approved"}


@router.post(
    "/runs/{runId}/plan/reject",
    status_code=status.HTTP_200_OK,
    response_model=PlanDecisionResponse,
    responses={
        403: {"model": ErrorEnvelope, "description": "role may not reject"},
        404: {"model": ErrorEnvelope, "description": "plan mismatch"},
    },
)
async def reject_plan(runId: str, req: PlanActionRequest, actor: PlanApprover) -> dict:
    try:
        await _service.reject_plan(runId, req.planId, actor.userId)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "PLAN_MISMATCH", "message": str(e)}},
        ) from e
    except PlanAlreadyDecidedError as e:
        # M19-N1: same durable-CAS conflict surface as approve (above).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "PLAN_ALREADY_DECIDED", "message": str(e)}},
        ) from e
    return {"runId": runId, "planId": req.planId, "status": "rejected"}


async def _enforce_team_budget(run_id: str) -> None:
    """Consume the run's TEAM budget for a spend surface, or answer 429.

    Imported lazily so this module keeps importing without the limiter's
    dependencies in bare unit contexts. A missing factory or an unresolvable
    team skips the guard: the ownership gate has already run, and a lookup
    failure must not become an outage. The bucket itself lives in the same
    ``rate_buckets`` store as the IP limiter, under a ``team:`` namespace.
    """
    from harness_contracts.ratelimit import (
        RUN_START_TEMPLATE,
        consume_team_budget,
    )

    factory = getattr(_service, "run_queue_factory", None) or getattr(
        _service, "session_factory", None
    )
    if factory is None:
        return
    team_id = await _service._team_for_run({"runId": run_id})
    allowed, retry_after = await consume_team_budget(factory, team_id, RUN_START_TEMPLATE)
    if allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": {
                "code": "RATE_LIMITED",
                "message": "team run-start budget exhausted; retry shortly",
            }
        },
        headers={"Retry-After": str(retry_after)},
    )


@router.post(
    "/runs/{runId}/start",
    status_code=status.HTTP_200_OK,
    response_model=RunIdStatusResponse,
    responses={
        400: {"model": ErrorEnvelope, "description": "INVALID_STATE"},
        403: {"model": ErrorEnvelope, "description": "PLAN_NOT_APPROVED / role denial"},
        404: {"model": ErrorEnvelope, "description": "unknown or foreign run (no-oracle)"},
        429: {"model": ErrorEnvelope, "description": "RATE_LIMITED (team budget)"},
    },
)
async def start_execution(runId: str, actor: RunWriter) -> dict[str, str]:
    # W15 slice 2: spawn cost is limited per team, not only per IP. The team
    # is resolved from the run after authorization; an over-budget team is
    # answered before any engine work starts.
    await _enforce_team_budget(runId)
    try:
        await _service.start_execution(runId)
        # S1-R05e: durable lifecycle record — "who started what, when".
        await emit_run_lifecycle(
            _service,
            action=AUDIT_ACTION_RUN_STARTED,
            run_id=runId,
            principal=actor,
        )
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


@router.post(
    "/runs/{runId}/pause",
    status_code=status.HTTP_200_OK,
    response_model=RunIdStatusResponse,
)
async def pause_agent(runId: str, actor: RunWriter) -> dict[str, str]:
    try:
        await _service.pause_agent(runId)
    except InvalidStateTransitionError as e:
        # pause vs stop/complete race: the run is already finished, say so (409), not a 500.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "RUN_TERMINAL", "message": str(e)}},
        ) from e
    await emit_run_lifecycle(
        _service, action=AUDIT_ACTION_RUN_PAUSED, run_id=runId, principal=actor
    )
    # Honest status: "pausing" while the engine can still mutate files.
    return {
        "runId": runId,
        "status": "paused" if _service.engine_quiesced(runId) else "pausing",
    }


@router.post(
    "/runs/{runId}/resume",
    status_code=status.HTTP_200_OK,
    response_model=RunStateResponse,
)
async def resume_agent(runId: str, actor: RunWriter) -> dict[str, str]:
    await _service.resume_agent(runId)
    await emit_run_lifecycle(
        _service, action=AUDIT_ACTION_RUN_RESUMED, run_id=runId, principal=actor
    )
    # RunStateResponse carries `state`, not `status` — and resume is not
    # statically EXECUTING: terminal runs no-op silently (resume-from-DONE is
    # pinned by tests/unit/test_resume.py), so report the machine's real state.
    status_after = await _service.get_status(runId)
    return {"runId": runId, "state": status_after["state"]}


@router.post(
    "/runs/{runId}/stop",
    status_code=status.HTTP_200_OK,
    response_model=RunIdStatusResponse,
)
async def stop_agent(runId: str, actor: RunWriter) -> dict[str, str]:
    await emit_run_lifecycle(
        _service, action=AUDIT_ACTION_CANCEL_REQUESTED, run_id=runId, principal=actor
    )
    changed = await _service.stop_agent(runId)
    # S1-R05e: durable lifecycle record - "who stopped what, when". A repeated stop
    # (changed=False) is idempotent: the request is audited above, but no second
    # `run.stopped` / `run.cancel_confirmed` row claims a stop that did not happen.
    if changed:
        await emit_run_lifecycle(
            _service,
            action=AUDIT_ACTION_RUN_STOPPED,
            run_id=runId,
            principal=actor,
        )
    # Honest status: "stopping" while the engine thread is still alive (it
    # cannot be killed); "stopped" only once cancellation is confirmed.
    quiesced = _service.engine_quiesced(runId)
    if quiesced and changed:
        await emit_run_lifecycle(
            _service, action=AUDIT_ACTION_CANCEL_CONFIRMED, run_id=runId, principal=actor
        )
    if not changed:
        # Repeat stop: report what the run actually is (a run that completed
        # normally is "completed", not "stopped").
        phase = (await _service.get_status(runId)).get("lifecycle", {}).get("phase")
        if phase in ("stopped", "stopping", "completed"):
            return {"runId": runId, "status": phase}
    return {"runId": runId, "status": "stopped" if quiesced else "stopping"}


@router.post("/runs/{runId}/human-edit", status_code=status.HTTP_200_OK)
async def submit_human_edit(
    runId: str, req: HumanEditRequest, actor: RunWriter
) -> dict[str, str]:
    await _service.submit_human_edit(runId, req.path, req.content)
    return {"runId": runId, "status": "paused_on_human_edit", "path": req.path}


@router.post(
    "/runs/{runId}/done-signal",
    status_code=status.HTTP_200_OK,
    response_model=DoneSignalResponse,
    responses={
        400: {"model": ErrorEnvelope, "description": "EMPTY_DIFF"},
        403: {"model": ErrorEnvelope, "description": "DONE_SIGNAL_ROLE_DENIED"},
        404: {"model": ErrorEnvelope, "description": "unknown or foreign run (no-oracle)"},
        409: {"model": ErrorEnvelope, "description": "RUN_NOT_LIVE"},
    },
)
async def signal_done(
    runId: str, req: DoneSignalRequest, actor: RunReader, request: Request
) -> dict:
    # H-007: the done-gate is enforced HERE, before any transition or review
    # spend. The route still declares RunReader so read-only principals reach
    # this handler — and get the gate's explicit 403 instead of a generic
    # authz denial, with identical semantics for service-level callers.
    principal: Principal = actor
    try:
        await _service.gate_done_signal(runId, principal, req.diffText)
    except RunNotLiveForDoneSignalError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": e.code, "message": e.message}},
        ) from e
    except EmptyDiffTextError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": e.code, "message": e.message}},
        ) from e
    except DoneSignalRejectedError as e:
        # The status follows the error class (role denied 403, verification required 400, ...).
        raise HTTPException(
            status_code=e.http_status,
            detail={"error": {"code": e.code, "message": e.message}},
        ) from e
    # The reviewer's model call runs as the person who signalled done: their token, role, budget.
    auth = request.headers.get("authorization", "")
    model_router = None
    if auth.startswith("Bearer "):
        model_router = ModelsServiceRouter(auth.removeprefix("Bearer "))
    findings = await _service.signal_done(runId, diff_text=req.diffText, model_router=model_router)
    await _artifacts.record_done_changes(runId, principal)
    dump = findings.model_dump(by_alias=True)
    return {"runId": runId, "findings": dump}


@router.get("/runs/{runId}/engine", status_code=status.HTTP_200_OK)
async def get_engine_result(runId: str, reader: RunReader) -> dict[str, Any]:
    return await _service.get_engine_result(runId)


@router.get(
    "/runs/{runId}/status",
    status_code=status.HTTP_200_OK,
    response_model=RunStatusResponse,
)
async def get_run_status(runId: str, reader: RunReader) -> dict[str, Any]:
    return await _service.get_status(runId)


@router.get(
    "/runs/{runId}",
    status_code=status.HTTP_200_OK,
    response_model=RunAggregateResponse,
    responses={
        404: {
            "model": ErrorEnvelope,
            "description": "unknown or foreign run (no-oracle: identical envelope)",
        },
    },
)
async def get_run(runId: str, reader: RunReader) -> dict[str, Any]:
    """Single-run aggregate read — pair-contract W4-C2 (M14-W4 F3a).

    Wire shape ``{runId, state, costUsd?, prId?, url?, steps?}`` plus the
    legacy status fields (superset of ``/status``). Nullability is explicit:
    ``costUsd`` is None until cost_ledger rows exist for the run; ``prId``/
    ``url`` stay None until S2's PR-open producer binds one; ``steps`` lists
    the run's agent_events as ordered ``{seq, type, ts}`` summaries, capped at
    the LAST 100 (wire order = seq order). Unknown runs and foreign teams'
    runs both answer 404 NOT_FOUND with the identical frozen error envelope
    (M13-G5a no-oracle law).
    """
    try:
        return await _service.get_status(runId)
    except RunNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "run not found"}},
        ) from e


@router.get("/runs/{runId}/learned-rule", status_code=status.HTTP_200_OK)
async def get_learned_rule(runId: str, reader: RunReader) -> dict[str, Any]:
    """I-08 (Sprint 5 Task 37): the project rule a denial stop suggested, read-only.

    ``{offer: {failureClass, paths, suggestedRule, recordedAt, status: "proposed"} | null,
    similarCases: [...]}``. A proposal only (CD-02): saving it is the human PUT on the
    project rules API. Any team member who can read the run can read its lesson; foreign
    and unknown runs answer the same 404 as every other run read."""
    factory = getattr(_service, "session_factory", None)
    if factory is None:
        return dict(collab.NO_LEARNED_RULE)
    return await collab.learned_rule_for_run(factory, runId, reader.teamId)


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


class ReadinessCheckOut(BaseModel):
    check: Literal[
        "provider", "model", "worker", "revision", "repository", "permissions", "provider-call"
    ]
    status: Literal["ok", "fail", "unknown"]
    component: str
    reason: str
    detail: str


class ReadinessResponse(BaseModel):
    ok: bool
    checks: list[ReadinessCheckOut]


@router.get(
    "/readiness",
    status_code=status.HTTP_200_OK,
    response_model=ReadinessResponse,
    responses={
        404: {"model": ErrorEnvelope, "description": "unknown or foreign project (no-oracle)"}
    },
)
async def get_readiness(projectId: str, principal: RequirePrincipal) -> dict[str, Any]:
    """W23: would a run started now in this project work, and if not, what is wrong."""
    import asyncio

    from harness_orchestrator import readiness
    from harness_orchestrator.engine import DAEMON_TOKEN_ENV

    factory = getattr(_service, "session_factory", None)
    repo, known = None, factory is not None
    if known:
        from sqlalchemy import text

        async with factory() as db:
            row = (
                await db.execute(
                    text("SELECT team_id, repo_full_name FROM projects WHERE id = :p"),
                    {"p": projectId},
                )
            ).first()
        if row is None or row.team_id != principal.teamId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "project not found"}},
            )
        repo = row.repo_full_name
    worker = await asyncio.to_thread(
        readiness.check_worker,
        getattr(_service.engine, "daemon_url", "") or "",
        os.environ.get(DAEMON_TOKEN_ENV, ""),
        readiness.code_digest(),
    )
    return readiness.report([
        *readiness.check_engine(os.environ),
        *worker,
        readiness.check_repository(repo, known=known),
        readiness.check_permissions(principal),
    ])


@router.post(
    "/readiness/probe",
    status_code=status.HTTP_200_OK,
    response_model=ReadinessResponse,
    responses={
        403: {"model": ErrorEnvelope, "description": "the caller may not start runs"},
        429: {"model": ErrorEnvelope, "description": "a probe was sent less than a minute ago"},
    },
)
async def post_readiness_probe(principal: RequirePrincipal) -> dict[str, Any]:
    """W23 F2: send one one-token request to the provider and say whether it accepts the model.

    Opt-in and paid (a fraction of a cent): `harness doctor --probe` is the only caller, and the
    server allows one a minute. A caller who may not start runs may not spend on the provider."""
    import asyncio

    from harness_orchestrator import readiness

    gate = readiness.check_permissions(principal)
    if gate["status"] != readiness.OK:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "ROLE_DENIED", "message": gate["detail"]}},
        )
    try:
        entry = await asyncio.to_thread(readiness.probe_provider, os.environ)
    except readiness.ProbeTooSoon as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": {"code": "PROBE_TOO_SOON", "message": str(exc)}},
            headers={"Retry-After": str(int(exc.retry_after) + 1)},
        ) from exc
    return readiness.report([entry])


# ---------------------------------------------------------------------------
# Shared-run collaboration (multiplayer pilot S2) - see collab.py for the rules.
# Every route is guarded by the run-ownership dependency (foreign run => 404);
# the acting identity is always the authenticated Principal, never the body.
# ---------------------------------------------------------------------------
from harness_orchestrator import artifacts as _artifacts  # noqa: E402
from harness_orchestrator import collab, delivery  # noqa: E402

CollabReader = Annotated[Principal, Depends(_authz_dep("read", audit=True))]
CollabWriter = Annotated[Principal, Depends(_authz_dep("write", audit=True))]


def _pilot_gate(actor: Principal) -> None:
    """Collaboration is limited to HARNESS_PILOT_TEAMS when set (unset = open).

    Other teams get a 404 with code PILOT_NOT_ENABLED (the UI shows a plain notice).
    """
    if not collab.pilot_allowed(actor.teamId):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "PILOT_NOT_ENABLED",
                              "message": "Shared runs are not enabled for your team."}},
        )


async def _collab(call):
    factory = getattr(_service, "session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "COLLAB_UNAVAILABLE", "message": "no database wired"}},
        )
    try:
        return await call(factory)
    except collab.CollabError as e:
        ctx = _denial_ctx.get()
        if not e.audited and ctx is not None:
            await collab.audit_denial(factory, ctx[0], ctx[1], ctx[2], e.code, e.status)
        raise HTTPException(
            status_code=e.status, detail={"error": {"code": e.code, "message": str(e)}}
        ) from e


async def _persist_run(run_id: str) -> None:
    """Guarantee the agent_runs parent row (FK target of every collab table).

    Memory-only runs get it created via the bus (idempotent); if it still is
    not there the caller gets a clean 409, never an FK 500.
    """
    factory = getattr(_service, "session_factory", None)
    if factory is None:
        return  # _collab answers 503
    from sqlalchemy import text

    async def exists() -> bool:
        async with factory() as db:
            row = (
                await db.execute(text("SELECT 1 FROM agent_runs WHERE id = :r"), {"r": run_id})
            ).first()
        return row is not None

    if await exists():
        return
    run_data = await _service._ensure_run(run_id)
    bus = getattr(_service, "bus", None)
    if bus is not None and run_data.get("projectId"):
        await bus.ensure_run(run_data["projectId"], run_id)
    if not await exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "RUN_NOT_PERSISTED",
                              "message": "run has no durable record yet"}},
        )


class RedirectRequest(BaseModel):
    kind: Literal["guidance", "scope_change", "permission_change"] = "guidance"
    content: str = Field(max_length=collab.MAX_TEXT)
    reason: str | None = Field(default=None, max_length=collab.MAX_REASON)
    requestId: str | None = Field(default=None, max_length=64)


class HandoffRequest(BaseModel):
    toUserId: str = Field(max_length=36)


class HandoffDecision(BaseModel):
    accept: bool


class ApprovalRequest(BaseModel):
    action: Literal["delete", "modify"]
    paths: list[str] = Field(max_length=collab.MAX_PATHS)
    summary: str = Field(max_length=collab.MAX_TEXT)
    diff: str = Field(default="", max_length=collab.MAX_DIFF)


class ApprovalDecision(BaseModel):
    approve: bool
    digest: str = Field(max_length=64)  # the digest the approver reviewed
    # X-07 KPI signal from the client (same wire name and meaning as section8's ApproveRequest):
    # was the proposed diff on screen before Approve? None = not measured (unknown, never blind).
    viewed_detail: bool | None = None


class ApprovalDigest(BaseModel):
    digest: str = Field(max_length=64)


class ApprovalOutcome(BaseModel):
    ok: bool
    outcome: str = Field(default="", max_length=collab.MAX_TEXT)


@router.post("/runs/{runId}/join")
async def collab_join(runId: str, actor: CollabReader) -> dict:
    _pilot_gate(actor)
    await _persist_run(runId)
    return await _collab(lambda f: collab.join(f, actor, runId))


@router.get("/runs/{runId}/participants")
async def collab_roster(runId: str, actor: CollabReader) -> dict:
    _pilot_gate(actor)
    return await _collab(lambda f: collab.roster(f, runId))


@router.post("/runs/{runId}/leader")
async def collab_claim_leader(runId: str, actor: CollabWriter) -> dict:
    _pilot_gate(actor)
    await _persist_run(runId)
    return await _collab(lambda f: collab.claim_leader(f, actor, runId))


@router.post("/runs/{runId}/redirect")
async def collab_redirect(runId: str, req: RedirectRequest, actor: CollabWriter) -> dict:
    _pilot_gate(actor)
    result = await _collab(
        lambda f: collab.redirect(
            f, actor, runId, req.kind, req.content, req.reason,
            collab.actor_request_id(actor, req.requestId),
        )
    )
    if f"redirect.{req.kind}" in collab.GUIDANCE_KINDS:  # permission changes are not injected
        result["takesEffect"] = await collab.takes_effect_now(
            _service, runId, bool(result.get("queued")))
    return result


@router.get("/runs/{runId}/collab-history")
async def collab_history(
    runId: str, actor: CollabReader, after: int = 0, limit: int = 200, schemaVersion: int = 1
) -> dict:
    _pilot_gate(actor)
    return await _collab(lambda f: delivery.history_page(
        f, actor.teamId, runId, after, limit, schemaVersion))


@router.post("/runs/{runId}/handoff")
async def collab_handoff(runId: str, req: HandoffRequest, actor: CollabWriter) -> dict:
    _pilot_gate(actor)
    return await _collab(lambda f: collab.request_handoff(f, actor, runId, req.toUserId))


@router.post("/runs/{runId}/handoff/{handoffId}")
async def collab_handoff_decide(
    runId: str, handoffId: str, req: HandoffDecision, actor: CollabWriter
) -> dict:
    _pilot_gate(actor)
    return await _collab(lambda f: collab.decide_handoff(f, actor, runId, handoffId, req.accept))


@router.post("/runs/{runId}/approvals", status_code=status.HTTP_201_CREATED)
async def collab_approval_request(runId: str, req: ApprovalRequest, actor: CollabWriter) -> dict:
    _pilot_gate(actor)
    return await _collab(
        lambda f: collab.request_approval(
            f, actor, runId, req.action, req.paths, req.summary, req.diff
        )
    )


@router.get("/runs/{runId}/approvals")
async def collab_approval_list(runId: str, actor: CollabReader) -> dict:
    _pilot_gate(actor)
    return {"approvals": await _collab(lambda f: collab.list_approvals(f, runId))}


@router.put("/runs/{runId}/approvals/{approvalId}")
async def collab_approval_amend(
    runId: str, approvalId: str, req: ApprovalRequest, actor: CollabWriter
) -> dict:
    _pilot_gate(actor)
    return await _collab(
        lambda f: collab.amend_approval(
            f, actor, runId, approvalId, req.paths, req.summary, req.diff
        )
    )


@router.post("/runs/{runId}/approvals/{approvalId}/decision")
async def collab_approval_decide(
    runId: str, approvalId: str, req: ApprovalDecision, actor: CollabWriter
) -> dict:
    _pilot_gate(actor)
    return await _collab(
        lambda f: collab.decide_approval(
            f, actor, runId, approvalId, req.approve, req.digest, viewed_detail=req.viewed_detail
        )
    )


@router.post("/runs/{runId}/approvals/{approvalId}/claim")
async def collab_approval_claim(
    runId: str, approvalId: str, req: ApprovalDigest, actor: CollabWriter
) -> dict:
    _pilot_gate(actor)
    return await _collab(lambda f: collab.claim_approval(f, actor, runId, approvalId, req.digest))


@router.post("/runs/{runId}/approvals/{approvalId}/complete")
async def collab_approval_complete(
    runId: str, approvalId: str, req: ApprovalOutcome, actor: CollabWriter
) -> dict:
    _pilot_gate(actor)
    return await _collab(
        lambda f: collab.complete_approval(f, actor, runId, approvalId, req.ok, req.outcome)
    )


# ---- B3: staged (withheld) high-impact changes -----------------------------
import asyncio  # noqa: E402

from harness_orchestrator import staging  # noqa: E402


def _staging_root() -> tuple[str, str] | None:
    eng = getattr(_service, "engine", None)
    if eng is None or not getattr(eng, "isolated", False) or not hasattr(eng, "run_workspace"):
        return None
    return eng.workspace, eng.run_workspace


@router.post("/runs/{runId}/staged/approvals")
async def collab_staged_approvals(runId: str, actor: CollabWriter) -> dict:
    """(Re)file approvals for whatever is currently withheld for this run."""
    _pilot_gate(actor)
    cfg = _staging_root()
    if cfg is None:
        return {"approvals": []}
    groups = await asyncio.to_thread(staging.pending_groups, runId, cfg[0])
    out = await _collab(
        lambda f: collab.ensure_staged_approvals(f, actor.teamId, runId, groups)
    )
    return {"approvals": out}


@router.post("/runs/{runId}/approvals/{approvalId}/apply")
async def collab_apply_staged(
    runId: str, approvalId: str, req: ApprovalDigest, actor: CollabWriter
) -> dict:
    """Land an APPROVED staged change: claim (digest re-checked) -> apply -> complete."""
    _pilot_gate(actor)
    cfg = _staging_root()
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "NO_STAGING", "message": "run is not staged"}},
        )
    root, ws_for = cfg

    async def go(f):
        claimed = await collab.claim_approval(f, actor, runId, approvalId, req.digest)
        try:
            applied = await asyncio.to_thread(
                staging.apply_group, runId, ws_for(runId), root,
                claimed["action"], claimed["paths"], req.digest,
            )
        except staging.StagingError as e:
            await collab.complete_approval(f, actor, runId, approvalId, False, str(e))
            domain_metrics.inc("harness_approval_denied_total", "staging_mismatch")
            raise collab.CollabError(409, "STAGING_MISMATCH", str(e), audited=True) from e
        return await collab.complete_approval(
            f, actor, runId, approvalId, True, f"applied {len(applied)} file(s)"
        )

    return await _collab(go)
