"""Section 8: Review Area — ReviewService implementing the ReviewGate protocol.

Owns:
  - Diff parsing and changed-files extraction (delegates to diff_engine)
  - Risk-flag analysis (delegates to risk_analyzer)
  - AI summary generation via Section 3 ModelRouter (delegates to summary generator)
  - PR metadata lookup via Section 2 GitHubService (getPRStatus)
  - Inline review comments (stores in review_comments table)
  - approve / request_changes (ReviewGate protocol)
  - Audit logging via Section 1's write_audit

Never calls a GitHub merge API. Merge is always a manual human action on GitHub.
"""

import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from harness_contracts.signatures import ReviewGate
from sqlalchemy import text

from .diff_engine import extract_unchanged_context, parse_diff
from .heuristic import (
    ReviewerThresholds,
    ReviewVerdict,  # noqa: E501 (re-export via ReviewData)
    ThresholdSource,
    diff_touches_tests,
    evaluate_review_verdict,
)
from .models import (
    ReviewData,
    ReviewStatus,
    RiskFlag,
)
from .risk_analyzer import RiskAnalyzer
from .store import (
    add_review_comment,
    get_agent_test_events,
    get_diff_from_summary,
    get_pull_request_record,
    get_review_comments,
    get_stored_summary,
    store_summary_json,
    update_review_status,
)
from .summary import SummaryGenerator

# A callable that returns unified-diff text for a given GitHub PR number.
# This decouples Section 8 from Section 2's internals — the caller injects
# a provider (e.g., from Section 2's GitHubService) or a mock in tests.
DiffProvider = Callable[[str, int], str]


class NoDiffAvailableError(LookupError):
    """Raised when no diff text can be obtained for a PR."""


class ReviewService(ReviewGate):
    """Human-in-the-loop review gate for PRs.

    Parameters
    ----------
    session_factory :
        Async SQLAlchemy session factory from the shared DB (Section 1 schema).
    github_service :
        Section 2's GitHubService instance (used for getPRStatus). Optional —
        if None, PR metadata is read only from the local DB.
    model_router :
        Section 3's ModelRouter instance. Optional — if None, heuristic
        summaries are used instead of calling an LLM.
    diff_provider :
        Callable that returns diff text for (project_id, github_pr_number).
        Injected so Section 8 doesn't import Section 2 directly.
    write_audit :
        Section 1's audit logger function. Every approve/request-changes
        action is recorded here.
    """

    def __init__(
        self,
        session_factory,
        github_service=None,
        model_router=None,
        diff_provider: DiffProvider | None = None,
        write_audit: Callable[..., Any] | None = None,
        risk_analyzer: RiskAnalyzer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._github_service = github_service
        self._model_router = model_router
        self._diff_provider = diff_provider
        self._write_audit = write_audit
        self._risk_analyzer = risk_analyzer or RiskAnalyzer()
        self._summary_generator = SummaryGenerator(
            model_router=model_router, risk_analyzer=self._risk_analyzer
        )

    async def _resolve_thresholds(
        self, db, project_id: str
    ) -> tuple[ReviewerThresholds, ThresholdSource]:
        """Resolve the owning team's reviewer thresholds (M15-G6b).

        Unknown projects answer defaults — the verdict must not depend on
        whether a settings row happens to exist.
        """
        from .team_thresholds import get_team_reviewer_thresholds

        row = (
            await db.execute(
                text("SELECT team_id FROM projects WHERE id = :pid"),
                {"pid": project_id},
            )
        ).mappings().first()
        return await get_team_reviewer_thresholds(db, row["team_id"] if row else None)

    async def _session(self):
        return self._session_factory()

    async def get_review_data(
        self,
        pr_id: str,
        diff_text: str | None = None,
    ) -> ReviewData:
        """Fetch full review data for a PR.

        If *diff_text* is not supplied, attempts to retrieve it from the PR's
        stored summary JSON or via the injected *diff_provider*.
        """
        async with await self._session() as db:
            record = await get_pull_request_record(db, pr_id)
            if record is None:
                raise LookupError(f"PR record '{pr_id}' not found")

            # Resolve diff
            if diff_text is None:
                diff_text = await get_diff_from_summary(db, pr_id)
            if diff_text is None and self._diff_provider is not None:
                provided = self._diff_provider(
                    record["project_id"], record["github_pr_number"]
                )
                diff_text = await provided if inspect.isawaitable(provided) else provided
            if diff_text is None:
                raise NoDiffAvailableError(
                    f"No diff available for PR '{pr_id}'. "
                    "Pass diff_text explicitly or provide a diff_provider."
                )

            # Parse diff
            changed_files = parse_diff(diff_text)
            changed_file_paths = [f.path for f in changed_files]
            unchanged_note = extract_unchanged_context(diff_text)

            # Risk analysis
            risk_flags = self._risk_analyzer.analyze(changed_file_paths)

            # AI summary (via Section 3) — only generate if not already cached
            stored_summary = await get_stored_summary(db, pr_id)
            if stored_summary and stored_summary.get("generated"):
                from .models import ReviewSummary

                summary = ReviewSummary(**stored_summary["summary"])
            else:
                summary = self._summary_generator.generate(
                    diff_text=diff_text,
                    changed_files=changed_file_paths,
                    brief_goal=stored_summary.get("brief_goal", "") if stored_summary else "",
                    test_summary="",
                )
                store_payload = {
                    "generated": True,
                    "diff": diff_text,
                    "summary": summary.model_dump(),
                    "generated_at": datetime.now(UTC).isoformat(),
                }
                await store_summary_json(db, pr_id, store_payload)

            # Test results (from agent_events if available)
            test_results = []
            run_id = record.get("run_id")
            if run_id:
                from .models import TestResult

                test_events = await get_agent_test_events(db, run_id)
                for ev in test_events:
                    payload = ev.get("payload", {})
                    test_results.append(
                        TestResult(
                            name=payload.get("name", ev["type"]),
                            status=payload.get("status", "unknown"),
                            pass_count=payload.get("passed", 0),
                            fail_count=payload.get("failed", 0),
                            summary=payload.get("summary", ""),
                        )
                    )

            # Review comments
            comments = await get_review_comments(db, pr_id)

            # PR metadata from Section 2 (if available)
            pr_meta: dict[str, Any] = {}
            if self._github_service is not None:
                try:
                    pr_meta = await self._github_service.getPRStatus(
                        _system_user_id, record["project_id"], record["github_pr_number"]
                    )
                except (PermissionError, RuntimeError):
                    # User may not have GitHub OAuth connected; fall back to DB record
                    pr_meta = {}

            # Unchanged-areas note
            unchanged_areas_note = (
                unchanged_note
                if unchanged_note
                else "No unchanged context available (diff may be additions-only)."
            )

            return ReviewData(
                prId=pr_id,
                project_id=record["project_id"],
                github_pr_number=record["github_pr_number"],
                pr_url=record["pr_url"],
                head_branch=pr_meta.get("headBranch", ""),
                base_branch=pr_meta.get("baseBranch", ""),
                state=pr_meta.get("state", record["status"]),
                review_status=ReviewStatus(record["status"])
                if record["status"] in ReviewStatus.__members__.values()
                else ReviewStatus.PENDING,
                approved_by=record.get("approved_by"),
                diff=diff_text,
                changed_files=changed_files,
                unchanged_areas_note=unchanged_areas_note,
                risk_flags=risk_flags,
                summary=summary,
                review_comments=comments,
                test_results=test_results,
            )

    async def approve(self, pr_id: str, reviewer_user_id: str) -> None:
        """Approve a PR for merge (human action — merge itself is manual on GitHub)."""
        async with await self._session() as db:
            record = await get_pull_request_record(db, pr_id)
            if record is None:
                raise LookupError(f"PR record '{pr_id}' not found")

            await update_review_status(db, pr_id, ReviewStatus.APPROVED, reviewer_user_id)

            if self._write_audit:
                await self._write_audit(
                    db,
                    "review.approved",
                    actor_member_id=None,
                    target_type="pull_request",
                    target_id=pr_id,
                    detail={
                        "reviewer_user_id": reviewer_user_id,
                        "github_pr_number": record["github_pr_number"],
                    },
                )

    async def request_changes(
        self,
        pr_id: str,
        comments: list[dict],
        reviewer_user_id: str,
    ) -> None:
        """Request changes on a PR, recording each comment as a review_comments row."""
        async with await self._session() as db:
            record = await get_pull_request_record(db, pr_id)
            if record is None:
                raise LookupError(f"PR record '{pr_id}' not found")

            for comment in comments:
                await add_review_comment(
                    db,
                    pr_id=pr_id,
                    author_user_id=reviewer_user_id,
                    file_path=comment.get("file_path"),
                    line=comment.get("line"),
                    body=comment.get("body", ""),
                )

            await update_review_status(db, pr_id, ReviewStatus.CHANGES_REQUESTED)

            if self._write_audit:
                await self._write_audit(
                    db,
                    "review.changes_requested",
                    actor_member_id=None,
                    target_type="pull_request",
                    target_id=pr_id,
                    detail={
                        "reviewer_user_id": reviewer_user_id,
                        "comment_count": len(comments),
                        "github_pr_number": record["github_pr_number"],
                    },
                )

    async def add_inline_comment(
        self,
        pr_id: str,
        file_path: str | None,
        line: int | None,
        body: str,
        author_user_id: str,
    ) -> dict[str, Any]:
        """Add an inline review comment to a PR."""
        async with await self._session() as db:
            record = await get_pull_request_record(db, pr_id)
            if record is None:
                raise LookupError(f"PR record '{pr_id}' not found")

            comment = await add_review_comment(
                db, pr_id, author_user_id, file_path, line, body
            )
            if self._write_audit:
                await self._write_audit(
                    db,
                    "review.comment_added",
                    actor_member_id=None,
                    target_type="review_comment",
                    target_id=comment["id"],
                    detail={
                        "file_path": file_path,
                        "line": line,
                        "pr_id": pr_id,
                    },
                )
            return comment

    async def get_comments(self, pr_id: str) -> list[dict[str, Any]]:
        """Return all inline review comments for a PR."""
        async with await self._session() as db:
            return await get_review_comments(db, pr_id)

    async def get_risk_flags(self, pr_id: str, diff_text: str | None = None) -> list[RiskFlag]:
        """Re-run risk analysis on a PR's diff and return flags."""
        review_data = await self.get_review_data(pr_id, diff_text=diff_text)
        return review_data.risk_flags

    async def get_summary(
        self, pr_id: str, diff_text: str | None = None
    ) -> ReviewData:
        """Re-generate and return the AI summary for a PR."""
        return await self.get_review_data(pr_id, diff_text=diff_text)

    async def evaluate_verdict(
        self, pr_id: str, diff_text: str | None = None
    ) -> ReviewVerdict:
        """Pass/fail verdict under the PR's team thresholds (M15-G6b).

        The reviewer's decision-time consumer of team tuning: resolves the
        owning team's stored document from Section 1's team_settings and runs
        the documented heuristic. Missing config → defaults; malformed config
        → defaults (never an unusable gate). Pure decision logic lives in
        heuristic.evaluate_review_verdict so it stays calibration-testable.
        """
        async with await self._session() as db:
            record = await get_pull_request_record(db, pr_id)
            if record is None:
                raise LookupError(f"PR record '{pr_id}' not found")

            # Resolve diff (same chain as get_review_data).
            if diff_text is None:
                diff_text = await get_diff_from_summary(db, pr_id)
            if diff_text is None and self._diff_provider is not None:
                provided = self._diff_provider(
                    record["project_id"], record["github_pr_number"]
                )
                diff_text = await provided if inspect.isawaitable(provided) else provided
            if diff_text is None:
                raise NoDiffAvailableError(
                    f"No diff available for PR '{pr_id}'. "
                    "Pass diff_text explicitly or provide a diff_provider."
                )

            changed_files = parse_diff(diff_text)
            changed_file_paths = [f.path for f in changed_files]
            risk_flags = self._risk_analyzer.analyze(changed_file_paths)

            run_id = record.get("run_id")
            has_passing_tests = False
            if run_id:
                test_events = await get_agent_test_events(db, run_id)
                has_passing_tests = any(
                    ev.get("payload", {}).get("passed", 0) > 0 for ev in test_events
                )

            thresholds, thresholds_source = await self._resolve_thresholds(
                db, record["project_id"]
            )
            return evaluate_review_verdict(
                thresholds,
                risk_flags=risk_flags,
                changed_file_count=len(changed_files),
                added_lines=sum(f.additions for f in changed_files),
                deleted_lines=sum(f.deletions for f in changed_files),
                touches_test_files=diff_touches_tests(changed_file_paths),
                has_passing_tests=has_passing_tests,
                thresholds_source=thresholds_source,
            )


# System user ID used when Section 8 needs to look up PR data via Section 2
# but no individual user's OAuth token is in scope. In production this would
# be replaced by the calling user's session principal.
_system_user_id = "system-review"
