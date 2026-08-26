"""M21-X4 — retrieval-augmented planner tests.

The exit criterion, proven here not claimed: a plan proposed against a SEEDED
index derives its steps from the retrieved chunks — every step cites the real
``path:startLine-endLine`` location of an indexed chunk.

Stack: Section 11's REAL ContextService on in-memory SQLite (aiosqlite) with
the deterministic HashingEmbedding backend — same fixtures philosophy as
section11/tests/conftest.py, zero network, no pgvector needed. Section 4 code
under test never imports harness_context (sections import ONLY
harness_contracts); the test drives the frozen W21-C1 seam from the outside.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Bootstrap Section 11 onto the path RELATIVE to this file so the test does not
# depend on where the monorepo is checked out.
_SECTION4_ROOT = Path(__file__).resolve().parents[2]
_SECTION11_SRC = _SECTION4_ROOT.parent / "section11" / "src"
sys.path.insert(0, str(_SECTION11_SRC))

from harness_context.embeddings import HashingEmbedding  # noqa: E402
from harness_context.models import ScoredChunk  # noqa: E402
from harness_context.service import IndexJob  # noqa: E402
from harness_context.sql import DDL_SQLITE  # noqa: E402

from harness_orchestrator.planner import (  # noqa: E402
    DEFAULT_CONTEXT_TOKEN_BUDGET,
    DEFAULT_SCORE_THRESHOLD,
    PlanService,
    apply_context_budget,
    filter_by_score,
    format_citation,
)

PROJECT = "p-x4"
QUERY = "widget store quota"
CITATION_RE = re.compile(
    r"(?P<path>[\w./\-]+\.(?:py|ts|tsx|js|jsx|md|sql|json)):(?P<start>\d+)-(?P<end>\d+)"
)


# --------------------------------------------------------------------- fixtures


def _write_seed_repo(root: Path) -> None:
    """Two-file mini-repo: one widget-quota module (on-topic), one telemetry
    emitter sharing no vocabulary with QUERY."""
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "widget_store.py").write_text(
        '"""Widget storage with a hard per-team quota."""\n'
        "\n"
        "MAX_WIDGETS = 1000\n"
        "\n"
        "\n"
        "class WidgetStore:\n"
        "    def __init__(self, quota: int = MAX_WIDGETS):\n"
        "        self.quota = quota\n"
        '        self.widgets: dict[str, dict] = {}\n'
        "\n"
        "    def put(self, widget_id: str, payload: dict) -> bool:\n"
        '        """Store a widget; refuse once the quota is exhausted."""\n'
        "        if len(self.widgets) >= self.quota:\n"
        "            return False\n"
        "        self.widgets[widget_id] = payload\n"
        "        return True\n",
        encoding="utf-8",
    )
    (pkg / "telemetry.py").write_text(
        '"""Telemetry emitter for journey counters."""\n'
        "\n"
        "def emit(event_name: str, attrs: dict) -> None:\n"
        '    print("event", event_name)\n',
        encoding="utf-8",
    )


@pytest.fixture
async def seeded_index(tmp_path: Path):
    """REAL X1 pipeline: ingest the mini-repo into SQLite, expose search()."""
    from harness_context.service import ContextService as _C  # imported post-bootstrap

    repo = tmp_path / "minirepo"
    _write_seed_repo(repo)
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(text(DDL_SQLITE))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = _C(factory, HashingEmbedding(dims=256), window=6, overlap=2)
    await svc.reindex(IndexJob(id="j-x4", project_id=PROJECT, repo_path=str(repo)), repo)

    async def search(query: str, top_k: int = 8) -> list[ScoredChunk]:
        async with factory() as session:
            return await svc.search(await session.connection(), query, PROJECT, top_k=top_k)

    yield search
    await engine.dispose()


def _chunk(
    path: str,
    score: float,
    content: str = "relevant body",
    start: int = 1,
    end: int = 10,
    lang: str = "python",
) -> ScoredChunk:
    return ScoredChunk.model_construct(
        path=path, lang=lang, start_line=start, end_line=end, content=content, score=score
    )


# ------------------------------------------------- exit criterion (gate law)


async def test_plan_steps_cite_real_seeded_index_paths(seeded_index):
    """THE gate: proposed plan steps cite the seeded index's REAL file paths."""
    bundle = await seeded_index(QUERY, top_k=8)
    relevant = {h.path for h in bundle if h.score >= DEFAULT_SCORE_THRESHOLD}
    assert relevant == {"pkg/widget_store.py"}  # sanity: seed ranks as designed

    service = PlanService()
    _plan_id, plan = service.create_plan(
        goal="Raise the per-team widget quota and enforce it in WidgetStore.put",
        context_bundle=bundle,
    )

    assert plan.steps, "retrieval-augmented plan must have derived steps"
    joined = "\n".join(plan.steps)
    citations = CITATION_RE.findall(joined)
    assert citations, f"no path:line citations in steps:\n{joined}"

    # Every citation points at a REAL indexed location...
    real_locations = {(h.path, h.start_line, h.end_line) for h in bundle}
    for path, start, end in [(c[0], int(c[1]), int(c[2])) for c in citations]:
        assert (
            path,
            start,
            end,
        ) in real_locations, f"cites unindexed location {path}:{start}-{end}"

    # ...and covers every above-threshold retrieved file (demonstrably USED).
    cited_paths = {c[0] for c in citations}
    assert relevant <= cited_paths

    # Retrieved paths also populate filesLikelyToChange.
    assert plan.filesLikelyToChange == ["pkg/widget_store.py"]

    # No hallucinated or off-topic files leak into the plan.
    assert "telemetry" not in joined


async def test_derived_citations_match_chunk_line_ranges_exactly(seeded_index):
    """format_citation round-trips through the frozen W21-C1 shape."""
    bundle = await seeded_index(QUERY, top_k=1)
    top = bundle[0]
    assert format_citation(top) == f"{top.path}:{top.start_line}-{top.end_line}"


# ------------------------------------------------------- threshold drop gate


def test_filter_by_score_drops_below_threshold():
    chunks = [_chunk("a.py", 0.61), _chunk("b.py", 0.30), _chunk("c.py", 0.29)]
    assert [c.path for c in filter_by_score(chunks)] == ["a.py", "b.py"]
    assert [c.path for c in filter_by_score(chunks, min_score=0.5)] == ["a.py"]


async def test_create_plan_applies_threshold_before_step_derivation():
    low = _chunk("pkg/noise.py", 0.05, content="unrelated noise")
    high = _chunk("pkg/widget_store.py", 0.62, content="WidgetStore quota logic")
    service = PlanService()
    _plan_id, plan = service.create_plan(
        goal="g", context_bundle=[low, high], min_chunk_score=0.5
    )
    joined = "\n".join(plan.steps)
    assert "pkg/widget_store.py:" in joined
    assert "pkg/noise.py" not in joined
    assert plan.filesLikelyToChange == ["pkg/widget_store.py"]


async def test_all_chunks_below_threshold_falls_back_to_legacy_steps():
    below = [_chunk("a.py", 0.10), _chunk("b.py", 0.20)]
    service = PlanService()
    _plan_id, plan = service.create_plan(goal="g", context_bundle=below)
    # Nothing survived the gate -> byte-identical legacy defaults.
    assert plan.steps == ["Inspect codebase", "Apply edits", "Run unit tests"]
    assert plan.filesLikelyToChange == []


# --------------------------------------------------------- token budget mgmt


def test_apply_context_budget_caps_prompt_plus_chunks():
    big = _chunk("big.py", 0.9, content="x" * 400)  # 100 tokens
    small = _chunk("small.py", 0.8, content="y" * 40)  # 10 tokens
    prompt = "z" * 40  # 10 tokens

    kept = apply_context_budget([big, small], prompt_text=prompt, token_budget=115)
    # 10 (prompt) + 100 (big) = 110 <= 115; adding small would exceed.
    assert [c.path for c in kept] == ["big.py"]

    kept_all = apply_context_budget([big, small], prompt_text=prompt, token_budget=120)
    assert [c.path for c in kept_all] == ["big.py", "small.py"]  # 10+100+10 == cap

    kept_none = apply_context_budget([big], prompt_text="p" * 400, token_budget=50)
    assert kept_none == []  # nothing fits -> caller falls back to legacy steps


def test_apply_context_budget_drop_order_is_lowest_score_first():
    chunks = [
        _chunk("lo.py", 0.10, content="a" * 200),
        _chunk("hi.py", 0.90, content="b" * 200),
        _chunk("mid.py", 0.50, content="c" * 200),
    ]
    kept = apply_context_budget(chunks, prompt_text="", token_budget=100)
    # 50-token budget admits hi, then mid; lo (lowest score) is dropped first.
    assert [c.path for c in kept] == ["hi.py", "mid.py"]
    assert [c.score for c in kept] == sorted((c.score for c in kept), reverse=True)


async def test_create_plan_respects_per_service_budget():
    fat = _chunk("fat.py", 0.80, content="f" * 400)  # 100 tokens
    thin = _chunk("thin.py", 0.60, content="t" * 40)  # 10 tokens
    service = PlanService(context_token_budget=105)  # prompt + thin fit; fat cannot
    _plan_id, plan = service.create_plan(
        goal="g", context_bundle=[fat, thin]
    )
    joined = "\n".join(plan.steps)
    assert "thin.py:" in joined
    assert "fat.py" not in joined
    # Budget ceiling held: derived step text stays within the cap (char/4).
    assert (len(joined) + 3) // 4 <= DEFAULT_CONTEXT_TOKEN_BUDGET


# ------------------------------------------- shape tolerance (X2 coordination)


async def test_accepts_wire_shape_dicts_like_x2_resolver_output():
    """X2 hands over camelCase wire dicts OR snake_case models — both work."""
    as_dict = {
        "path": "pkg/widget_store.py",
        "lang": "python",
        "startLine": 7,
        "endLine": 12,
        "content": "class WidgetStore quota",
        "score": 0.55,
    }
    _plan_id, plan = PlanService().create_plan(goal="g", context_bundle=[as_dict])
    assert "pkg/widget_store.py:7-12" in "\n".join(plan.steps)


async def test_object_chunks_and_dict_chunks_yield_identical_plans():
    obj = _chunk("pkg/a.py", 0.55, content="same body", start=3, end=9)
    as_dict = {
        "path": "pkg/a.py",
        "lang": "python",
        "startLine": 3,
        "endLine": 9,
        "content": "same body",
        "score": 0.55,
    }
    p_obj = PlanService().create_plan(goal="g", context_bundle=[obj])[1]
    p_dict = PlanService().create_plan(goal="g", context_bundle=[as_dict])[1]
    assert p_obj.steps == p_dict.steps
    assert p_obj.filesLikelyToChange == p_dict.filesLikelyToChange


# --------------------------------------------------- no-context fallback gate


def test_no_context_call_signature_unchanged():
    """Legacy positional/keyword call without any context kwargs is untouched."""
    service = PlanService()
    plan_id, plan = service.create_plan(
        goal="Fix the login bug",
        files_likely_to_change=["src/auth.py"],
        estimated_cost_usd=0.07,
    )
    assert plan_id.startswith("plan-")
    assert plan.goal == "Fix the login bug"
    assert plan.filesLikelyToChange == ["src/auth.py"]
    assert plan.steps == ["Inspect codebase", "Apply edits", "Run unit tests"]
    assert plan.tests == ["pytest tests/"]
    assert plan.estimatedCostUsd == 0.07
    assert plan.forbiddenPaths == []
    assert plan.expectedResult == "All tests pass and changes conform to brief."


def test_empty_bundle_behaves_as_no_context():
    _plan_id, plan = PlanService().create_plan(goal="g", context_bundle=[])
    assert plan.steps == ["Inspect codebase", "Apply edits", "Run unit tests"]
    assert plan.filesLikelyToChange == []


async def test_accepts_x2_context_bundle_wrapper_object():
    """X2's ContextBundle carries .chunks — create_plan unwraps it directly."""
    from types import SimpleNamespace

    bundle = SimpleNamespace(
        chunks=[_chunk("pkg/widget_store.py", 0.55, start=7, end=12)],
        max_tokens=2048,
        dropped_chunks=0,
    )
    _plan_id, plan = PlanService().create_plan(goal="g", context_bundle=bundle)
    assert "pkg/widget_store.py:7-12" in "\n".join(plan.steps)


async def test_end_to_end_seeded_index_to_approved_plan(seeded_index):
    """Full lane flow: seed -> retrieve -> plan with citations -> approval."""
    bundle = await seeded_index(QUERY, top_k=8)
    service = PlanService()
    plan_id, plan = service.create_plan(goal="Adjust widget quota handling", context_bundle=bundle)
    assert any(CITATION_RE.search(s) for s in plan.steps)
    assert service.approve_plan(plan_id) == "approved"
    assert service.is_approved(plan_id)
