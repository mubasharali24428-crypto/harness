import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from harness_contracts.plan import AgentPlan

DEFAULT_SCORE_THRESHOLD = 0.30
"""Relevance floor: chunks under this cosine score never reach the model call."""

DEFAULT_CONTEXT_TOKEN_BUDGET = 4_000
"""Per-plan ceiling on prompt + retrieved-bundle tokens, combined."""

_CHARS_PER_TOKEN = 4
_STEP_CHUNK_CAP = 6  # steps derived from retrieval stay readable


class PlanNotApprovedError(Exception):
    """Raised when code execution is attempted before plan approval."""

    pass


def _estimate_tokens(text: str) -> int:
    """Deterministic char/4 estimate — no tokenizer dependency at plan time."""
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _field(chunk: Any, snake: str, camel: str) -> Any:
    """Read one W21-C1 field off a ScoredChunk object or its wire-shape dict.

    X2's resolver hands over ScoredChunk instances (snake_case attrs) or the
    camelCase wire dicts; the planner accepts either without importing
    Section 11 (sections import ONLY harness_contracts).
    """
    if isinstance(chunk, Mapping):
        return chunk.get(snake, chunk.get(camel))
    value = getattr(chunk, snake, None)
    return value if value is not None else getattr(chunk, camel, None)


def format_citation(chunk: Any) -> str:
    """Citation form embedded in step text: ``path:startLine-endLine``."""
    return (
        f"{_field(chunk, 'path', 'path')}:"
        f"{_field(chunk, 'start_line', 'startLine')}-{_field(chunk, 'end_line', 'endLine')}"
    )


def filter_by_score(
    chunks: Sequence[Any], min_score: float = DEFAULT_SCORE_THRESHOLD
) -> list[Any]:
    """Retrieval quality gate: drop low-relevance chunks BEFORE the model call."""
    return [c for c in chunks if float(_field(c, "score", "score")) >= min_score]


def apply_context_budget(
    chunks: Sequence[Any],
    prompt_text: str = "",
    token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
) -> list[Any]:
    """Cap total tokens across bundle + prompt per plan; drop lowest-score first.

    Chunks are admitted in descending score order while prompt + admitted
    content stays within ``token_budget``. A chunk too large for the remaining
    room is skipped whole (never truncated mid-source), so one oversized
    high-score chunk cannot starve smaller relevant chunks behind it; across
    the ranked list, whatever must be discarded is always the lowest-scoring
    material.
    """
    ranked = sorted(chunks, key=lambda c: float(_field(c, "score", "score")), reverse=True)
    used = _estimate_tokens(prompt_text)
    kept: list[Any] = []
    for chunk in ranked:
        content = str(_field(chunk, "content", "content") or "")
        cost = _estimate_tokens(content)
        if used + cost > token_budget:
            continue
        kept.append(chunk)
        used += cost
    return kept


class PlanService:
    """Generates and manages execution plans matching brief fields."""

    def __init__(self, *, context_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET) -> None:
        self.plans: dict[str, AgentPlan] = {}
        self.plan_approvals: dict[str, bool] = {}
        self.context_token_budget = context_token_budget

    # ------------------------------------------------- retrieval-augmented (X4)

    def derive_retrieval_steps(self, chunks: Sequence[Any]) -> list[str]:
        """Turn retrieved chunks into plan steps that CITE path:line ranges."""
        steps: list[str] = []
        for chunk in chunks[:_STEP_CHUNK_CAP]:
            cite = format_citation(chunk)
            summary = " ".join(str(_field(chunk, "content", "content") or "").split())[:120]
            steps.append(f"Review {cite} — {summary}")
        return steps

    def create_plan(
        self,
        goal: str,
        files_likely_to_change: list[str] | None = None,
        steps: list[str] | None = None,
        tests: list[str] | None = None,
        risks: list[str] | None = None,
        estimated_cost_usd: float = 0.05,
        forbidden_paths: list[str] | None = None,
        expected_result: str = "All tests pass and changes conform to brief.",
        context_bundle: Any = None,  # chunk list, wire dicts, or X2 ContextBundle
        min_chunk_score: float = DEFAULT_SCORE_THRESHOLD,
    ) -> tuple[str, AgentPlan]:
        """Build an AgentPlan; when a resolved bundle is supplied, derive steps
        from it (retrieval-augmented planning).

        ``context_bundle`` is the X2 resolver's output shape: ScoredChunk-like
        objects (or wire dicts) with {path, lang, start_line, end_line, content,
        score}. Chunks under ``min_chunk_score`` are dropped before any step is
        derived; the survivors are budget-capped against the prompt and their
        citations become the plan's step text. Without a bundle (or if every
        chunk falls below threshold) behavior is byte-identical to the legacy
        no-context path.
        """
        effective_steps = steps
        effective_files: list[str] | None = files_likely_to_change
        if context_bundle:
            # Tolerate an X2 ContextBundle-style wrapper: use its .chunks list.
            bundle_items = getattr(context_bundle, "chunks", context_bundle)
            gated = filter_by_score(bundle_items, min_score=min_chunk_score)
            prompt_text = goal + "\n" + (expected_result or "")
            gated = apply_context_budget(
                gated, prompt_text=prompt_text, token_budget=self.context_token_budget
            )
            if gated:
                derived = self.derive_retrieval_steps(gated)
                if effective_files is None:
                    seen: set[str] = set()
                    unique_paths = [
                        p
                        for p in (str(_field(c, "path", "path")) for c in gated)
                        if not (p in seen or seen.add(p))
                    ]
                    effective_files = unique_paths
                effective_steps = derived
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        plan = AgentPlan(
            goal=goal,
            filesLikelyToChange=effective_files or [],
            steps=effective_steps or ["Inspect codebase", "Apply edits", "Run unit tests"],
            tests=tests or ["pytest tests/"],
            risks=risks or ["Regression in existing modules"],
            estimatedCostUsd=estimated_cost_usd,
            forbiddenPaths=forbidden_paths or [],
            expectedResult=expected_result,
        )
        self.plans[plan_id] = plan
        self.plan_approvals[plan_id] = False
        return plan_id, plan

    def get_plan(self, plan_id: str) -> AgentPlan:
        if plan_id not in self.plans:
            raise KeyError(f"Plan '{plan_id}' not found.")
        return self.plans[plan_id]

    def approve_plan(self, plan_id: str) -> str | None:
        """Flip approval True; returns ``"approved"`` when THIS call decided.

        M19-N1: already-approved plans short-circuit to ``None`` so the only
        caller that flips memory is the one that owns the decision — a
        replica converging on another replica's approval must not re-fire
        the PLAN_APPROVED emission.
        """
        if plan_id not in self.plans:
            raise KeyError(f"Plan '{plan_id}' not found.")
        if self.plan_approvals.get(plan_id):
            return None  # already approved: idempotent, no second decision
        self.plan_approvals[plan_id] = True
        return "approved"

    def reject_plan(self, plan_id: str) -> str | None:
        """False-out a decided plan; returns ``"rejected"`` when THIS call did.

        Same M19-N1 discipline as approve_plan: a plan that is absent or
        already rejected is a no-op returning ``None``; only the caller that
        actually flips True → False owns the rejection side effects.
        """
        if plan_id not in self.plan_approvals or not self.plan_approvals[plan_id]:
            return None
        self.plan_approvals[plan_id] = False
        return "rejected"

    def is_approved(self, plan_id: str) -> bool:
        return self.plan_approvals.get(plan_id, False)
