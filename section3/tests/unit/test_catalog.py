"""E-cat — provider catalog tests."""

from harness_router.catalog import ProviderCatalog, ProviderEntry
from harness_router.config import MODEL_PRICING_CATALOG


def test_default_catalog_covers_every_priced_model():
    catalog = ProviderCatalog.default()
    covered = {m for e in catalog.list_providers() for m in e.models}
    assert covered == set(MODEL_PRICING_CATALOG), "catalog drift: priced model without a provider"


def test_get_returns_entry_or_none():
    catalog = ProviderCatalog.default()
    deepseek = catalog.get("deepseek")
    assert deepseek is not None
    assert deepseek.kind == "api"
    assert deepseek.api_key_env == "DEEPSEEK_API_KEY"
    assert catalog.get("nonexistent") is None


def test_provider_for_model_routes_correctly():
    catalog = ProviderCatalog.default()
    assert catalog.provider_for_model("openai/gpt-4o").name == "openai"
    assert catalog.provider_for_model("local/custom").kind == "local"
    assert catalog.provider_for_model("unknown/model") is None


def test_local_fallback_candidates_are_free_models():
    catalog = ProviderCatalog.default()
    candidates = catalog.local_fallback_candidates()
    assert candidates, "local provider must expose fallback candidates"
    for m in candidates:
        assert m.startswith("local/")
        pricing = MODEL_PRICING_CATALOG[m]
        assert pricing.input_cost_per_million == 0.0


def test_custom_catalog_replaceable():
    custom = ProviderCatalog(
        [
            ProviderEntry(name="only", kind="api", models=["m/1"]),
            ProviderEntry(name="loc", kind="local", models=["m/free"]),
        ]
    )
    assert [e.name for e in custom.list_providers()] == ["only", "loc"]
    assert custom.local_fallback_candidates() == ["m/free"]
    assert custom.provider_for_model("m/1").name == "only"
