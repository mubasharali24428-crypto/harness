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
        # S-02: a team's override never leaks into another team's calls.
        self.team_role_models: dict[tuple[str, ModelRole], str] = {}
        self.call_history: list[dict] = []

    def set_role_model(self, role: ModelRole, model_name: str, team_id: str | None = None) -> None:
        if team_id is None:
            self.role_models[role] = model_name
        else:
            self.team_role_models[(team_id, role)] = model_name

    def team_override(self, role: ModelRole, team_id: str | None) -> str | None:
        """The team's in-process override, if one was set this lifetime."""
        if team_id is None:
            return None
        return self.team_role_models.get((team_id, role))

    def _role_model(self, role: ModelRole, team_id: str | None) -> str:
        if team_id is not None and (team_id, role) in self.team_role_models:
            return self.team_role_models[(team_id, role)]
        return self.role_models.get(role, config.default_primary_model)

    def call_model(
        self,
        agent_id: str,
        role: ModelRole,
        messages: list[dict[str, str]],
        opts: ModelCallOpts | None = None,
        api_key: str | None = None,
        model_name: str | None = None,
        team_id: str | None = None,
    ) -> ModelResult:
        """One provider call. ``api_key`` is the team BYO secret — it goes to
        the provider layer only, never into history or logs. ``model_name``
        overrides the role default (the ``model`` REST body field)."""
        in_catalog = model_name in MODEL_PRICING_CATALOG
        model = model_name if in_catalog else self._role_model(role, team_id)
        result = self.client.call(model=model, messages=messages, opts=opts, api_key=api_key)

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

    def estimate_cost(
        self, role: ModelRole, input_tokens: int, output_tokens: int, team_id: str | None = None
    ) -> float:
        model = self._role_model(role, team_id)
        # Wire edge: /estimate-cost answers in JSON numbers; math stays exact.
        return float(PricingEngine.calculate_cost(model, input_tokens, output_tokens))

    def list_available_models(self) -> list[str]:
        return list(MODEL_PRICING_CATALOG.keys())
