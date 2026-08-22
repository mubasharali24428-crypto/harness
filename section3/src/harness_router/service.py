from harness_contracts.signatures import ModelCallOpts, ModelResult, ModelRole, ModelRouter

from harness_router.config import MODEL_PRICING_CATALOG, config
from harness_router.pricing import PricingEngine
from harness_router.providers import ModelProviderClient


class ModelRouterService(ModelRouter):
    """Section 3 Model Router implementation of ModelRouter Protocol."""

    def __init__(self, client: ModelProviderClient | None = None) -> None:
        self.client = client or ModelProviderClient()
        self.role_models: dict[ModelRole, str] = {
            ModelRole.PRIMARY: config.default_primary_model,
            ModelRole.REVIEWER: config.default_reviewer_model,
        }
        self.call_history: list[dict] = []

    def set_role_model(self, role: ModelRole, model_name: str) -> None:
        self.role_models[role] = model_name

    def call_model(
        self,
        agent_id: str,
        role: ModelRole,
        messages: list[dict[str, str]],
        opts: ModelCallOpts | None = None,
    ) -> ModelResult:
        model = self.role_models.get(role, config.default_primary_model)
        result = self.client.call(model=model, messages=messages, opts=opts)

        self.call_history.append(
            {
                "agent_id": agent_id,
                "role": role,
                "model": model,
                "costUsd": result.costUsd,
                "latencyMs": result.latencyMs,
            }
        )
        return result

    def estimate_cost(self, role: ModelRole, input_tokens: int, output_tokens: int) -> float:
        model = self.role_models.get(role, config.default_primary_model)
        return PricingEngine.calculate_cost(model, input_tokens, output_tokens)

    def list_available_models(self) -> list[str]:
        return list(MODEL_PRICING_CATALOG.keys())
