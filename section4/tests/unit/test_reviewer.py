from fixtures.golden_fixtures import GOLDEN_DIFF_CLEAN, GOLDEN_DIFF_CONFLICT

from harness_orchestrator.reviewer import ReviewerSubAgent


def test_reviewer_passes_clean_diff():
    reviewer = ReviewerSubAgent()
    findings = reviewer.evaluate_changes(
        brief_summary="Implement auth token verification",
        diff_text=GOLDEN_DIFF_CLEAN,
        test_results_summary="passed",
    )
    assert findings.passed is True
    assert findings.conflicts_detected is False
    assert findings.syntax_ok is True


def test_reviewer_fails_conflict_diff():
    reviewer = ReviewerSubAgent()
    findings = reviewer.evaluate_changes(
        brief_summary="Implement auth token verification",
        diff_text=GOLDEN_DIFF_CONFLICT,
        test_results_summary="passed",
    )
    assert findings.passed is False
    assert findings.conflicts_detected is True
    assert len(findings.issues) >= 1
