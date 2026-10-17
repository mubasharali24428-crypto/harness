"""Token-budgeted context ranking (F016).

Turns a goal string into a ranked, budget-fitted context bundle:

1. **Query variants** — the raw goal plus a deduplicated keyword variant built
   from :func:`harness_context.embeddings.tokenize` (camelCase/snake_case
   split, stopwords dropped). One embedding call for ALL variants, so ranking
   costs one round-trip regardless of variant count.
2. **Score** — a chunk's score is its best cosine across variants; the
   winning variant is recorded as the reason ("goal" or "keywords"), and the
   matched query itself is kept on the chunk for explainability.
3. **Threshold** — chunks below ``min_score`` (default 0.05) are excluded and
   counted as ``below_threshold``, never silently padded in.
4. **Budget** — greedy fill in score order with an ESTIMATED token count
   (``len(text) // 4``, capped ≥1); an oversized chunk is skipped and the
   next one gets its chance. ``dropped_for_budget`` counts what did not fit —
   callers can widen the budget or re-query instead of debugging a silent
   truncation.

Deterministic: ties break by (path, start_line). No DB access here — pure
scoring over rows, so it unit-tests without fixtures; the service method
``rank()`` wires it to storage + embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .embeddings import tokenize

MIN_SCORE = 0.05
CHARS_PER_TOKEN = 4  # conservative estimate; we never claim exact tokens
MAX_KEYWORD_TERMS = 12


@dataclass(frozen=True)
class RankedChunk:
    path: str
    lang: str
    start_line: int
    end_line: int
    content: str
    score: float
    approx_tokens: int
    reason: str  # "goal" | "keywords"
    matched_query: str

    def to_wire(self) -> dict:
        return {
            "path": self.path,
            "lang": self.lang,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "content": self.content,
            "score": self.score,
            "approxTokens": self.approx_tokens,
            "reason": self.reason,
            "matchedQuery": self.matched_query,
        }


@dataclass
class RankedContext:
    project_id: str
    queries: list[str]
    chunks: list[RankedChunk] = field(default_factory=list)
    used_tokens: int = 0
    max_tokens: int = 0
    scored_candidates: int = 0
    below_threshold: int = 0
    dropped_for_budget: int = 0

    def to_wire(self) -> dict:
        return {
            "projectId": self.project_id,
            "queries": self.queries,
            "maxTokens": self.max_tokens,
            "usedTokens": self.used_tokens,
            "scoredCandidates": self.scored_candidates,
            "belowThreshold": self.below_threshold,
            "droppedForBudget": self.dropped_for_budget,
            "chunks": [c.to_wire() for c in self.chunks],
        }


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def derive_queries(goal: str, max_terms: int = MAX_KEYWORD_TERMS) -> list[str]:
    """Goal plus a keyword variant; keyword variant omitted when redundant."""
    queries = []
    goal = (goal or "").strip()
    if goal:
        queries.append(goal)
    terms: list[str] = []
    seen: set[str] = set()
    for token in tokenize(goal):
        if token not in seen:
            seen.add(token)
            terms.append(token)
        if len(terms) >= max_terms:
            break
    if terms:
        keyword_query = " ".join(terms)
        if keyword_query != goal:
            queries.append(keyword_query)
    return queries


def rank(
    rows: list[dict],
    query_vecs: list[list[float]],
    queries: list[str],
    *,
    max_tokens: int,
    top_k: int = 12,
    min_score: float = MIN_SCORE,
) -> RankedContext:
    """Score ``rows`` against ``query_vecs`` (aligned with ``queries``), budget-fill."""
    from .service import _cosine  # local import: avoids module-load cycle

    ranked: list[RankedChunk] = []
    for row in rows:
        best = -1.0
        best_idx = -1
        for idx, qvec in enumerate(query_vecs):
            score = _cosine(qvec, row["vec"])
            if score > best:
                best = score
                best_idx = idx
        if best < min_score or best_idx < 0:
            continue
        kind = "goal" if best_idx == 0 and queries and queries[0] else "keywords"
        ranked.append(
            RankedChunk(
                path=row["path"],
                lang=row["lang"],
                start_line=row["startLine"],
                end_line=row["endLine"],
                content=row["content"],
                score=best,
                approx_tokens=estimate_tokens(row["content"]),
                reason=kind,
                matched_query=queries[best_idx],
            )
        )

    ranked.sort(key=lambda c: (-c.score, c.path, c.start_line))

    scored = len(ranked)
    below = len(rows) - scored
    included: list[RankedChunk] = []
    used = 0
    for chunk in ranked:
        if len(included) >= top_k:
            break
        if used + chunk.approx_tokens > max_tokens:
            continue  # smaller later chunk may still fit
        included.append(chunk)
        used += chunk.approx_tokens

    return RankedContext(
        project_id="",
        queries=queries,
        chunks=included,
        used_tokens=used,
        max_tokens=max_tokens,
        scored_candidates=scored,
        below_threshold=below,
        dropped_for_budget=max(0, scored - len(included)),
    )
