"""Reviewer-threshold document contract — the one shared validation shape.

M17-B3 promotion: this model lived at ``harness_review.heuristic`` and was
reached by Section 1's settings API through an s1→s8 package-name import —
a REVERSE isolation edge (identity importing review-domain config) that the
CI guard could not see. The pure document contract now lives in the frozen
contracts package; Section 8 keeps the evaluation logic (risk bands,
verdicts, calibration) and imports this module for the model itself, so
writer (S1 settings PUT validation) and reader (S8 decision time) can never
drift apart.

Wire/storage form is camelCase (alias generator below), so the exact JSON a
team PUTs is the exact JSON Section 8 re-reads at decision time. Unknown
keys are ignored on READ (forward compatible); values are validated
(non-negative ints, positive size caps) — violations are a 422 on PUT and a
silent fallback to these defaults at read.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ReviewerThresholds(BaseModel):
    """Tunable knobs of the reviewer heuristic — one document per team."""

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

__all__ = [
    "DEFAULT_REVIEWER_THRESHOLDS",
    "REVIEWER_THRESHOLD_KEY",
    "ReviewerThresholds",
]
