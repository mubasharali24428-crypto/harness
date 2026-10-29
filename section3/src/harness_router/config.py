from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelPricing(BaseModel):
    """Cost in USD per 1 Million tokens."""

    input_cost_per_million: float
    output_cost_per_million: float


# Catalog of supported models and prices per million tokens
MODEL_PRICING_CATALOG: dict[str, ModelPricing] = {
    # DeepSeek (OpenRouter ids; prices re-read from /models 2026-10-29)
    "deepseek/deepseek-chat": ModelPricing(
        input_cost_per_million=0.2574,
        output_cost_per_million=1.0287,
    ),
    "deepseek/deepseek-v4-flash": ModelPricing(
        input_cost_per_million=0.091,
        output_cost_per_million=0.182,
    ),
    "deepseek/deepseek-r1-0528": ModelPricing(
        input_cost_per_million=0.5,
        output_cost_per_million=2.15,
    ),
    # Qwen
    "qwen/qwen-2.5-coder-32b-instruct": ModelPricing(
        input_cost_per_million=0.66,
        output_cost_per_million=1.0,
    ),
    "qwen/qwen-2.5-72b-instruct": ModelPricing(
        input_cost_per_million=0.36,
        output_cost_per_million=0.4,
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
    "anthropic/claude-sonnet-4.5": ModelPricing(
        input_cost_per_million=3.0,
        output_cost_per_million=15.0,
    ),
    "anthropic/claude-haiku-4.5": ModelPricing(
        input_cost_per_million=1.0,
        output_cost_per_million=5.0,
    ),
    # Mistral
    "mistralai/mistral-large-2512": ModelPricing(
        input_cost_per_million=0.5,
        output_cost_per_million=1.5,
    ),
    "mistralai/codestral-2508": ModelPricing(
        input_cost_per_million=0.3,
        output_cost_per_million=0.9,
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
    default_primary_model: str = "deepseek/deepseek-v4-flash"
    default_reviewer_model: str = "openai/gpt-4o-mini"
    suggest_cheaper_model: bool = False  # default OFF, never force downgrade

    model_config = SettingsConfigDict(env_prefix="ROUTER_")


config = RouterConfig()
