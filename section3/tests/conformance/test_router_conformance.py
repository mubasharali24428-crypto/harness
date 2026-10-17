from fastapi.testclient import TestClient
from fixtures.golden_fixtures import (
    GOLDEN_AGENT_ID,
    GOLDEN_EXPECTED_MODELS,
    GOLDEN_MESSAGES,
)
from harness_contracts.signatures import ModelResult, ModelRole

from harness_router.api import get_principal, router
from harness_router.service import ModelRouterService


def test_router_implements_protocol():
    service = ModelRouterService()
    for method_name in ["call_model", "estimate_cost", "list_available_models"]:
        method = getattr(service, method_name, None)
        assert method is not None, f"ModelRouterService missing {method_name}"
        assert callable(method)


def test_router_lifecycle_call_and_history():
    service = ModelRouterService()

    # 1. List available models
    models = service.list_available_models()
    for m in GOLDEN_EXPECTED_MODELS:
        assert m in models

    # 2. Call model as PRIMARY
    primary_res = service.call_model(
        agent_id=GOLDEN_AGENT_ID,
        role=ModelRole.PRIMARY,
        messages=GOLDEN_MESSAGES,
    )
    assert isinstance(primary_res, ModelResult)
    assert primary_res.costUsd >= 0.0

    # 3. Call model as REVIEWER
    reviewer_res = service.call_model(
        agent_id=GOLDEN_AGENT_ID,
        role=ModelRole.REVIEWER,
        messages=GOLDEN_MESSAGES,
    )
    assert isinstance(reviewer_res, ModelResult)

    # 4. Check call history recorded for cost ledger
    assert len(service.call_history) == 2
    assert service.call_history[0]["role"] == ModelRole.PRIMARY
    assert service.call_history[1]["role"] == ModelRole.REVIEWER


def test_rest_api_conformance():
    from types import SimpleNamespace

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u-conf", teamId="t1", role="engineer"
    )
    client = TestClient(app)

    # 1. GET /api/v1/models
    res = client.get("/api/v1/models")
    assert res.status_code == 200
    model_list = res.json()
    assert isinstance(model_list, list)
    assert "deepseek/deepseek-coder" in model_list
    assert "local/qwen3:14b-ablated" in model_list

    # 2. POST /api/v1/models/estimate-cost
    res = client.post(
        "/api/v1/models/estimate-cost",
        json={"role": "primary", "inputTokens": 10000, "outputTokens": 2000},
    )
    assert res.status_code == 200
    data = res.json()
    assert "estimatedCostUsd" in data
    assert data["estimatedCostUsd"] > 0

    # 3. POST /api/v1/models/call (successful)
    res = client.post(
        "/api/v1/models/call",
        json={
            "agentId": GOLDEN_AGENT_ID,
            "role": "primary",
            "messages": GOLDEN_MESSAGES,
        },
    )
    assert res.status_code == 200
    call_data = res.json()
    assert "text" in call_data
    assert "costUsd" in call_data
    assert "latencyMs" in call_data

    # 4. POST /api/v1/models/call (invalid key error handling)
    res = client.post(
        "/api/v1/models/call",
        json={
            "agentId": GOLDEN_AGENT_ID,
            "role": "primary",
            "messages": [{"role": "user", "content": "Trigger: INVALID_KEY_TRIGGER"}],
        },
    )
    assert res.status_code == 401
    err_data = res.json()
    assert err_data["detail"]["error"]["code"] == "INVALID_API_KEY"

    # 5. POST /api/v1/models/config (change role model)
    res = client.post(
        "/api/v1/models/config",
        json={"role": "reviewer", "model": "anthropic/claude-3-5-haiku"},
    )
    assert res.status_code == 200
