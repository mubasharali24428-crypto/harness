from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


class ChangeStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"


class ChangedFile(BaseModel):
    path: str
    status: ChangeStatus
    additions: int = 0
    deletions: int = 0
    rename_from: str | None = None


class RiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskFlag(BaseModel):
    path: str
    severity: RiskSeverity
    rule: str
    reason: str


class SummarySection(BaseModel):
    label: str
    text: str


class ReviewSummary(BaseModel):
    model: str | None = None
    sections: list[SummarySection] = Field(default_factory=list)
    cost_usd: float | None = None


class ReviewFindings(BaseModel):
    """AI-generated findings for a PR review, produced via Section 3."""

    passed: bool
    summary: ReviewSummary
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    name: str
    status: str
    pass_count: int = 0
    fail_count: int = 0
    summary: str = ""


class ReviewData(BaseModel):
    """Full payload returned to the Review Area UI."""

    prId: str
    project_id: str
    github_pr_number: int
    pr_url: str
    head_branch: str
    base_branch: str
    state: str
    review_status: ReviewStatus = ReviewStatus.PENDING
    approved_by: str | None = None
    diff: str = ""
    changed_files: list[ChangedFile] = Field(default_factory=list)
    unchanged_areas_note: str | None = None
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    summary: ReviewSummary | None = None
    review_comments: list[dict[str, Any]] = Field(default_factory=list)
    test_results: list[TestResult] = Field(default_factory=list)
    model_config = {"protected_namespaces": ()}


class PreReviewDigest(BaseModel):
    """I-10: adversarial pre-review digest for the human reviewer (advisory only).

    Combines deterministic path-risk flags with a cheap adversarial model pass
    run BEFORE human review. It never approves, merges, or replaces the human
    verdict. ``model_used`` records the model (no silent downgrade); ``None``
    means deterministic-only or withheld. Secrets are redacted before any model
    context. ``requires_human_attention`` highlights elevated risk; it is not an
    approval gate.
    """

    risk_flags: list[RiskFlag] = Field(default_factory=list)
    highest_severity: RiskSeverity = RiskSeverity.LOW
    concerns: list[str] = Field(default_factory=list)
    model_used: str | None = None
    cost_usd: float | None = None
    requires_human_attention: bool = False
    basis: str = ""
