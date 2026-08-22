"""Unit tests for SummaryGenerator — heuristic path and Section 3 model path."""

import pytest

from harness_review.models import RiskSeverity
from harness_review.summary import SummaryGenerator
from tests.fixtures.golden_fixtures import GOLDEN_DIFF, GOLDEN_DIFF_RISK


def test_heuristic_summary_no_model_router():
    gen = SummaryGenerator(model_router=None)
    summary = gen.generate(
        diff_text=GOLDEN_DIFF,
        changed_files=["src/auth.py"],
        brief_goal="Add token verification helper",
        test_summary="passed",
    )
    assert summary.sections, "heuristic fallback must still produce sections"
    assert any("WHAT CHANGED" == sec.label for sec in summary.sections)
    assert any("Brief goal" in sec.text or sec.label == "WHY" for sec in summary.sections)
    assert summary.model is None
    assert summary.cost_usd is None


def test_model_router_used_on_reviewer_role():
    class FakeResult:
        text = "## What changed\nToken check added.\n\n## Why\nBrief requires it."
        model = "deepseek-v4-flash"
        costUsd = 0.0123

    class FakeRouter:
        def __init__(self):
            self.calls = []

        def call_model(self, agent_id, role, messages, opts=None):
            self.calls.append({"agent_id": agent_id, "role": role})
            return FakeResult()

    router = FakeRouter()
    gen = SummaryGenerator(model_router=router)
    summary = gen.generate(diff_text=GOLDEN_DIFF, changed_files=["src/auth.py"])

    assert len(router.calls) == 1
    assert router.calls[0]["role"].value == "reviewer"
    assert summary.model == "deepseek-v4-flash"
    assert summary.cost_usd == pytest.approx(0.0123)
    assert len(summary.sections) == 1
    assert summary.sections[0].label == "SUMMARY"
    assert "Token check added." in summary.sections[0].text


def test_risky_path_surfaces_in_what_needs_review():
    clean = SummaryGenerator().generate(diff_text=GOLDEN_DIFF, changed_files=["docs/readme.md"])
    risky = SummaryGenerator().generate(
        diff_text=GOLDEN_DIFF_RISK, changed_files=["src/auth/middleware.py"]
    )
    clean_review = next(s.text for s in clean.sections if s.label == "WHAT NEEDS REVIEW")
    risky_risky = next(s.text for s in risky.sections if s.label == "WHAT'S RISKY")
    assert "No specific risk zones" in clean_review
    assert RiskSeverity.CRITICAL.value in risky_risky
