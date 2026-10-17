"""DB operations for Section 8 — reads from frozen Section 1 schema.

Only touches: pull_requests, review_comments, agent_events.
Audit writes go through Section 1's write_audit.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ReviewStatus


async def get_pull_request_record(
    db: AsyncSession, pr_id: str, team_id: str | None = None
) -> dict[str, Any] | None:
    """Fetch a PR record by internal ID. Returns None if not found.

    S1-R01d: when *team_id* is given, only PRs whose project belongs to that
    team match — a foreign PR reads as not-found (no existence oracle).
    """
    query = (
        "SELECT rp.id, rp.project_id, rp.run_id, rp.github_pr_number, rp.pr_url, "
        "rp.summary_md, rp.status, rp.approved_by, rp.created_at "
        "FROM pull_requests rp "
    )
    params: dict[str, Any] = {"id": pr_id}
    if team_id is not None:
        query += "JOIN projects p ON p.id = rp.project_id "
    query += "WHERE rp.id = :id"
    if team_id is not None:
        query += " AND p.team_id = :team"
        params["team"] = team_id
    row = (await db.execute(text(query), params)).mappings().first()
    return dict(row) if row else None


async def get_pr_by_github_number(
    db: AsyncSession, project_id: str, github_pr_number: int
) -> dict[str, Any] | None:
    """Fetch a PR record by project + GitHub PR number."""
    row = (
        await db.execute(
            text(
                "SELECT id, project_id, run_id, github_pr_number, pr_url, "
                "summary_md, status, approved_by, created_at "
                "FROM pull_requests WHERE project_id = :pid "
                "AND github_pr_number = :prn"
            ),
            {"pid": project_id, "prn": github_pr_number},
        )
    ).mappings().first()
    return dict(row) if row else None


async def get_run_pr_record(db: AsyncSession, run_id: str) -> dict[str, Any] | None:
    """M12-W2 E4a (W2-C2 consumer half): resolve a run's opened PR.

    Reads agent_runs.pr_id (migration 0014, written by Section 2's PR-open
    producer) and joins the canonical pull_requests row. Returns the exact
    W2-C2 wire triple plus the GitHub number::

        {"runId", "prId", "url", "githubPrNumber"}

    or None when the run has not opened a PR (or does not exist). camelCase
    per api-conventions so callers can pass it straight to the UI.
    """
    row = (
        await db.execute(
            text(
                "SELECT r.id AS run_id, r.pr_id AS pr_id, p.pr_url AS url, "
                "p.github_pr_number AS github_pr_number "
                "FROM agent_runs r JOIN pull_requests p ON p.id = r.pr_id "
                "WHERE r.id = :run_id"
            ),
            {"run_id": run_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return {
        "runId": row["run_id"],
        "prId": row["pr_id"],
        "url": row["url"],
        "githubPrNumber": int(row["github_pr_number"]),
    }


async def update_review_status(
    db: AsyncSession,
    pr_id: str,
    status: ReviewStatus,
    approved_by: str | None = None,
) -> None:
    """Update the PR's review status and optional approver."""
    await db.execute(
        text(
            "UPDATE pull_requests SET status = :status, approved_by = :ab "
            "WHERE id = :id"
        ),
        {"status": status.value, "ab": approved_by, "id": pr_id},
    )
    await db.commit()


async def store_summary_json(db: AsyncSession, pr_id: str, summary: dict[str, Any]) -> None:
    """Store a structured summary JSON blob in the PR's summary_md field."""
    await db.execute(
        text("UPDATE pull_requests SET summary_md = :s WHERE id = :id"),
        {"s": json.dumps(summary), "id": pr_id},
    )
    await db.commit()


async def get_stored_summary(db: AsyncSession, pr_id: str) -> dict[str, Any] | None:
    """Retrieve the summary JSON from the PR's summary_md field, if it's JSON."""
    row = (
        await db.execute(
            text("SELECT summary_md FROM pull_requests WHERE id = :id"),
            {"id": pr_id},
        )
    ).mappings().first()
    if row is None:
        return None
    raw = row["summary_md"]
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def get_diff_from_summary(db: AsyncSession, pr_id: str) -> str | None:
    """Try to extract the diff text from the PR's stored summary JSON."""
    summary = await get_stored_summary(db, pr_id)
    if summary and "diff" in summary:
        return summary["diff"]
    return None


async def get_review_comments(
    db: AsyncSession, pr_id: str
) -> list[dict[str, Any]]:
    """Return all inline review comments on a PR, oldest first."""
    rows = await db.execute(
        text(
            "SELECT id, pr_id, author_user_id, file_path, line, body, created_at "
            "FROM review_comments WHERE pr_id = :pr ORDER BY created_at ASC"
        ),
        {"pr": pr_id},
    )
    return [dict(r) for r in rows.mappings().fetchall()]


async def add_review_comment(
    db: AsyncSession,
    pr_id: str,
    author_user_id: str,
    file_path: str | None,
    line: int | None,
    body: str,
) -> dict[str, Any]:
    """Insert an inline review comment and return the created row."""
    comment_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    await db.execute(
        text(
            "INSERT INTO review_comments (id, pr_id, author_user_id, file_path, "
            "line, body, created_at) VALUES "
            "(:id, :pr, :uid, :fp, :ln, :body, :now)"
        ),
        {
            "id": comment_id,
            "pr": pr_id,
            "uid": author_user_id,
            "fp": file_path,
            "ln": line,
            "body": body,
            "now": now,
        },
    )
    await db.commit()
    return {
        "id": comment_id,
        "pr_id": pr_id,
        "author_user_id": author_user_id,
        "file_path": file_path,
        "line": line,
        "body": body,
        "created_at": now.isoformat(),
    }


async def get_agent_test_events(
    db: AsyncSession, run_id: str
) -> list[dict[str, Any]]:
    """Fetch test-related events for an agent run from agent_events."""
    rows = await db.execute(
        text(
            "SELECT type, payload FROM agent_events "
            "WHERE run_id = :rid AND type LIKE :pattern "
            "ORDER BY seq ASC"
        ),
        {"rid": run_id, "pattern": "%test%"},
    )
    return [
        {"type": r["type"], "payload": dict(r["payload"])}
        for r in rows.mappings().fetchall()
    ]
