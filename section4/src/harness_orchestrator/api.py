import bisect
import logging
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from harness_contracts.roles import Principal
from pydantic import BaseModel, Field
from starlette.datastructures import Headers, MutableHeaders
from starlette.routing import Match

# M12-G3: shared cap constants live in section1; this venv does not carry
# harness_identity as an installed distribution, so the src tree is appended
# exactly like the wave-1 audit seam (service.py) already does at call time.
_ROOT = Path(__file__).resolve().parents[3]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

from harness_identity.limits import (  # noqa: E402
    DONE_DIFF_TEXT_MAX,
    HUMAN_EDIT_CONTENT_MAX,
    HUMAN_EDIT_PATH_MAX,
    PLAN_ACTION_PLAN_ID_MAX,
    PROPOSE_GOAL_MAX,
    PROPOSE_PROJECT_ID_MAX,
)

from harness_orchestrator.authz import (
    OwnershipDeniedError,
    RoleDeniedError,
    authorize_run,
    require_role,
)
from harness_orchestrator.logging_utils import (
    REQUEST_ID_HEADER,
    reset_request_id,
    set_request_id,
)
from harness_orchestrator.planner import PlanNotApprovedError
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.signals import (
    DoneSignalRejectedError,
    EmptyDiffTextError,
    RunNotLiveForDoneSignalError,
)
from harness_orchestrator.state_machine import InvalidStateTransitionError

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

    def observe(
        self, *, method: str, path: str, status_code: int | str, duration_s: float
    ) -> None:
        ckey = (method, path, str(status_code))
        hkey = (method, path)
        idx = bisect.bisect_right(self._buckets, duration_s)
        with self._lock:
            self._counts[ckey] = self._counts.get(ckey, 0) + 1
            raw = self._hist_raw.setdefault(hkey, [0] * len(self._buckets))
            if 0 <= idx < len(raw):
                raw[idx] += 1
            self._hist_sum[hkey] = self._hist_sum.get(hkey, 0.0) + duration_s

    def render(self) -> str:
        """Prometheus text format (version 0.0.4), deterministic ordering."""
        with self._lock:
            counters = sorted(self._counts.items())
            hist_raw = {k: list(v) for k, v in self._hist_raw.items()}
            hist_sum = dict(self._hist_sum)

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
        return "\n".join(lines) + "\n"


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
    # M12-G3: bounded ids (limits are additive; see harness_identity.limits).
    planId: str = Field(max_length=PLAN_ACTION_PLAN_ID_MAX)


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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": e.code, "message": e.message}},
        ) from e
    findings = await _service.signal_done(runId, diff_text=req.diffText)
    dump = findings.model_dump(by_alias=True)
    return {"runId": runId, "findings": dump}


@router.get("/runs/{runId}/engine", status_code=status.HTTP_200_OK)
async def get_engine_result(runId: str, reader: RunReader) -> dict[str, Any]:
    return await _service.get_engine_result(runId)


@router.get("/runs/{runId}/status", status_code=status.HTTP_200_OK)
async def get_run_status(runId: str, reader: RunReader) -> dict[str, Any]:
    return await _service.get_status(runId)


@router.get("/runs/{runId}", status_code=status.HTTP_200_OK)
async def get_run(runId: str, reader: RunReader) -> dict[str, Any]:
    """Minimal single-run aggregate read (M12-W2 E4a).

    Same wire shape as ``/status`` plus ``prId`` — the run's opened PR id
    (agent_runs.pr_id, set by Section 2's PR-open producer), or None while
    no PR exists. The review flow uses this to resolve run → PR without
    replaying bus events.
    """
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
