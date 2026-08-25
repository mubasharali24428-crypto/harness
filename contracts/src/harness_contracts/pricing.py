"""Token pricing engine — exact base-10 money math for model invocations.

M17-B3 promotion: this lived at ``harness_router.pricing`` and was reached by
Section 4's metering hook through runtime sys.path injection of section3's
src tree (an isolation-law violation the CI guard could not see). The frozen
``MODEL_PRICING_CATALOG`` and the NUMERIC(12,6)-exact ``PricingEngine`` now
live in contracts; section3 re-imports them so its own call sites are
unchanged.

NUMERIC(12,6): 6 decimal places of exact base-10 money. Quantizing every
result to this scale keeps the Python-side math aligned with the DB columns
(H-023: float money dies — all money arithmetic is Decimal).
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Union

from pydantic import BaseModel

from harness_contracts.signatures import ModelRole

Number = Union[int, float, str, "Decimal"]

# NUMERIC(12,6) quantum.
_MONEY_Q = Decimal("0.000001")


class ModelPricing(BaseModel):
    """Cost in USD per 1 Million tokens."""

    input_cost_per_million: float
    output_cost_per_million: float


#: Catalog of supported models and prices per million tokens.
MODEL_PRICING_CATALOG: dict[str, ModelPricing] = {
    # DeepSeek
    "deepseek/deepseek-chat": ModelPricing(
        input_cost_per_million=0.14,
        output_cost_per_million=0.28,
    ),
    "deepseek/deepseek-coder": ModelPricing(
        input_cost_per_million=0.14,
        output_cost_per_million=0.28,
    ),
    "deepseek/deepseek-reasoner": ModelPricing(
        input_cost_per_million=0.55,
        output_cost_per_million=2.19,
    ),
    # Qwen
    "qwen/qwen-2.5-coder-32b": ModelPricing(
        input_cost_per_million=0.20,
        output_cost_per_million=0.60,
    ),
    "qwen/qwen-2.5-72b": ModelPricing(
        input_cost_per_million=0.40,
        output_cost_per_million=1.20,
    ),
    # OpenAI
    "openai/gpt-4o": ModelPricing(
        input_cost_per_million=2.50,
        output_cost_per_million=10.00,
    ),
    "openai/gpt-4o-mini": ModelPricing(
        input_cost_per_million=0.15,
        output_cost_per_million=0.60,
    ),
    # Anthropic
    "anthropic/claude-3-5-sonnet": ModelPricing(
        input_cost_per_million=3.00,
        output_cost_per_million=15.00,
    ),
    "anthropic/claude-3-5-haiku": ModelPricing(
        input_cost_per_million=0.80,
        output_cost_per_million=4.00,
    ),
    # Mistral
    "mistral/mistral-large": ModelPricing(
        input_cost_per_million=2.00,
        output_cost_per_million=6.00,
    ),
    "mistral/codestral": ModelPricing(
        input_cost_per_million=0.30,
        output_cost_per_million=0.90,
    ),
    # Local & Open-Weight (Free / self-hosted execution)
    "local/qwen3:14b-ablated": ModelPricing(
        input_cost_per_million=0.0,
        output_cost_per_million=0.0,
    ),
    "local/custom": ModelPricing(
        input_cost_per_million=0.0,
        output_cost_per_million=0.0,
    ),
}


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

    # M17-B gate repair: estimate_cost rode along when this engine was promoted
    # from section3 (M17-B3) but was dropped because it read section3's local
    # ``config`` for the per-role defaults. Restored with the same shipped
    # defaults pinned here — config stays section-local, so contracts carries
    # its own copy (keep both sides in lockstep if they ever move).
    DEFAULT_ROLE_MODELS: dict[ModelRole, str] = {
        ModelRole.PRIMARY: "deepseek/deepseek-coder",
        ModelRole.REVIEWER: "openai/gpt-4o-mini",
    }

    @classmethod
    def estimate_cost(
        cls,
        role: ModelRole,
        input_tokens: int,
        output_tokens: int,
        custom_model: str | None = None,
    ) -> Decimal:
        """Cost estimate for a role's default model (or an explicit override)."""
        if custom_model:
            model = custom_model
        else:
            model = cls.DEFAULT_ROLE_MODELS.get(role)
            if model is None:
                raise ValueError(f"no default model for role {role!r}")
        return cls.calculate_cost(model, int(input_tokens), int(output_tokens))
