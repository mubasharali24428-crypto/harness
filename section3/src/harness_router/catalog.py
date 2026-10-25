"""E-cat — declarative provider catalog.

Extends the pricing catalog with provider routing metadata: which base URL /
API-key env each provider uses, and which local models are valid fallback
candidates. S4's engine reads the fallback list from here (env still wins for
one-off overrides); S3's API exposes the catalog so the UI can render options.

The list itself lives in `provider_registry.py` — this module adapts it to the
shape consumers already depend on, and unions in the priced model ids so a
model that has a price but no registry entry still shows up.

Declarative only — no network at import time, no secrets read at import time.
"""

from pydantic import BaseModel, Field

from harness_router import provider_registry
from harness_router.config import MODEL_PRICING_CATALOG


class ProviderEntry(BaseModel):
    """One provider in the catalog."""

    name: str
    kind: str  # "api" | "local"
    base_url_env: str | None = None  # env var holding the endpoint, if any
    api_key_env: str | None = None  # env var holding the key, if any
    models: list[str] = Field(default_factory=list)


#: The historical pseudo-provider: every zero-priced model in the pricing
#: catalog, reachable through the deployment's OpenAI-compatible endpoint
#: (Ollama in dev). S4's offline fallback list is built from this, so it stays
#: byte-identical to the pre-registry catalog.
_LOCAL_ENTRY_NAME = "local"


def _priced_models_for(prefix: str) -> list[str]:
    return [m for m in MODEL_PRICING_CATALOG if m.startswith(f"{prefix}/")]


def _zero_priced_models() -> list[str]:
    return [
        m
        for m, p in MODEL_PRICING_CATALOG.items()
        if p.input_cost_per_million == 0.0 and p.output_cost_per_million == 0.0
    ]


class ProviderCatalog:
    """Static catalog + lookup helpers. Replaceable wholesale (frozen-contract
    discipline: consumers depend on the helper methods, not the dict shape)."""

    def __init__(self, entries: list[ProviderEntry]) -> None:
        self._entries = entries
        self._by_name = {e.name: e for e in entries}

    @classmethod
    def default(cls) -> "ProviderCatalog":
        entries: list[ProviderEntry] = []
        for provider in provider_registry.providers():
            models = list(dict.fromkeys([*provider.models, *_priced_models_for(provider.name)]))
            entries.append(
                ProviderEntry(
                    name=provider.name,
                    kind="local" if provider.local else "api",
                    base_url_env=provider.base_url_env,
                    api_key_env=provider.api_key_env,
                    models=models,
                )
            )
        # Providers that have priced models but no endpoint entry yet (a lab we
        # can quote but not route to). Kept visible so pricing lookups resolve.
        listed = {e.name for e in entries}
        for prefix in {m.split("/", 1)[0] for m in MODEL_PRICING_CATALOG}:
            if prefix in listed or prefix == _LOCAL_ENTRY_NAME:
                continue
            entries.append(
                ProviderEntry(
                    name=prefix,
                    kind="api",
                    base_url_env=f"{prefix.upper()}_BASE_URL",
                    api_key_env=f"{prefix.upper()}_API_KEY",
                    models=_priced_models_for(prefix),
                )
            )
        entries.append(
            ProviderEntry(
                name=_LOCAL_ENTRY_NAME,
                kind="local",
                base_url_env=provider_registry.LEGACY_BASE_URL_ENV,
                api_key_env=None,
                models=_zero_priced_models(),
            )
        )
        return cls(entries)

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
        """Models S4 may fall back to when no env override exists: everything
        served by a `kind="local"` entry that costs nothing per token.

        "Costs nothing" means either priced at zero in the pricing catalog, or
        absent from it — a model served by Ollama/vLLM/LM Studio on the
        operator's own hardware has no per-token price to look up, and an
        unpriced model must never be mistaken for a billable one.
        """
        candidates: list[str] = []
        for entry in self._entries:
            if entry.kind != "local":
                continue
            for model in entry.models:
                pricing = MODEL_PRICING_CATALOG.get(model)
                free = pricing is None or (
                    pricing.input_cost_per_million == 0.0
                    and pricing.output_cost_per_million == 0.0
                )
                if free:
                    candidates.append(model)
        return candidates

    def self_hosted_providers(self) -> list[ProviderEntry]:
        """Runtimes that serve weights on the operator's own machine."""
        return [
            e
            for e in self._entries
            if e.kind == "local" and e.name != _LOCAL_ENTRY_NAME
        ]


catalog = ProviderCatalog.default()
