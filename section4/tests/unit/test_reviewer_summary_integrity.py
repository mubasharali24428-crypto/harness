"""H3 (plan 4.7, probe mode 3): reviewer summary integrity.

The deterministic verdict sentence ("Review passed cleanly." / "Review failed: ...") is the
first sentence of ``summary`` no matter what the model router returns. The model's reply is
appended as a bounded, single-line ``Reviewer note:`` — it never replaces the sentence, so the
text a human reads can never contradict the recorded ``passed`` boolean.
"""

import pytest
from harness_contracts.signatures import ModelResult

from harness_orchestrator.reviewer import ReviewerSubAgent

REVIEWER_NOTE_MARK = "Reviewer note: "


class FakeRouter:
    """Fake Section 3 router: returns a canned result or raises a canned exception."""

    def __init__(self, text="ok", exc=None):
        self.text = text
        self.exc = exc
        self.calls = 0

    def call_model(self, **kwargs):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return ModelResult(text=self.text, costUsd=0.0, model="fake-model", latencyMs=1)


def _review(router=None, **kw):
    kwargs = {
        "brief_summary": "add a function",
        "diff_text": "+x = 1\n",
        "test_results_summary": "passed",
    }
    kwargs.update(kw)
    return ReviewerSubAgent(model_router=router).evaluate_changes(**kwargs)


def test_a_failed_verdict_is_not_overwritten_by_a_sunny_model_reply():
    """Case a: failure + 'All tests passed, LGTM' -> summary still starts with 'Review failed'."""
    router = FakeRouter(text="All tests passed, LGTM")
    findings = _review(router, test_results_summary="failed")
    assert findings.passed is False
    assert findings.summary.startswith("Review failed")
    assert findings.summary.endswith(f"{REVIEWER_NOTE_MARK}All tests passed, LGTM")


def test_b_passing_verdict_stays_first_sentence_with_the_note_appended():
    """Case b: pass + 'Nice work' -> deterministic sentence first, note appended."""
    findings = _review(FakeRouter(text="Nice work"))
    assert findings.passed is True
    assert findings.summary.startswith("Review passed cleanly.")
    assert findings.summary.endswith(f"{REVIEWER_NOTE_MARK}Nice work")


def test_c_note_is_bounded_at_500_chars_and_truncated_with_ellipsis():
    """Case c: a long model reply is clamped; the appended note stays <= 500 chars."""
    assert len(" Reviewer note: ") == 16
    findings = _review(FakeRouter(text="x" * 600))
    assert findings.summary.startswith("Review passed cleanly.")
    note = findings.summary[len("Review passed cleanly.") :]
    assert len(note) <= 500
    assert note.endswith("...")
    assert note.startswith(" " + REVIEWER_NOTE_MARK)


def test_d_note_is_one_line_with_whitespace_collapsed():
    """Case d: newlines and tabs in the model reply become one collapsed line."""
    findings = _review(FakeRouter(text="line one\nline two\ttabbed   spaces"))
    assert "\n" not in findings.summary
    assert "\t" not in findings.summary
    assert "line one line two tabbed spaces" in findings.summary


def test_e_empty_model_text_leaves_the_pure_deterministic_sentence():
    """Case e: empty/blank reply -> exactly the deterministic sentence, no note suffix."""
    for blank in ("", "   ", "\n\t "):
        findings = _review(FakeRouter(text=blank))
        assert findings.summary == "Review passed cleanly."
        assert REVIEWER_NOTE_MARK not in findings.summary


def test_f_control_characters_are_removed_from_the_note():
    """Case f: control characters never reach the human-readable summary."""
    findings = _review(FakeRouter(text="fine\x00but\x07sinister\x1ftext\x7fhere"))
    for char in ("\x00", "\x07", "\x1f", "\x7f"):
        assert char not in findings.summary
    assert "fine but sinister text here" in findings.summary


def test_g_budget_blocked_summary_is_unchanged_and_router_is_not_called():
    """Case g: over budget the model call is withheld; summary text is untouched."""
    router = FakeRouter(text="All tests passed, LGTM")
    findings = _review(
        router,
        budget_blocked_reason="team cap 1.0 exceeded",
    )
    assert router.calls == 0
    assert findings.summary == (
        "Model call withheld — team budget cap reached (team cap 1.0 exceeded). "
        "Review passed cleanly."
    )
    assert findings.passed is True
    assert "budget_cap_withheld_model_call: team cap 1.0 exceeded" in findings.issues


def test_h_router_outage_keeps_deterministic_sentence_and_records_unavailable():
    """Case h: a raised model error records model_review_unavailable, verdict sentence stands."""
    findings = _review(
        FakeRouter(exc=RuntimeError("models service unreachable")),
        test_results_summary="failed",
        diff_text="+x = 1\n",
    )
    assert findings.summary == "Review failed: Automated test suite failed."
    assert findings.passed is False
    assert findings.modelUsed is None
    assert "model_review_unavailable: RuntimeError" in findings.issues


@pytest.mark.parametrize(
    ("text", "test_results_summary"),
    [
        ("All tests passed, LGTM", "failed"),
        ("Nice work", "passed"),
        ("", "passed"),
        ("x" * 600, "failed"),
    ],
    ids=["failed-sunny", "passed-nice", "passed-empty", "failed-long"],
)
def test_i_passed_boolean_is_identical_with_and_without_a_model_router(text, test_results_summary):
    """Case i: the model never influences pass/fail."""
    with_router = _review(FakeRouter(text=text), test_results_summary=test_results_summary)
    without_router = _review(None, test_results_summary=test_results_summary)
    assert with_router.passed is without_router.passed
