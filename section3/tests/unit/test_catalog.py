"""E-cat — provider catalog tests."""

from harness_router.catalog import ProviderCatalog, ProviderEntry
from harness_router.config import MODEL_PRICING_CATALOG
from harness_router.provider_registry import providers


def test_default_catalog_covers_every_priced_model():
    """The drift guard: a priced model with no provider entry is a bug. The
    registry carries far more models than the price list, so coverage is a
    superset, not an equality."""
    catalog = ProviderCatalog.default()
    covered = {m for e in catalog.list_providers() for m in e.models}
    missing = set(MODEL_PRICING_CATALOG) - covered
    assert not missing, f"catalog drift: priced model without a provider: {sorted(missing)}"


def test_registry_is_a_broad_provider_set():
    """The 2026-10-25 requirement: at least fifty providers, closed and open,
    from more than one region — and the list stays a convenience, not a
    ceiling (see provider_of() falling back for unknown prefixes)."""
    entries = providers()
    assert len(entries) >= 50, f"only {len(entries)} providers registered"
    names = [e.name for e in entries]
    assert len(set(names)) == len(names), "duplicate provider name"
    for entry in entries:
        assert entry.base_url.startswith(("http://", "https://")), entry.name
        assert entry.protocol in ("openai", "anthropic", "gemini"), entry.name
        if not entry.local:
            assert entry.api_key_env, f"{entry.name} has no key env"
    assert len({e.region for e in entries}) >= 6, "providers all claim one region"
    assert any(e.open_weights for e in entries), "no open-weight provider registered"


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
        pricing = MODEL_PRICING_CATALOG.get(m)
        # Absent from the price list means a self-hosted runtime: free by
        # definition. Present means it must actually be priced at zero.
        assert pricing is None or (
            pricing.input_cost_per_million == 0.0
            and pricing.output_cost_per_million == 0.0
        ), f"{m} is billable and must not be an offline fallback"
    assert any(m.startswith("local/") for m in candidates)


def test_self_hosted_runtimes_are_listed_separately():
    catalog = ProviderCatalog.default()
    names = {e.name for e in catalog.self_hosted_providers()}
    assert {"ollama", "vllm", "lmstudio"} <= names
    assert "local" not in names  # the pseudo-provider is not a runtime


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
