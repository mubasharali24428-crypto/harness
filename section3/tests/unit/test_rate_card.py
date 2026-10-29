"""W14 — the operator rate card: overlay, visibility and parity.

The frozen contract engine prices only the built-in catalog. A deployment can
add rates without editing contracts; an unpriced model still bills zero, but the
zero is now attributable ("unpriced") instead of indistinguishable from free.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from harness_contracts.pricing import MODEL_PRICING_CATALOG, PricingEngine
from harness_contracts.signatures import ModelRole
from starlette.testclient import TestClient

from harness_router import rate_card
from harness_router.api import get_principal, get_router_service, router
from harness_router.providers import priced_cost

CARD = {
    "models": {
        "lab/new-model": {"input": 1.25, "output": 10.0},
        "lab/free-preview": {"input": 0, "output": 0},
    }
}


@pytest.fixture(autouse=True)
def fresh_card(monkeypatch):
    monkeypatch.delenv(rate_card.RATE_CARD_ENV, raising=False)
    rate_card.set_active(None)
    yield
    rate_card.set_active(None)


# ------------------------------------------------------------------ money math


def test_local_math_matches_the_frozen_engine_for_every_catalog_model():
    """Parity is the guard against duplicating the contract formula wrongly."""
    for model, pricing in MODEL_PRICING_CATALOG.items():
        assert rate_card.cost_of(pricing, 1234, 567) == PricingEngine.calculate_cost(
            model, 1234, 567
        ), model


# --------------------------------------------------------------------- overlay


def test_overlay_prices_an_unknown_model_and_is_labelled_operator():
    card = rate_card.RateCard(rate_card.parse_overlay(CARD))
    assert card.source("lab/new-model") == "operator"
    assert card.priced("lab/new-model") is True
    assert card.cost("lab/new-model", 1_000_000, 1_000_000) == Decimal("11.250000")


def test_overlay_can_override_a_catalog_model():
    card = rate_card.RateCard(
        rate_card.parse_overlay({"models": {"openai/gpt-4o": {"input": 1.0, "output": 2.0}}})
    )
    assert card.source("openai/gpt-4o") == "operator"
    assert card.cost("openai/gpt-4o", 1_000_000, 1_000_000) == Decimal("3.000000")


def test_catalog_and_unpriced_are_distinguishable():
    card = rate_card.RateCard()
    assert card.source("deepseek/deepseek-chat") == "catalog"
    assert card.source("brandnew-lab/model-v9") == "unpriced"
    assert card.priced("brandnew-lab/model-v9") is False
    # An unpriced model bills zero — never an invented rate.
    assert card.cost("brandnew-lab/model-v9", 500_000, 500_000) == Decimal("0")


def test_zero_priced_catalog_entries_are_catalog_not_unpriced():
    card = rate_card.RateCard()
    assert card.source("local/custom") == "catalog"
    assert card.priced("local/custom") is True


# ----------------------------------------------------------- validation / env


@pytest.mark.parametrize(
    "payload",
    [
        {"models": {"x/y": {"input": -1, "output": 2}}},
        {"models": {"x/y": {"input": "cheap", "output": 2}}},
        {"models": {"x/y": {"input": 1}}},
        {"models": {"": {"input": 1, "output": 2}}},
        {"models": []},
        ["not", "an", "object"],
    ],
)
def test_malformed_cards_fail_loudly(payload):
    with pytest.raises(rate_card.RateCardError):
        rate_card.parse_overlay(payload)


def test_inline_env_card_is_loaded_and_malformed_env_fails(monkeypatch):
    monkeypatch.setenv(
        rate_card.RATE_CARD_ENV,
        '{"models": {"lab/env-model": {"input": 2, "output": 4}}}',
    )
    rate_card.set_active(None)
    card = rate_card.active()
    assert card.source("lab/env-model") == "operator"
    assert card.cost("lab/env-model", 1_000_000, 0) == Decimal("2.000000")

    monkeypatch.setenv(rate_card.RATE_CARD_ENV, "{not json")
    rate_card.set_active(None)
    with pytest.raises(rate_card.RateCardError):
        rate_card.active()


def test_priced_cost_uses_the_active_card(monkeypatch):
    card = rate_card.RateCard(
        rate_card.parse_overlay({"models": {"lab/x": {"input": 3, "output": 6}}})
    )
    rate_card.set_active(card)
    assert priced_cost("lab/x", 1_000_000, 1_000_000) == Decimal("9.000000")
    assert priced_cost("still/unknown", 1_000_000, 1_000_000) == Decimal("0")


# --------------------------------------------------------------- HTTP surface


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u-rate", teamId="t-rate", role="engineer"
    )
    app.dependency_overrides[get_router_service] = lambda: SimpleNamespace()
    return TestClient(app)


def test_estimate_reports_priced_and_source_for_a_named_model(client):
    card = rate_card.RateCard(
        rate_card.parse_overlay({"models": {"lab/named": {"input": 1, "output": 1}}})
    )
    rate_card.set_active(card)
    response = client.post(
        "/api/v1/models/estimate-cost",
        json={"role": ModelRole.PRIMARY.value, "inputTokens": 1_000_000,
              "outputTokens": 0, "model": "lab/named"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "lab/named"
    assert body["priced"] is True
    assert body["pricingSource"] == "operator"
    assert body["estimatedCostUsd"] == 1.0


def test_estimate_never_substitutes_the_team_model_for_an_unpriced_request(client):
    response = client.post(
        "/api/v1/models/estimate-cost",
        json={"role": ModelRole.PRIMARY.value, "inputTokens": 1_000,
              "outputTokens": 1_000, "model": "brandnew-lab/model-v9"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # It used to answer the team's default price under the requested model name.
    assert body["model"] == "brandnew-lab/model-v9"
    assert body["priced"] is False
    assert body["pricingSource"] == "unpriced"
    assert body["estimatedCostUsd"] == 0.0


def test_estimate_without_a_model_uses_the_role_default(client):
    response = client.post(
        "/api/v1/models/estimate-cost",
        json={"role": ModelRole.PRIMARY.value, "inputTokens": 10_000, "outputTokens": 2_000},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["priced"] is True
    assert body["pricingSource"] == "catalog"
    assert body["estimatedCostUsd"] > 0
