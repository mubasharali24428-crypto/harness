"""Section 8: Review Area — human-in-the-loop review and merge gate.

The only thing humans approve before code reaches production. Section 8 shows
the diff, flags high-risk zones, generates an AI-written PR summary via Section 3,
and records every approve/request-changes action through Section 1's audit log.
The app itself never merges — that is always a manual GitHub action.
"""

from .models import (
    ChangedFile,
    PreReviewDigest,
    ReviewData,
    ReviewFindings,
    ReviewStatus,
    RiskFlag,
    RiskSeverity,
)
from .pre_review import build_pre_review_digest
from .service import ReviewService

__all__ = [
    "ChangedFile",
    "PreReviewDigest",
    "ReviewData",
    "ReviewFindings",
    "ReviewStatus",
    "RiskFlag",
    "RiskSeverity",
    "ReviewService",
    "build_pre_review_digest",
]
