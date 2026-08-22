"""Comprehensive stress tests for Section 3: Model Routing & BYOK Layer."""

import pytest
from harness_contracts.signatures import ModelResult, ModelRole

from harness_router.config import MODEL_PRICING_CATALOG, RouterConfig, config
from harness_router.pricing import PricingEngine
from harness_router.providers import (
    ModelProviderClient,
)
from harness_router.service import ModelRouterService

# ================================================================
# PRICING ENGINE - EDGE CASES
# ================================================================


class TestPricingEdgeCases:
    def test_zero_token_cost(self):
        cost = PricingEngine.calculate_cost("openai/gpt-4o", 0, 0)
        assert cost == 0.0

    def test_single_token_cost(self):
        cost = PricingEngine.calculate_cost("openai/gpt-4o", 1, 0)
        assert cost > 0.0
        assert cost < 0.001

    def test_very_large_token_count(self):
        cost = PricingEngine.calculate_cost("openai/gpt-4o", 1_000_000_000, 1_000_000_000)
        assert cost > 0.0
        assert isinstance(cost, float)

    def test_all_catalog_models_have_nonnegative_pricing(self):
        for model_name, pricing in MODEL_PRICING_CATALOG.items():
            assert pricing.input_cost_per_million >= 0.0, f"{model_name} neg input"
            assert pricing.output_cost_per_million >= 0.0, f"{model_name} neg output"

    def test_local_models_are_free(self):
        for model_name, pricing in MODEL_PRICING_CATALOG.items():
            if model_name.startswith("local/"):
                assert pricing.input_cost_per_million == 0.0
                assert pricing.output_cost_per_million == 0.0

    def test_unknown_model_gets_fallback_pricing(self):
        pricing = PricingEngine.get_pricing("nonexistent/model-xyz")
        assert pricing.input_cost_per_million == 0.50
        assert pricing.output_cost_per_million == 1.50

    def test_estimate_cost_with_custom_model_override(self):
        cost_default = PricingEngine.estimate_cost(ModelRole.PRIMARY, 10_000, 5_000)
        cost_custom = PricingEngine.estimate_cost(
            ModelRole.PRIMARY,
            10_000,
            5_000,
            custom_model="anthropic/claude-3-5-sonnet",
        )
        assert cost_custom > cost_default

    def test_cost_rounding_precision(self):
        cost = PricingEngine.calculate_cost("deepseek/deepseek-coder", 7, 3)
        assert cost == round(cost, 6)


# ================================================================
# PROVIDER CLIENT - ERROR PATHS
# ================================================================


class TestProviderClientStress:
    def test_empty_messages_list(self):
        client = ModelProviderClient()
        result = client.call(model="deepseek/deepseek-coder", messages=[])
        assert isinstance(result, ModelResult)

    def test_messages_with_empty_content(self):
        client = ModelProviderClient()
        result = client.call(
            model="deepseek/deepseek-coder",
            messages=[{"role": "user", "content": ""}],
        )
        assert isinstance(result, ModelResult)

    def test_very_long_prompt(self):
        client = ModelProviderClient()
        result = client.call(
            model="deepseek/deepseek-coder",
            messages=[{"role": "user", "content": "x" * 100_000}],
        )
        assert isinstance(result, ModelResult)
        assert result.costUsd >= 0.0

    def test_multiple_mocks_first_match_wins(self):
        client = ModelProviderClient()
        client.set_mock_response("hello", "Response A")
        client.set_mock_response("hello world", "Response B")
        result = client.call(
            model="deepseek/deepseek-coder",
            messages=[{"role": "user", "content": "hello world"}],
        )
        assert result.text == "Response A"

    def test_result_has_all_required_fields(self):
        client = ModelProviderClient()
        result = client.call(
            model="openai/gpt-4o",
            messages=[{"role": "user", "content": "test"}],
        )
        assert hasattr(result, "text")
        assert hasattr(result, "costUsd")
        assert hasattr(result, "model")
        assert hasattr(result, "latencyMs")
        assert result.latencyMs > 0

    def test_opts_are_optional(self):
        client = ModelProviderClient()
        result = client.call(
            model="deepseek/deepseek-coder",
            messages=[{"role": "user", "content": "no opts"}],
        )
        assert isinstance(result, ModelResult)

    def test_api_keys_storage(self):
        client = ModelProviderClient(api_keys={"openai": "sk-test-123"})
        assert client.api_keys["openai"] == "sk-test-123"


# ================================================================
# MODEL ROUTER SERVICE - LIFECYCLE
# ================================================================


class TestModelRouterServiceStress:
    def test_list_models_covers_full_catalog(self):
        service = ModelRouterService()
        models = service.list_available_models()
        for key in MODEL_PRICING_CATALOG:
            assert key in models, f"Missing model: {key}"

    def test_call_model_records_history_for_each_call(self):
        service = ModelRouterService()
        for i in range(5):
            service.call_model(
                agent_id=f"agent-{i}",
                role=ModelRole.PRIMARY,
                messages=[{"role": "user", "content": f"request {i}"}],
            )
        assert len(service.call_history) == 5

    def test_primary_and_reviewer_use_different_models(self):
        service = ModelRouterService()
        r1 = service.call_model("a1", ModelRole.PRIMARY, [{"role": "user", "content": "t"}])
        r2 = service.call_model("a1", ModelRole.REVIEWER, [{"role": "user", "content": "t"}])
        assert r1.model == config.default_primary_model
        assert r2.model == config.default_reviewer_model

    def test_set_role_model_overrides_default(self):
        service = ModelRouterService()
        service.set_role_model(ModelRole.PRIMARY, "anthropic/claude-3-5-sonnet")
        result = service.call_model("a-ovr", ModelRole.PRIMARY, [{"role": "user", "content": "t"}])
        assert result.model == "anthropic/claude-3-5-sonnet"

    def test_estimate_matches_calculate(self):
        service = ModelRouterService()
        est = service.estimate_cost(ModelRole.PRIMARY, 10_000, 2_000)
        direct = PricingEngine.calculate_cost(config.default_primary_model, 10_000, 2_000)
        assert est == direct

    def test_history_entry_shape(self):
        service = ModelRouterService()
        service.call_model("agent-audit", ModelRole.PRIMARY, [{"role": "user", "content": "t"}])
        entry = service.call_history[0]
        assert entry["agent_id"] == "agent-audit"
        assert entry["role"] == ModelRole.PRIMARY
        assert "costUsd" in entry
        assert "latencyMs" in entry
        assert entry["costUsd"] >= 0.0

    def test_multiple_agents_isolated_history(self):
        service = ModelRouterService()
        for aid in ["a", "b", "c"]:
            service.call_model(aid, ModelRole.PRIMARY, [{"role": "user", "content": aid}])
        assert [h["agent_id"] for h in service.call_history] == ["a", "b", "c"]


# ================================================================
# REST API - EDGE CASES
# ================================================================


class TestRouterRestApiStress:
    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from harness_router.api import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_call_missing_agent_id_422(self, client):
        res = client.post(
            "/api/v1/models/call",
            json={"role": "primary", "messages": [{"role": "user", "content": "t"}]},
        )
        assert res.status_code == 422

    def test_call_invalid_role_422(self, client):
        res = client.post(
            "/api/v1/models/call",
            json={"agentId": "t", "role": "INVALID", "messages": []},
        )
        assert res.status_code == 422

    def test_estimate_negative_tokens_422(self, client):
        res = client.post(
            "/api/v1/models/estimate-cost",
            json={"role": "primary", "inputTokens": -1, "outputTokens": 100},
        )
        assert res.status_code == 422

    def test_config_unknown_model_400(self, client):
        res = client.post(
            "/api/v1/models/config",
            json={"role": "primary", "model": "nonexistent/xyz"},
        )
        assert res.status_code == 400
        assert res.json()["detail"]["error"]["code"] == "UNKNOWN_MODEL"

    def test_rate_limit_returns_429(self, client):
        res = client.post(
            "/api/v1/models/call",
            json={
                "agentId": "t",
                "role": "primary",
                "messages": [{"role": "user", "content": "RATE_LIMIT_TRIGGER"}],
            },
        )
        assert res.status_code == 429

    def test_list_models_non_empty(self, client):
        res = client.get("/api/v1/models")
        assert res.status_code == 200
        assert len(res.json()) >= 10


# ================================================================
# CONFIG - VALIDATION
# ================================================================


class TestConfigValidation:
    def test_default_config_values(self):
        cfg = RouterConfig()
        assert cfg.default_primary_model == "deepseek/deepseek-coder"
        assert cfg.default_reviewer_model == "openai/gpt-4o-mini"
        assert cfg.suggest_cheaper_model is False

    def test_catalog_minimum_size(self):
        assert len(MODEL_PRICING_CATALOG) >= 10

    def test_catalog_keys_are_namespaced(self):
        for key in MODEL_PRICING_CATALOG:
            parts = key.split("/")
            assert len(parts) == 2, f"Key '{key}' not provider/model"
            assert len(parts[0]) > 0
            assert len(parts[1]) > 0
