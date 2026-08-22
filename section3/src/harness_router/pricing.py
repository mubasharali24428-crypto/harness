from harness_contracts.signatures import ModelRole

from harness_router.config import MODEL_PRICING_CATALOG, ModelPricing, config


class PricingEngine:
    """Calculates token costs and USD metering for model invocations."""

    @staticmethod
    def get_pricing(model: str) -> ModelPricing:
        if model in MODEL_PRICING_CATALOG:
            return MODEL_PRICING_CATALOG[model]
        # Default fallback pricing if custom model used
        return ModelPricing(input_cost_per_million=0.50, output_cost_per_million=1.50)

    @classmethod
    def calculate_cost(cls, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = cls.get_pricing(model)
        input_cost = (input_tokens / 1_000_000.0) * pricing.input_cost_per_million
        output_cost = (output_tokens / 1_000_000.0) * pricing.output_cost_per_million
        return round(input_cost + output_cost, 6)

    @classmethod
    def estimate_cost(
        cls,
        role: ModelRole,
        input_tokens: int,
        output_tokens: int,
        custom_model: str | None = None,
    ) -> float:
        if custom_model:
            model = custom_model
        elif role == ModelRole.PRIMARY:
            model = config.default_primary_model
        else:
            model = config.default_reviewer_model

        return cls.calculate_cost(model, input_tokens, output_tokens)
