from harness_contracts.signatures import ModelRole

from harness_router.pricing import PricingEngine


def test_deepseek_pricing_calculation():
    cost = PricingEngine.calculate_cost("deepseek/deepseek-coder", 10_000, 2_000)
    assert cost == 0.00196


def test_local_endpoint_zero_cost():
    cost = PricingEngine.calculate_cost("local/qwen3:14b-ablated", 50_000, 10_000)
    assert cost == 0.0


def test_estimate_cost_primary_vs_reviewer():
    primary_cost = PricingEngine.estimate_cost(ModelRole.PRIMARY, 5_000, 1_000)
    reviewer_cost = PricingEngine.estimate_cost(ModelRole.REVIEWER, 5_000, 1_000)

    assert primary_cost > 0
    assert reviewer_cost > 0
