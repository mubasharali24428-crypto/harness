"""M15-G6b — reviewer-heuristic calibration corpus + threshold semantics.

Regression pin: every case below asserts the CURRENT verdict under DEFAULT
thresholds. If someone retunes the code or the defaults and a verdict flips,
these tests fail loudly — tuning must be an explicit, reviewed act, never a
silent behavior change. Two corpora:

Corpus A — end-to-end diffs (known-good / known-bad unified diffs) run
through the real parser + RiskAnalyzer + heuristic, exactly like production.

Corpus B — synthetic risk-flag sets that pin each threshold's EXACT boundary
(count == cap passes, count == cap + 1 fails) so the documented comparison
semantics ("fails only when strictly exceeding") can't drift either.
"""


from pathlib import Path

import pytest

from harness_review.diff_engine import parse_diff
from harness_review.heuristic import (
    DEFAULT_REVIEWER_THRESHOLDS,
    ReviewerThresholds,
    ReviewVerdict,
    diff_touches_tests,
    evaluate_review_verdict,
)
from harness_review.models import RiskFlag, RiskSeverity
from harness_review.risk_analyzer import RiskAnalyzer


def _flag(sev: RiskSeverity, path: str = "x") -> RiskFlag:
    return RiskFlag(path=path, severity=sev, rule="synthetic", reason="corpus")


def _verdict(thresholds, *, flags=(), files=1, add=1, dele=0, tests=False, passing=False):
    return evaluate_review_verdict(
        thresholds,
        risk_flags=list(flags),
        changed_file_count=files,
        added_lines=add,
        deleted_lines=dele,
        touches_test_files=tests,
        has_passing_tests=passing,
    )


# ---------------------------------------------------------------- Corpus A
# Known-good diffs: MUST stay passed=True under default thresholds.

GOOD_DIFFS = {
    "trivial_comment_tweak": """\
diff --git a/src/calc.py b/src/calc.py
--- a/src/calc.py
+++ b/src/calc.py
@@ -1,4 +1,5 @@
 def add(a, b):
+    # optimized for readability
     return a + b
""",
    "small_clean_feature": """\
diff --git a/src/utils/format.py b/src/utils/format.py
new file mode 100644
--- /dev/null
+++ b/src/utils/format.py
@@ -0,0 +1,3 @@
+def slugify(text):
+    return text.lower().replace(" ", "-")
diff --git a/src/utils/__init__.py b/src/utils/__init__.py
--- a/src/utils/__init__.py
+++ b/src/utils/__init__.py
@@ -1,2 +1,3 @@
+# export formatting helpers
""",
    "docs_only": """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,3 +1,6 @@
 # Demo
+
+## Setup
+Run make bootstrap.
""",
}

# Known-bad diffs: MUST stay passed=False under default thresholds. Each entry
# pins WHICH rule trips so a retune can't swap one failure mode for another.

BAD_DIFFS = {
    "auth_file_touch": (
        """\
diff --git a/src/auth/session.py b/src/auth/session.py
--- a/src/auth/session.py
+++ b/src/auth/session.py
@@ -1,3 +1,5 @@
-def verify(token):
-    return True
+def verify(token):
+    return token != ""
""",
        ["critical"],
    ),
    "env_file_modified": (
        """\
diff --git a/.env.production b/.env.production
--- a/.env.production
+++ b/.env.production
@@ -1,2 +1,3 @@
-DATABASE_URL=old
+DATABASE_URL=new
+DEBUG=true
""",
        ["critical"],
    ),
    "migration_plus_token": (
        """\
diff --git a/migrations/0009_add_index.sql b/migrations/0009_add_index.sql
new file mode 100644
--- /dev/null
+++ b/migrations/0009_add_index.sql
@@ -0,0 +1,2 @@
+CREATE INDEX idx_a ON users (email);
diff --git a/src/auth/token_store.py b/src/auth/token_store.py
--- a/src/auth/token_store.py
+++ b/src/auth/token_store.py
@@ -1,2 +1,4 @@
+def store(token):
+    pass
""",
        ["high"],
    ),
}


def _run_diff(diff_text: str) -> tuple[ReviewVerdict, int]:
    files = parse_diff(diff_text)
    paths = [f.path for f in files]
    flags = RiskAnalyzer().analyze(paths)
    verdict = _verdict(
        DEFAULT_REVIEWER_THRESHOLDS,
        flags=flags,
        files=len(files),
        add=sum(f.additions for f in files),
        dele=sum(f.deletions for f in files),
        tests=diff_touches_tests(paths),
    )
    return verdict, len(flags)


@pytest.mark.parametrize("name", sorted(GOOD_DIFFS))
def test_corpus_known_good_passes_at_defaults(name):
    verdict, n_flags = _run_diff(GOOD_DIFFS[name])
    assert verdict.passed, f"{name} regressed: {verdict.reasons}"
    assert verdict.thresholds_source == "defaults"


@pytest.mark.parametrize("name", sorted(BAD_DIFFS))
def test_corpus_known_bad_fails_at_defaults(name):
    diff_text, expected_band_trips = BAD_DIFFS[name]
    verdict, _ = _run_diff(diff_text)
    assert not verdict.passed, f"{name} regressed to PASS under defaults"
    joined = " ".join(verdict.reasons)
    for band in expected_band_trips:
        assert f"{band} risk flags" in joined, (
            f"{name} should trip the {band} band; got: {verdict.reasons}"
        )


def test_corpus_golden_risk_diff_fails_on_critical():
    """The existing golden risky diff (auth/middleware.py) must keep failing."""
    fixture_text = Path("tests/fixtures/golden_fixtures.py").read_text()
    golden_risk_diff = fixture_text.split('GOLDEN_DIFF_RISK = """')[1].split('"""')[0]
    verdict, _ = _run_diff(golden_risk_diff)
    assert not verdict.passed
    assert any("critical" in r for r in verdict.reasons)


# ---------------------------------------------------------------- Corpus B
# Exact boundary semantics per threshold (strictly-exceeding comparison).


def test_boundary_medium_cap_three():
    t = ReviewerThresholds()
    three = [_flag(RiskSeverity.MEDIUM) for _ in range(3)]
    assert _verdict(t, flags=three).passed, "at-cap must pass"
    four = [*three, _flag(RiskSeverity.MEDIUM)]
    v = _verdict(t, flags=four)
    assert not v.passed
    assert any("medium" in r for r in v.reasons)


def test_boundary_total_cap_ten_ignores_bands():
    """10 LOW flags pass every band cap yet hit the total cap at 11."""
    t = ReviewerThresholds()
    ten = [_flag(RiskSeverity.LOW) for _ in range(10)]
    assert _verdict(t, flags=ten).passed
    eleven = [*ten, _flag(RiskSeverity.LOW)]
    v = _verdict(t, flags=eleven)
    assert not v.passed
    assert any("total" in r for r in v.reasons)


def test_boundary_changed_files_cap_fifty():
    t = ReviewerThresholds()
    assert _verdict(t, files=50).passed
    v = _verdict(t, files=51)
    assert not v.passed
    assert any("changed files" in r for r in v.reasons)


def test_boundary_diff_lines_cap_two_thousand():
    t = ReviewerThresholds()
    assert _verdict(t, add=1200, dele=800).passed  # exactly 2000
    v = _verdict(t, add=1201, dele=800)
    assert not v.passed
    assert any("diff size" in r for r in v.reasons)


def test_zero_caps_fail_on_first_hit():
    t = ReviewerThresholds(max_critical_risk_flags=0)
    v = _verdict(t, flags=[_flag(RiskSeverity.CRITICAL)])
    assert not v.passed
    # zero means zero: even one hit is over


def test_all_reasons_accumulate_not_short_circuit():
    """A diff tripping several caps reports EVERY reason (auditable why)."""
    t = ReviewerThresholds()
    flags = [_flag(RiskSeverity.CRITICAL), _flag(RiskSeverity.HIGH)]
    v = _verdict(t, flags=flags, files=99, add=5000)
    assert not v.passed
    joined = " ".join(v.reasons)
    for needle in ("critical", "high", "changed files", "diff size"):
        assert needle in joined


# ------------------------------------------------- test-evidence opt-in rule


def test_require_test_evidence_off_by_default():
    t = ReviewerThresholds()
    assert _verdict(t, tests=False, passing=False).passed


def test_require_test_evidence_blocks_when_no_tests():
    t = ReviewerThresholds(require_test_evidence=True)
    v = _verdict(t, tests=False, passing=False)
    assert not v.passed
    assert any("test evidence" in r for r in v.reasons)


@pytest.mark.parametrize(("touches", "passing"), [(True, False), (False, True), (True, True)])
def test_require_test_evidence_escapes(touches, passing):
    t = ReviewerThresholds(require_test_evidence=True)
    assert _verdict(t, tests=touches, passing=passing).passed


def test_diff_touches_tests_detection():
    assert diff_touches_tests(["src/pkg/test_utils.py"])
    assert diff_touches_tests(["tests/unit/test_x.py"])
    assert diff_touches_tests(["src/thing_test.ts"])
    assert diff_touches_tests(["src/conftest.py"])
    assert diff_touches_tests(["web/app/user.spec.js"]) or diff_touches_tests(
        ["web/app/user.spec.jsx"]
    ) or diff_touches_tests(["web/app/user.test.js"])
    assert not diff_touches_tests(["src/utils/format.py"])
    assert not diff_touches_tests([])


# ------------------------------------------------------- tuning does flip


def test_raised_thresholds_rescue_a_failing_diff():
    """The whole point of tuning: same bad-for-defaults diff, looser team.

    Note the fixture diff trips THREE bands at defaults (auth file =>
    CRITICAL via *auth*.py; token store => HIGH; migration => HIGH), so the
    rescuing document must loosen every tripped cap — exactly what an admin
    deliberately widening their gate would write.
    """
    verdict_default, _ = _run_diff(BAD_DIFFS["migration_plus_token"][0])
    assert not verdict_default.passed

    files = parse_diff(BAD_DIFFS["migration_plus_token"][0])
    paths = [f.path for f in files]
    flags = RiskAnalyzer().analyze(paths)
    loose = ReviewerThresholds(
        max_critical_risk_flags=5,
        max_high_risk_flags=3,
    )
    rescued = evaluate_review_verdict(
        loose,
        risk_flags=flags,
        changed_file_count=len(files),
        added_lines=sum(f.additions for f in files),
        deleted_lines=sum(f.deletions for f in files),
        touches_test_files=diff_touches_tests(paths),
        has_passing_tests=False,
    )
    assert rescued.passed, f"loosened caps should pass this diff: {rescued.reasons}"
