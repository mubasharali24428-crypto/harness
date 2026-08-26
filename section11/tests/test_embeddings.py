"""Embedding backend contract: determinism, pluggability, config resolution."""

import pytest

from harness_context.embeddings import (
    HashingEmbedding,
    OllamaEmbedding,
    make_backend,
    tokenize,
)


async def test_hashing_is_deterministic_and_normalized():
    a = await HashingEmbedding(dims=64).embed(["widget store quota"])
    b = await HashingEmbedding(dims=64).embed(["widget store quota"])
    assert a[0] == b[0]
    norm = sum(v * v for v in a[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-6  # cosine geometry holds


def test_tokenize_splits_camel_and_snake_and_drops_stopwords():
    toks = tokenize("getWidgetStore the MAX_WIDGETS")
    assert "get" in toks and "widget" in toks and "store" in toks
    assert "max" in toks and "widgets" in toks
    assert "the" not in toks  # stopword


def test_make_backend_known_names_and_rejection():
    assert isinstance(make_backend("hashing", dims=32), HashingEmbedding)
    ollama = make_backend("ollama")
    assert isinstance(ollama, OllamaEmbedding)
    with pytest.raises(ValueError):
        make_backend("vendor-x")


def test_ollama_backend_configuration_surface():
    """Pluggability contract for vendors: model + base_url configurable, dims
    resolved live on first embed (never hardcoded per vendor)."""
    be = OllamaEmbedding(model="nomic-embed-text", base_url="http://127.0.0.1:11434/")
    assert be.model == "nomic-embed-text"
    assert be.base_url == "http://127.0.0.1:11434"
    assert be.dims == -1  # unknown until first response


def test_config_default_is_local_ollama_never_a_vendored_sdk(settings):
    from harness_context.config import Settings

    defaults = Settings()
    assert defaults.embedding_backend == "ollama"  # local models per AGENTS.md env
    # env override path used by CI/tests:
    overridden = Settings(embedding_backend="hashing")
    assert overridden.embedding_backend == "hashing"


async def test_empty_batch_short_circuits_all_backends():
    assert await HashingEmbedding().embed([]) == []
    assert await OllamaEmbedding().embed([]) == []
