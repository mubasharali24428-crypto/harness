import threading
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from harness_contracts.signatures import ModelCallOpts, ModelResult, ModelRole
from pydantic import BaseModel, Field

from harness_router.catalog import catalog
from harness_router.config import MODEL_PRICING_CATALOG
from harness_router.providers import InvalidApiKeyError, RateLimitExceededError
from harness_router.service import ModelRouterService

# Wave 8B: tiny self-written Prometheus collector (NO prometheus_client dep).
# Counted per (HTTP method, route template) so label cardinality stays bounded
# even with unbounded agent/project ids in real paths.
_lock = threading.Lock()
_requests_total: dict[tuple[str, str], int] = defaultdict(int)


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


class EstimateCostRequest(BaseModel):
    role: ModelRole
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)
    model: str | None = None


class ConfigureRoleModelRequest(BaseModel):
    role: ModelRole
    model: str


@router.post("/call", response_model=ModelResult, status_code=status.HTTP_200_OK)
def call_model(req: CallModelRequest) -> ModelResult:
    try:
        return _service.call_model(
            agent_id=req.agentId,
            role=req.role,
            messages=req.messages,
            opts=req.opts,
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
