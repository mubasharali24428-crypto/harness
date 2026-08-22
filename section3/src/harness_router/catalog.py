"""E-cat — declarative provider catalog.

Extends the pricing catalog with provider routing metadata: which base URL /
API-key env each provider uses, and which local models are valid fallback
candidates. S4's engine reads the fallback list from here (env still wins for
one-off overrides); S3's API exposes the catalog so the UI can render options.

Declarative only — no network at import time, no secrets read at import time.
"""

from pydantic import BaseModel, Field

from harness_router.config import MODEL_PRICING_CATALOG


class ProviderEntry(BaseModel):
    """One provider in the catalog."""

    name: str
    kind: str  # "api" | "local"
    base_url_env: str | None = None  # env var holding the endpoint, if any
    api_key_env: str | None = None  # env var holding the key, if any
    models: list[str] = Field(default_factory=list)


class ProviderCatalog:
    """Static catalog + lookup helpers. Replaceable wholesale (frozen-contract
    discipline: consumers depend on the helper methods, not the dict shape)."""

    def __init__(self, entries: list[ProviderEntry]) -> None:
        self._entries = entries
        self._by_name = {e.name: e for e in entries}

    @classmethod
    def default(cls) -> "ProviderCatalog":
        return cls(
            [
                ProviderEntry(
                    name="deepseek",
                    kind="api",
                    base_url_env="DEEPSEEK_BASE_URL",
                    api_key_env="DEEPSEEK_API_KEY",
                    models=[
                        m
                        for m in MODEL_PRICING_CATALOG
                        if m.startswith("deepseek/")
                    ],
                ),
                ProviderEntry(
                    name="openai",
                    kind="api",
                    base_url_env="OPENAI_BASE_URL",
                    api_key_env="OPENAI_API_KEY",
                    models=[m for m in MODEL_PRICING_CATALOG if m.startswith("openai/")],
                ),
                ProviderEntry(
                    name="anthropic",
                    kind="api",
                    api_key_env="ANTHROPIC_API_KEY",
                    models=[
                        m for m in MODEL_PRICING_CATALOG if m.startswith("anthropic/")
                    ],
                ),
                ProviderEntry(
                    name="mistral",
                    kind="api",
                    api_key_env="MISTRAL_API_KEY",
                    models=[
                        m for m in MODEL_PRICING_CATALOG if m.startswith("mistral/")
                    ],
                ),
                ProviderEntry(
                    name="qwen",
                    kind="api",
                    api_key_env="DASHSCOPE_API_KEY",
                    models=[m for m in MODEL_PRICING_CATALOG if m.startswith("qwen/")],
                ),
                ProviderEntry(
                    name="local",
                    kind="local",
                    base_url_env="DEEPSEEK_BASE_URL",  # Ollama's OpenAI-compatible endpoint
                    models=[
                        m
                        for m in MODEL_PRICING_CATALOG
                        if MODEL_PRICING_CATALOG[m].input_cost_per_million == 0.0
                        and MODEL_PRICING_CATALOG[m].output_cost_per_million == 0.0
                    ],
                ),
            ]
        )

    def list_providers(self) -> list[ProviderEntry]:
        return list(self._entries)

    def get(self, name: str) -> ProviderEntry | None:
        return self._by_name.get(name)

    def provider_for_model(self, model: str) -> ProviderEntry | None:
        for entry in self._entries:
            if model in entry.models:
                return entry
        return None

    def local_fallback_candidates(self) -> list[str]:
        """Free/self-hosted models (zero-priced), deterministic catalog order.
        Used by S4 when no env override exists."""
        return [
            model
            for entry in self._entries
            if entry.kind == "local"
            for model in entry.models
        ]


catalog = ProviderCatalog.default()
