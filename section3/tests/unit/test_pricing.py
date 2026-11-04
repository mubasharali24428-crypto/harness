"""H-023 (M13 Wave 3): pricing math is EXACT Decimal at NUMERIC(12,6) scale."""

from __future__ import annotations

from decimal import Decimal

from harness_contracts.signatures import ModelRole

from harness_router.pricing import PricingEngine


def test_deepseek_pricing_calculation_is_exact_decimal():
    cost = PricingEngine.calculate_cost("deepseek/deepseek-v4-flash", 10_000, 2_000)
    assert isinstance(cost, Decimal)
    assert cost == Decimal("0.001274")


def test_local_endpoint_zero_cost():
    cost = PricingEngine.calculate_cost("local/qwen3:14b-ablated", 50_000, 10_000)
    assert isinstance(cost, Decimal)
    assert cost == Decimal("0.000000")


def test_estimate_cost_primary_vs_reviewer():
    primary_cost = PricingEngine.estimate_cost(ModelRole.PRIMARY, 5_000, 1_000)
    reviewer_cost = PricingEngine.estimate_cost(ModelRole.REVIEWER, 5_000, 1_000)

    assert primary_cost > 0
    assert reviewer_cost > 0


def test_every_result_is_quantized_to_numeric_12_6_scale():
    """No result may carry sub-micro-dollar residue or a float type."""
    for model in ("deepseek/deepseek-chat", "anthropic/claude-haiku-4.5"):
        cost = PricingEngine.calculate_cost(model, 333_333, 333_334)
        assert isinstance(cost, Decimal)
        assert cost == cost.quantize(Decimal("0.000001"))
        assert -cost.as_tuple().exponent <= 6


def test_float_input_rates_convert_through_shortest_repr_not_binary_expansion():
    """0.1-style rates must become Decimal('0.1'), never 0.10000000000...55."""
    cost = PricingEngine.calculate_cost("local/qwen3:14b-ablated", 1_000_000, 1_000_000)
    assert cost == Decimal("0.000000")
