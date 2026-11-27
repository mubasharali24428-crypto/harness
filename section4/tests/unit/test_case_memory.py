"""Task 9: case-base memory — proposals only, deterministic, similarity + decay."""

from datetime import UTC, datetime, timedelta

import pytest

from harness_orchestrator.case_memory import (
    decayed,
    fingerprint,
    record_case,
    retrieve_similar_cases,
)
from harness_orchestrator.denial_learning import SUGGESTION_MAX_CHARS

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)


def test_case_is_a_bounded_proposal_never_applied():
    paths = ["docs/a.md"] * 3 + ["x" * 900]
    c = record_case("r1", "denials_repeated", "Write", paths, "denied", NOW)
    assert c["status"] == "proposed"
    assert len(c["suggestedRule"]) <= SUGGESTION_MAX_CHARS
    assert c["paths"] == sorted({"docs/a.md", "x" * 900})


def test_fingerprint_is_order_and_duplicate_insensitive():
    assert fingerprint("d", "Write", ["b", "a", "a"]) == fingerprint("d", "Write", ["a", "b"])
    assert fingerprint("d", "Write", ["a"]) != fingerprint("d", "Bash", ["a"])


def test_half_life_decay():
    assert decayed(1.0, NOW - timedelta(hours=168), NOW) == pytest.approx(0.5)
    assert decayed(1.0, NOW, NOW) == 1.0


def test_retrieval_ranks_similar_recovered_recent_first_and_skips_unrelated():
    fixed = record_case("r1", "denials_repeated", "Write", ["src/auth.py"], "recovered", NOW)
    stale = record_case("r2", "denials_repeated", "Write", ["src/auth.py"], "recovered",
                        NOW - timedelta(days=30))
    other = record_case("r3", "denials_repeated", "Bash", ["infra/main.tf"], "denied", NOW)
    hits = retrieve_similar_cases([stale, other, fixed], ["src/auth.py"], "Write", NOW)
    assert [h["runId"] for h in hits] == ["r1", "r2"]  # unrelated r3 absent; recent beats stale
    again = retrieve_similar_cases([fixed, stale, other], ["src/auth.py"], "Write", NOW)
    assert [h["caseId"] for h in again] == [h["caseId"] for h in hits]  # input order irrelevant


def test_unknown_outcome_rejected():
    with pytest.raises(ValueError):
        record_case("r1", "x", "Write", ["a"], "auto_fixed", NOW)
