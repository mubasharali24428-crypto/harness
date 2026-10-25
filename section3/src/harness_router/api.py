import asyncio
import logging
import threading
from collections import defaultdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from harness_contracts.roles import Principal, can
from harness_contracts.signatures import ModelCallOpts, ModelResult, ModelRole
from pydantic import BaseModel, Field

from harness_router import ledger as cost_ledger
from harness_router import prefs as model_prefs
from harness_router.catalog import catalog
from harness_router.config import MODEL_PRICING_CATALOG
from harness_router.pricing import PricingEngine
from harness_router.providers import (
    InvalidApiKeyError,
    ModelUnavailableError,
    RateLimitExceededError,
)
from harness_router.service import ModelRouterService

# Wave 8B: tiny self-written Prometheus collector (NO prometheus_client dep).
# Counted per (HTTP method, route template) so label cardinality stays bounded
# even with unbounded agent/project ids in real paths.
_lock = threading.Lock()
_requests_total: dict[tuple[str, str], int] = defaultdict(int)

logger = logging.getLogger(__name__)

# Cost-metering pool holder. None until deploy wiring calls set_ledger_pool —
# metering is then skipped entirely; the call path is never blocked.
_ledger_pool: list[Any] = [None]


def get_ledger_pool() -> Any:
    """Current cost_ledger pool, or None when metering is not wired."""
    return _ledger_pool[0]


def set_ledger_pool(pool: Any) -> None:
    """Attach/replace the cost_ledger pool (Section 10 assembly point)."""
    _ledger_pool[0] = pool


# Fire-and-forget usage tasks; kept so tests can drain deterministically and
# shutdown can attempt a flush. Done-callbacks prune finished entries.
_usage_tasks: set[asyncio.Task] = set()


def pending_usage_tasks() -> tuple[asyncio.Task, ...]:
    """Currently outstanding usage-recording tasks (test/drain hook)."""
    return tuple(_usage_tasks)


def _estimate_tokens(messages: list[dict[str, str]], response_text: str) -> tuple[int, int]:
    """Mirror of providers.ModelProviderClient's token accounting (the frozen
    ModelResult contract carries no token fields): ~4 chars per token."""
    prompt = " ".join(m.get("content", "") for m in messages)
    return max(1, len(prompt) // 4), max(1, len(response_text) // 4)


def record_call_usage(
    agent_id: str,
    role: ModelRole,
    messages: list[dict[str, str]],
    result: ModelResult,
    run_id: str | None = None,
    pool: Any | None = None,
) -> asyncio.Task | None:
    """Fire-and-forget usage recording after a successful model call.

    Schedules ``ledger.record_usage`` on a background task; ledger failures
    are swallowed inside record_usage and never reach the caller.
    """
    pool = pool if pool is not None else _ledger_pool[0]
    if pool is None:
        return None
    input_tokens, output_tokens = _estimate_tokens(messages, result.text)
    task = asyncio.get_running_loop().create_task(
        cost_ledger.record_usage(
            pool,
            agent_id=agent_id,
            role=role,
            model=result.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=result.latencyMs,
            run_id=run_id,
        )
    )
    _usage_tasks.add(task)
    task.add_done_callback(_usage_tasks.discard)
    return task


def _record_request(method: str, path: str) -> None:
    with _lock:
        _requests_total[(method, path)] += 1


def _count_request(request: Request) -> None:
    """Router-wide dependency: count every request handled by this router."""
    route = request.scope.get("route")
    path_template = getattr(route, "path", None) or request.url.path
    _record_request(request.method, path_template)


def render_prometheus_metrics() -> str:
    lines = [
        "# HELP models_requests_total Total requests handled by the model router.",
        "# TYPE models_requests_total counter",
    ]
    with _lock:
        snapshot = sorted(_requests_total.items())
    for (method, path), count in snapshot:
        lines.append(f'models_requests_total{{method="{method}",path="{path}"}} {count}')
    return "\n".join(lines) + "\n"


router = APIRouter(
    prefix="/api/v1/models",
    tags=["models"],
    dependencies=[Depends(_count_request)],
)
_service = ModelRouterService()


async def get_principal(request: Request) -> Principal:
    """Auth resolution via app.state.get_principal; fail closed (D6/P0.6)."""
    dep = getattr(request.app.state, "get_principal", None)
    if dep is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "AUTH_NOT_CONFIGURED", "message": "auth not wired"}},
        )
    return await dep(request)


async def require_reader(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """Read routes (list/catalog/estimate/spend): any authenticated role."""
    if not can(principal, "read"):  # all roles today; explicit for future vocab
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "ROLE_DENIED", "message": "role may not read"}},
        )
    return principal


async def require_writer(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """Spending model budget and reconfiguring role models is a write action."""
    if not can(principal, "write"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "ROLE_DENIED", "message": "role may not write"}},
        )
    return principal


Reader = Annotated[Principal, Depends(require_reader)]
Writer = Annotated[Principal, Depends(require_writer)]


def get_router_service() -> ModelRouterService:
    return _service


class CallModelRequest(BaseModel):
    agentId: str
    role: ModelRole
    messages: list[dict[str, str]]
    opts: ModelCallOpts | None = None
    # FIX-LOOP E1 (VT-2): the team's decrypted BYO key rides the REST body.
    # It is forwarded to the provider layer ONLY (Authorization header on the
    # outbound provider call) — never logged, never stored, never echoed, and
    # deliberately NOT part of the frozen contracts/ protocol types.
    apiKey: str | None = None
    model: str | None = None
    # Optional attribution to an orchestrator run.  The server verifies the
    # run belongs to the authenticated team before recording it.
    runId: str | None = None


class ModelPrefsBody(BaseModel):
    """PUT /models/prefs — the team's durable role models and effort."""

    primaryModel: str = Field(min_length=1, max_length=120)
    reviewerModel: str = Field(min_length=1, max_length=120)
    reasoningEffort: Literal["off", "low", "medium", "high"] = "off"


class EstimateCostRequest(BaseModel):
    role: ModelRole
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)
    model: str | None = None


class ConfigureRoleModelRequest(BaseModel):
    role: ModelRole
    model: str


class SpendTotals(BaseModel):
    """M13-G5a typing island: spend totals block (camelCase wire shape)."""

    inputTokens: int
    outputTokens: int
    costUsd: float


class SpendByModel(BaseModel):
    model: str
    costUsd: float
    tokens: int


class SpendByDay(BaseModel):
    day: str
    costUsd: float


class UnattributedSpend(BaseModel):
    """Ledger calls with no run; never presented as team-owned spend."""

    calls: int
    inputTokens: int
    outputTokens: int
    costUsd: float


class SpendSummaryResponse(BaseModel):
    """Aggregate spend from cost_ledger (mirrors ledger.summarize_spend).

    M14-W4 F1a (pair contract W4-C1): ``unwired`` distinguishes
    zero-because-nothing-ran from zero-because-unmetered. It is False whenever
    the ledger is connected and queryable (even at zero rows — the numbers are
    then genuinely zero, not unknown) and the route keeps answering 503
    LEDGER_UNAVAILABLE only when NO metering config exists, so the field never
    contradicts the HTTP status.
    """

    totals: SpendTotals
    byModel: list[SpendByModel] = Field(default_factory=list)
    byDay: list[SpendByDay] = Field(default_factory=list)
    unattributed: UnattributedSpend
    unwired: bool = False


class SpendErrorDetail(BaseModel):
    """Frozen contract error entry for responses={} documentation."""

    code: str
    message: str


class SpendErrorEnvelope(BaseModel):
    error: SpendErrorDetail



def _role_value(role: object) -> str:
    return getattr(role, "value", str(role))


def _require_admin(principal: Principal) -> None:
    if _role_value(principal.role) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": "admin only"}},
        )


def _reasoning_capable(model: str) -> bool:
    name = model.lower()
    return "reasoner" in name or "/o1" in name or "/o3" in name


def _with_reasoning(opts: ModelCallOpts | None, effort: str, model: str) -> ModelCallOpts | None:
    """Attach a provider reasoning object only where it is accepted.

    The preference is stored for every deployment, but sending an unknown
    field to a first-party API can fail the call, so this is limited to
    OpenRouter-style endpoints and reasoning-capable models. "off" is a no-op
    (never changes today's request shape).
    """
    if opts is None or effort == "off" or not model_prefs.reasoning_wire_supported():
        return opts
    if not _reasoning_capable(model):
        return opts
    extra = dict(opts.extra or {})
    if "reasoning" in extra:
        return opts
    extra["reasoning"] = {"effort": effort}
    return opts.model_copy(update={"extra": extra})


@router.post("/call", response_model=ModelResult, status_code=status.HTTP_200_OK)
async def call_model(req: CallModelRequest, principal: Writer) -> ModelResult:
    pool = get_ledger_pool()
    if req.runId and pool is not None and hasattr(pool, "fetchrow"):
        row = await pool.fetchrow(
            "SELECT r.id FROM agent_runs r JOIN projects p ON p.id = r.project_id "
            "WHERE r.id = $1 AND p.team_id = $2",
            req.runId,
            principal.teamId,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "RUN_NOT_FOUND", "message": "run not found"}},
            )
    # A team's saved preference is the default when the caller does not name
    # a model. Explicit request models still win.
    effective_model = req.model or _service.team_override(req.role, principal.teamId)
    effective_opts = req.opts
    if effective_model is None:
        saved = await model_prefs.load_prefs(pool, principal.teamId)
        effective_model = (
            saved["reviewerModel"]
            if req.role == ModelRole.REVIEWER
            else saved["primaryModel"]
        )
        effective_opts = _with_reasoning(
            effective_opts, saved["reasoningEffort"], effective_model
        )
    try:
        # Provider call is blocking; keep it off the event loop like the
        # threadpool the sync def previously ran in.
        result = await run_in_threadpool(
            _service.call_model,
            agent_id=req.agentId,
            role=req.role,
            messages=req.messages,
            opts=effective_opts,
            # FIX-LOOP E1: the decrypted team key survives the REST seam and
            # reaches the provider layer (real-mode gate). The simulated
            # fallback stays keyless-only and dev-gated.
            api_key=req.apiKey,
            model_name=effective_model,
            team_id=principal.teamId,
        )
    except InvalidApiKeyError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "INVALID_API_KEY", "message": str(e)}},
        ) from e
    except RateLimitExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": {"code": "RATE_LIMIT_EXCEEDED", "message": str(e)}},
        ) from e
    except ModelUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "PROVIDER_UNAVAILABLE", "message": str(e)}},
        ) from e

    # Wave 9A: record real usage on every successful provider call. The
    # recorder swallows ledger failures — it must never break the call path.
    try:
        record_call_usage(
            agent_id=req.agentId,
            role=req.role,
            messages=req.messages,
            result=result,
            run_id=req.runId,
        )
    except Exception:  # pragma: no cover - belt & braces around fire-and-forget
        logger.warning("cost metering scheduling failed; continuing", exc_info=True)
    return result


@router.get(
    "/spend",
    status_code=status.HTTP_200_OK,
    response_model=SpendSummaryResponse,
    responses={
        503: {
            "model": SpendErrorEnvelope,
            "description": "LEDGER_UNAVAILABLE — cost ledger pool not wired",
        },
    },
)
async def model_spend(
    principal: Reader,
    teamId: str | None = Query(default=None),
    projectId: str | None = Query(default=None),
    sinceDays: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """Aggregated spend from cost_ledger: totals, per-model and per-day.

    Team-scoped (S1-R01a): every caller is locked to ``principal.teamId``;
    an explicit foreign ``teamId`` is denied with 403.

    M14-W4 F1a (W4-C1): a wired, queryable ledger answers 200 with
    ``unwired=False`` — even when zero rows exist, the zeros are REAL zeros
    (metering is live; nothing ran). ``unwired`` therefore never appears as
    true in a 200 body: the unwired case is the 503 LEDGER_UNAVAILABLE branch
    below, which fires only when no metering config (ledger pool) exists.
    """
    if teamId is not None and teamId != principal.teamId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "ROLE_DENIED",
                    "message": "cross-team spend queries are not permitted",
                }
            },
        )
    teamId = principal.teamId
    pool = get_ledger_pool()
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "LEDGER_UNAVAILABLE",
                    "message": "Cost ledger pool is not wired; spend data unavailable.",
                }
            },
        )
    try:
        unattributed = await cost_ledger.unattributed_summary(pool)
        summary = await cost_ledger.spend_summary(
            pool,
            team_id=teamId or None,
            project_id=projectId or None,
            since_days=sinceDays,
        )
        # W4-C1: wired + queryable ⇒ unwired=False, always. The zeros in an
        # empty ledger are real zeros now that run metering is live (F1a).
        return {**summary, "unattributed": unattributed, "unwired": False}
    except Exception as e:
        logger.warning("spend aggregation failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {"code": "LEDGER_QUERY_FAILED", "message": f"Spend query failed: {e}"}
            },
        ) from e


@router.post("/estimate-cost", status_code=status.HTTP_200_OK)
async def estimate_cost(req: EstimateCostRequest, principal: Reader) -> dict[str, Any]:
    model = req.model if req.model in MODEL_PRICING_CATALOG else None
    if model is None:
        saved = await model_prefs.load_prefs(get_ledger_pool(), principal.teamId)
        model = (
            saved["reviewerModel"]
            if req.role == ModelRole.REVIEWER
            else saved["primaryModel"]
        )
    cost = float(
        PricingEngine.calculate_cost(model, req.inputTokens, req.outputTokens)
    )
    return {
        "role": req.role,
        "inputTokens": req.inputTokens,
        "outputTokens": req.outputTokens,
        "estimatedCostUsd": cost,
    }


@router.get("", response_model=list[str], status_code=status.HTTP_200_OK)
def list_models(principal: Reader) -> list[str]:
    return _service.list_available_models()


@router.get("/catalog", status_code=status.HTTP_200_OK)
def provider_catalog(principal: Reader) -> list[dict[str, Any]]:
    """E-cat: declarative provider catalog (providers, env-var indirection,
    models). No secrets — env var NAMES only."""
    return [
        {
            "name": e.name,
            "kind": e.kind,
            "baseUrlEnv": e.base_url_env,
            "apiKeyEnv": e.api_key_env,
            "models": e.models,
        }
        for e in catalog.list_providers()
    ]


@router.post("/config", status_code=status.HTTP_200_OK)
def configure_role_model(req: ConfigureRoleModelRequest, principal: Writer) -> dict[str, str]:
    if req.model not in MODEL_PRICING_CATALOG:
        msg = f"Model '{req.model}' is not in catalog."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "UNKNOWN_MODEL", "message": msg}},
        )
    _service.set_role_model(req.role, req.model, team_id=principal.teamId)
    return {"role": req.role, "model": req.model, "status": "configured"}


@router.get("/prefs", status_code=status.HTTP_200_OK)
async def get_model_prefs(principal: Reader) -> dict[str, Any]:
    """The team's saved role models + reasoning effort (defaults when unset)."""
    return await model_prefs.load_prefs(get_ledger_pool(), principal.teamId)


@router.put("/prefs", status_code=status.HTTP_200_OK)
async def put_model_prefs(body: ModelPrefsBody, principal: Writer) -> dict[str, Any]:
    """Admin-only durable update. Also applies the choice to this process."""
    _require_admin(principal)
    for model in (body.primaryModel, body.reviewerModel):
        if not model_prefs.model_known(model):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "UNKNOWN_MODEL",
                        "message": f"Model '{model}' is not in catalog.",
                    }
                },
            )
    try:
        await model_prefs.save_prefs(
            get_ledger_pool(),
            principal.teamId,
            primary_model=body.primaryModel,
            reviewer_model=body.reviewerModel,
            reasoning_effort=body.reasoningEffort,
            updated_by=principal.userId,
        )
    except Exception as exc:  # noqa: BLE001 — storage is the only failure here
        logger.warning("model-preference write failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "PREFS_UNAVAILABLE",
                    "message": "model-preference storage is not available",
                }
            },
        ) from exc
    _service.set_role_model(ModelRole.PRIMARY, body.primaryModel, team_id=principal.teamId)
    _service.set_role_model(ModelRole.REVIEWER, body.reviewerModel, team_id=principal.teamId)
    return await model_prefs.load_prefs(get_ledger_pool(), principal.teamId)


@router.get("/metrics", status_code=status.HTTP_200_OK)
def prometheus_metrics() -> Response:
    """PUBLIC Prometheus endpoint — no auth dep, like /health.

    Deliberately public: this is the Prometheus scrape target (S1-R01a
    residual R-13 — route-count enumeration only, no spend/data leakage).
    """
    return PlainTextResponse(
        render_prometheus_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
