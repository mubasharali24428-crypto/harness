"""Pluggable embedding backends for the Context Pillar.

The interface is small and final (W21-C1 spirit): an EmbeddingBackend turns a
list of chunk texts into fixed-dimension float vectors, in order.

    class MyVendorBackend(EmbeddingBackend):
        dims = 1536
        async def embed(self, texts): ...

Selection is config-driven — never a hardcoded vendor. `make_backend()`
resolves the configured name (Settings.embedding_backend in config.py; env
HARNESS_EMBEDDING_BACKEND):

  * "hashing" — deterministic feature-hashing vectors (default for tests and
    CI; zero network, zero deps). NOT semantically strong; it exists so the
    pipeline is runnable end-to-end anywhere.
  * "ollama"  — local Ollama /api/embed (default for dev/prod per AGENTS.md
    environment); set HARNESS_OLLAMA_MODEL to pick the model.

Vendors: subclass EmbeddingBackend, set dims, implement async embed(), and pass
the instance explicitly (service.py takes the backend as an argument). Keep
batching/rate limits inside your embed() — the pipeline only awaits it once per
file.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Tokenization shared by local backends. Deliberately dependency-free: no
# tokenizer package may become load-bearing in the hot path of a pure-Python
# fallback backend.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have if in into is it its of on "
    "or that the their this to was were will with without".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase identifier/number tokens, stopwords dropped, camelCase split."""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        lowered = raw.lower()
        if lowered in _STOPWORDS:
            continue
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", raw)
        # split snake_case remnants too
        for part in parts:
            tokens.extend(p for p in part.split("_") if p)
        if not parts:
            tokens.append(lowered)
    return [t.lower() for t in tokens]


class EmbeddingError(RuntimeError):
    """Raised when a backend cannot produce vectors (network, model missing...)."""


class EmbeddingBackend:
    """Contract every backend implements. Subclass + override dims/embed."""

    dims: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class HashingEmbedding(EmbeddingBackend):
    """Deterministic hashed bag-of-words vectors.

    Each token hashes to two bucket positions (+1/-1 signed) across a D-dim
    plane; the sum is L2-normalized. Same text -> same vector forever (pure
    function), which makes it the reference backend for deterministic tests.
    """

    def __init__(self, dims: int = 256) -> None:
        self.dims = dims

    def _positions(self, token: str) -> tuple[int, int]:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        n = int.from_bytes(digest, "big")
        return n % self.dims, (n // self.dims) % self.dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dims
            counts: dict[str, int] = {}
            for tok in tokenize(text):
                counts[tok] = counts.get(tok, 0) + 1
            for tok, count in counts.items():
                p, q = self._positions(tok)
                sign = 1.0 if p == q else (-1.0 if q % 2 else 1.0)
                weight = 1.0 + math.log(count)
                vec[p] += sign * weight
                if q != p:
                    vec[q] -= sign * weight * 0.5
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            out.append(vec)
        return out


class OllamaEmbedding(EmbeddingBackend):
    """Local Ollama embeddings via POST {base_url}/api/embed.

    AGENTS.md environment default: models run on 127.0.0.1:11434. Model choice
    via HARNESS_CONTEXT_OLLAMA_MODEL (e.g. "nomic-embed-text"); dims are read
    live from the first response so a model swap can't silently mismatch the
    pgvector column.
    """

    def __init__(
        self, model: str = "nomic-embed-text", base_url: str = "http://127.0.0.1:11434"
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.dims = -1  # resolved on first embed()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import asyncio

        if not texts:
            return []
        body = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def _post() -> dict[str, Any]:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                return json.loads(resp.read())

        data = await asyncio.to_thread(_post)
        if "error" in data:
            raise EmbeddingError(f"ollama: {data['error']}")
        embeddings = data.get("embeddings") or []
        if len(embeddings) != len(texts):
            raise EmbeddingError(
                f"ollama returned {len(embeddings)} vectors for {len(texts)} inputs"
            )
        if not embeddings or not embeddings[0]:
            raise EmbeddingError("ollama returned empty embeddings (is the model pulled?)")
        self.dims = len(embeddings[0])
        return [[float(x) for x in vec] for vec in embeddings]


_BACKEND_ALIASES: dict[str, str] = {"": "hashing", "test": "hashing"}


def make_backend(name: str | None = None, **kwargs: Any) -> EmbeddingBackend:
    """Resolve a backend by configured name. Unknown names raise ValueError."""
    key = (_BACKEND_ALIASES.get(name or "", name or "")).lower()
    if key == "hashing":
        return HashingEmbedding(dims=int(kwargs.get("dims", 256)))
    if key == "ollama":
        return OllamaEmbedding(
            model=str(kwargs.get("model") or "nomic-embed-text"),
            base_url=str(kwargs.get("base_url") or "http://127.0.0.1:11434"),
        )
    raise ValueError(
        f"unknown embedding backend {name!r} (known: hashing, ollama)"
    )
