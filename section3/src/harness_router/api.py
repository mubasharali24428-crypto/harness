import asyncio
import logging
import threading
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from harness_contracts.signatures import ModelCallOpts, ModelResult, ModelRole
from pydantic import BaseModel, Field

from harness_router import ledger as cost_ledger
from harness_router.catalog import catalog
from harness_router.config import MODEL_PRICING_CATALOG
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


class EstimateCostRequest(BaseModel):
    role: ModelRole
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)
    model: str | None = None


class ConfigureRoleModelRequest(BaseModel):
    role: ModelRole
    model: str


@router.post("/call", response_model=ModelResult, status_code=status.HTTP_200_OK)
async def call_model(req: CallModelRequest) -> ModelResult:
    try:
        # Provider call is blocking; keep it off the event loop like the
        # threadpool the sync def previously ran in.
        result = await run_in_threadpool(
            _service.call_model,
            agent_id=req.agentId,
            role=req.role,
            messages=req.messages,
            opts=req.opts,
            # FIX-LOOP E1: the decrypted team key survives the REST seam and
            # reaches the provider layer (real-mode gate). The simulated
            # fallback stays keyless-only and dev-gated.
            api_key=req.apiKey,
            model_name=req.model,
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
        record_call_usage(agent_id=req.agentId, role=req.role, messages=req.messages, result=result)
    except Exception:  # pragma: no cover - belt & braces around fire-and-forget
        logger.warning("cost metering scheduling failed; continuing", exc_info=True)
    return result


@router.get("/spend", status_code=status.HTTP_200_OK)
async def model_spend(
    teamId: str | None = Query(default=None),
    projectId: str | None = Query(default=None),
    sinceDays: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """Aggregated spend from cost_ledger: totals, per-model and per-day."""
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
        return await cost_ledger.spend_summary(
            pool,
            team_id=teamId or None,
            project_id=projectId or None,
            since_days=sinceDays,
        )
    except Exception as e:
        logger.warning("spend aggregation failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {"code": "LEDGER_QUERY_FAILED", "message": f"Spend query failed: {e}"}
            },
        ) from e


@router.post("/estimate-cost", status_code=status.HTTP_200_OK)
def estimate_cost(req: EstimateCostRequest) -> dict[str, Any]:
    cost = _service.estimate_cost(
        role=req.role,
        input_tokens=req.inputTokens,
        output_tokens=req.outputTokens,
    )
    return {
        "role": req.role,
        "inputTokens": req.inputTokens,
        "outputTokens": req.outputTokens,
        "estimatedCostUsd": cost,
    }


@router.get("", response_model=list[str], status_code=status.HTTP_200_OK)
def list_models() -> list[str]:
    return _service.list_available_models()


@router.get("/catalog", status_code=status.HTTP_200_OK)
def provider_catalog() -> list[dict[str, Any]]:
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
def configure_role_model(req: ConfigureRoleModelRequest) -> dict[str, str]:
    if req.model not in MODEL_PRICING_CATALOG:
        msg = f"Model '{req.model}' is not in catalog."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "UNKNOWN_MODEL", "message": msg}},
        )
    _service.set_role_model(req.role, req.model)
    return {"role": req.role, "model": req.model, "status": "configured"}


@router.get("/metrics", status_code=status.HTTP_200_OK)
def prometheus_metrics() -> Response:
    """PUBLIC Prometheus endpoint — no auth dep, like /health."""
    return PlainTextResponse(
        render_prometheus_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
