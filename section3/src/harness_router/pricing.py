from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Union

from harness_contracts.signatures import ModelRole

from harness_router.config import MODEL_PRICING_CATALOG, ModelPricing, config

Number = Union[int, float, str, "Decimal"]

# NUMERIC(12,6): 6 decimal places of exact base-10 money. Quantizing every
# result to this scale keeps the Python-side math aligned with the DB columns
# (H-023: float money dies — all money arithmetic is Decimal).
_MONEY_Q = Decimal("0.000001")


def _dec(value: Number) -> Decimal:
    """Exact Decimal conversion.

    Floats are converted via repr (shortest string that round-trips) so the
    user's decimal intent survives: float 0.1 -> Decimal('0.1'), NOT the binary
    expansion 0.1000000000000000055511151231257827021181583404541015625.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


class PricingEngine:
    """Calculates token costs and USD metering for model invocations."""

    @staticmethod
    def get_pricing(model: str) -> ModelPricing:
        if model in MODEL_PRICING_CATALOG:
            return MODEL_PRICING_CATALOG[model]
        # Default fallback pricing if custom model used
        return ModelPricing(input_cost_per_million=0.50, output_cost_per_million=1.50)

    @classmethod
    def calculate_cost(cls, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        """Exact per-call cost as a Decimal at NUMERIC(12,6) scale."""
        pricing = cls.get_pricing(model)
        input_cost = _dec(pricing.input_cost_per_million) * int(input_tokens)
        output_cost = _dec(pricing.output_cost_per_million) * int(output_tokens)
        total = (input_cost + output_cost) / Decimal(1_000_000)
        return total.quantize(_MONEY_Q, rounding=ROUND_HALF_EVEN)

    @classmethod
    def estimate_cost(
        cls,
        role: ModelRole,
        input_tokens: int,
        output_tokens: int,
        custom_model: str | None = None,
    ) -> Decimal:
        if custom_model:
            model = custom_model
        elif role == ModelRole.PRIMARY:
            model = config.default_primary_model
        else:
            model = config.default_reviewer_model

        return cls.calculate_cost(model, int(input_tokens), int(output_tokens))
