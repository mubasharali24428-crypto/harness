"""M15-G6b — reviewer pass/fail heuristic + per-team thresholds.

THE HEURISTIC (documented once, here):
The reviewer turns a parsed diff into a pass / request-changes verdict from
deterministic signals. It is intentionally NOT a learned model (same stance
as risk_analyzer.py): every rule is auditable and every threshold is a named
number a team admin can tune. Signals, evaluated independently:

1. Risk-flag bands (flags come from risk_analyzer's path rules):
   - CRITICAL flags (auth/payment/.env/secrets/private keys) exceeding
     ``max_critical_risk_flags`` (default 0) fail the PR.
   - HIGH flags (migrations, token-bearing files) exceeding
     ``max_high_risk_flags`` (default 0) fail the PR.
   - MEDIUM flags (route/api churn) exceeding ``max_medium_risk_flags``
     (default 3) fail the PR — some churn is normal, piles of it are not.
   - ALL flags combined exceeding ``max_total_risk_flags`` (default 10) fail
     the PR even when no single band trips: accumulated noise is its own
     risk.
2. Blast radius:
   - More than ``max_changed_files`` (default 50) files fail: too broad to
     review meaningfully.
   - More than ``max_diff_lines`` (default 2000) added+deleted lines fail:
     generated/mechanical dumps need a human-split, not a rubber stamp.
3. Test evidence (opt-in):
   - ``require_test_evidence=True`` plus a diff that neither touches a
     test-looking path nor has a recorded passing test result fails.
   - Default False: current behavior never demands test evidence.

Comparison semantics: a band fails only when its count STRICTLY EXCEEDS the
threshold ("exceeds the allowed N"). A diff at exactly the cap passes, so a
cap of 0 means "any single hit fails".

PER-TEAM TUNING: teams override these numbers via Section 1 team settings
(``PUT /api/v1/teams/{teamId}/settings/reviewer-thresholds``, admin-gated).
At read time a missing OR malformed stored config answers
DEFAULT_REVIEWER_THRESHOLDS — tuning can loosen the gate but can never leave
the reviewer without a usable threshold set. The admin PUT validates the
document and answers 422 VALIDATION_ERROR on garbage, so a corrupt document
is never persisted in the first place.
"""

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import RiskFlag, RiskSeverity

ThresholdSource = Literal["team", "defaults"]

_SEVERITY_FIELD = {
    RiskSeverity.CRITICAL: "critical",
    RiskSeverity.HIGH: "high",
    RiskSeverity.MEDIUM: "medium",
    RiskSeverity.LOW: "low",
}


class ReviewerThresholds(BaseModel):
    """Tunable knobs of the reviewer heuristic — one document per team.

    Wire/storage form is camelCase (alias generator below), so the exact JSON
    a team PUTs is the exact JSON Section 8 re-reads at decision time.
    Unknown keys are ignored (forward compatible); values are validated
    (non-negative ints, positive size caps) — violations are a 422 on PUT and
    a silent fallback to these defaults at read.
    """

    model_config = ConfigDict(
        alias_generator=lambda name: "".join(
            part if i == 0 else part.capitalize()
            for i, part in enumerate(name.split("_"))
        ),
        populate_by_name=True,
    )

    # Max CRITICAL-severity risk flags tolerated before the verdict flips to
    # request-changes. 0 = any auth/payment/secret/key touch fails the PR.
    # strict=True: booleans/strings must never masquerade as counts (JSON
    # true == Python 1 in lax mode — rejected here by design).
    max_critical_risk_flags: int = Field(default=0, ge=0, strict=True)

    # Max HIGH-severity flags tolerated (migrations, token files). 0 = any
    # single one fails; raise only when your flow expects routine migrations.
    max_high_risk_flags: int = Field(default=0, ge=0, strict=True)

    # Max MEDIUM-severity flags tolerated (routes/api churn). Default 3 lets
    # ordinary feature work pass while flagging wide API-surface sweeps.
    max_medium_risk_flags: int = Field(default=3, ge=0, strict=True)

    # Cap on ALL flags regardless of severity — catches slow noise creep on
    # diffs that never trip a single band.
    max_total_risk_flags: int = Field(default=10, ge=0, strict=True)

    # Blast-radius cap: changed files per PR.
    max_changed_files: int = Field(default=50, ge=1, strict=True)

    # Blast-radius cap: added + deleted lines per PR.
    max_diff_lines: int = Field(default=2000, ge=1, strict=True)

    # When True, a PR with neither a test-looking path among its changes nor
    # a recorded passing test result fails. Default False (legacy behavior).
    require_test_evidence: bool = Field(default=False, strict=True)


DEFAULT_REVIEWER_THRESHOLDS = ReviewerThresholds()

# Storage key of the single thresholds document in team_settings (migration
# 0024). One row, value = JSON object in the model's camelCase wire form.
REVIEWER_THRESHOLD_KEY = "reviewer_thresholds"

# Path fragments that count as "test evidence touched by this diff".
TEST_PATH_MARKERS = (
    "test_",       # test_*.py anywhere in the path/basename
    "_test.",      # *_test.py / *_test.ts style
    "/tests/",     # a tests directory component
    "conftest",    # pytest fixtures
    "spec.",       # *.spec.js style
)


def diff_touches_tests(changed_paths: Iterable[str]) -> bool:
    """True when any changed path looks like test code."""
    for path in changed_paths:
        lowered = path.lower()
        basename = lowered.rsplit("/", 1)[-1]
        if any(m in lowered for m in TEST_PATH_MARKERS) or basename.startswith("test"):
            return True
    return False


class RiskBandCounts(BaseModel):
    """Per-severity flag counts feeding the verdict."""

    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0


class ReviewVerdict(BaseModel):
    """Outcome of one reviewer-heuristic evaluation.

    ``reasons`` lists every tripped threshold in human form — empty exactly
    when ``passed`` is True, so the UI can show WHY a PR was failed without
    re-running the math.
    """

    passed: bool
    reasons: list[str] = Field(default_factory=list)
    counts: RiskBandCounts
    changed_files: int
    added_lines: int
    deleted_lines: int
    thresholds_source: Literal["team", "defaults"]
    thresholds_applied: ReviewerThresholds


def count_risk_bands(risk_flags: Iterable[RiskFlag]) -> RiskBandCounts:
    """Tally risk flags per severity band."""
    counts = RiskBandCounts()
    for flag in risk_flags:
        field = _SEVERITY_FIELD[flag.severity]
        setattr(counts, field, getattr(counts, field) + 1)
        counts.total += 1
    return counts


def evaluate_review_verdict(
    thresholds: ReviewerThresholds,
    *,
    risk_flags: Iterable[RiskFlag],
    changed_file_count: int,
    added_lines: int,
    deleted_lines: int,
    touches_test_files: bool,
    has_passing_tests: bool,
    thresholds_source: Literal["team", "defaults"] = "defaults",
) -> ReviewVerdict:
    """Apply the documented heuristic. Pure function — no I/O, fully
    calibratable (see tests/calibration/)."""
    counts = count_risk_bands(risk_flags)
    reasons: list[str] = []

    if counts.critical > thresholds.max_critical_risk_flags:
        reasons.append(
            f"critical risk flags ({counts.critical}) exceed the allowed "
            f"{thresholds.max_critical_risk_flags}"
        )
    if counts.high > thresholds.max_high_risk_flags:
        reasons.append(
            f"high risk flags ({counts.high}) exceed the allowed "
            f"{thresholds.max_high_risk_flags}"
        )
    if counts.medium > thresholds.max_medium_risk_flags:
        reasons.append(
            f"medium risk flags ({counts.medium}) exceed the allowed "
            f"{thresholds.max_medium_risk_flags}"
        )
    if counts.total > thresholds.max_total_risk_flags:
        reasons.append(
            f"total risk flags ({counts.total}) exceed the allowed "
            f"{thresholds.max_total_risk_flags}"
        )
    if changed_file_count > thresholds.max_changed_files:
        reasons.append(
            f"changed files ({changed_file_count}) exceed the allowed "
            f"{thresholds.max_changed_files}"
        )
    diff_lines = added_lines + deleted_lines
    if diff_lines > thresholds.max_diff_lines:
        reasons.append(
            f"diff size ({diff_lines} lines) exceeds the allowed "
            f"{thresholds.max_diff_lines}"
        )
    if thresholds.require_test_evidence and not (touches_test_files or has_passing_tests):
        reasons.append(
            "no test evidence: diff touches no test files and no passing test "
            "result is recorded"
        )

    return ReviewVerdict(
        passed=not reasons,
        reasons=reasons,
        counts=counts,
        changed_files=changed_file_count,
        added_lines=added_lines,
        deleted_lines=deleted_lines,
        thresholds_source=thresholds_source,
        thresholds_applied=thresholds,
    )
