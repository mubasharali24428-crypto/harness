from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelPricing(BaseModel):
    """Cost in USD per 1 Million tokens."""

    input_cost_per_million: float
    output_cost_per_million: float


# Catalog of supported models and prices per million tokens
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


class RouterConfig(BaseSettings):
    default_primary_model: str = "deepseek/deepseek-coder"
    default_reviewer_model: str = "openai/gpt-4o-mini"
    suggest_cheaper_model: bool = False  # default OFF, never force downgrade

    model_config = SettingsConfigDict(env_prefix="ROUTER_")


config = RouterConfig()
