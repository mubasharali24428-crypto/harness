"""F034/F035: evidence-based PR description + idempotent governed draft PR (never merge)."""

from __future__ import annotations

from typing import Any, Protocol

from .text import clean_block, clean_line, neutralize


def generate_pr_description(
    *,
    run_id: str,
    goal: str,
    changes: list[dict],
    evidence: list[dict] | None,
    commit_shas: list[str] | None = None,
    reviewers: dict | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Describes only what the supplied evidence shows; absent evidence is said, not implied."""
    title = neutralize(clean_line(goal or f"Run {run_id}", 72))
    lines = ["## Summary", "", neutralize(clean_block(goal or "(no goal recorded)", 1200)), ""]
    lines.append("## Changes")
    if not changes:
        lines.append("_No file changes were reported._")
    for c in changes[:50]:
        lines.append(
            f"- `{clean_line(str(c.get('path', '?')), 160)}` "
            f"({c.get('status', 'M')}, +{int(c.get('additions', 0))}/-{int(c.get('deletions', 0))})"
        )
    if len(changes) > 50:
        lines.append(f"- … and {len(changes) - 50} more files")
    lines += ["", "## Verification evidence"]
    if not evidence:
        lines.append(
            "**No verification evidence was recorded for this change.** "
            "Do not assume it was tested."
        )
    else:
        for e in evidence[:20]:
            status = str(e.get("status", "unknown"))
            mark = {"passed": "PASS", "failed": "FAIL"}.get(status, status.upper())
            lines.append(
                f"- `{clean_line(str(e.get('command', '?')), 120)}` — **{mark}**"
                + (f": {clean_line(str(e['summary']), 160)}" if e.get("summary") else "")
            )
        if any(e.get("status") != "passed" for e in evidence):
            lines.append("\n> Not every recorded check passed; review before approving.")
    if reviewers and (reviewers.get("reviewers") or reviewers.get("teamReviewers")):
        owners = [f"@{u}" for u in reviewers.get("reviewers", [])] + [
            f"@{t}" for t in reviewers.get("teamReviewers", [])
        ]
        lines += ["", "## Suggested reviewers (CODEOWNERS)", ", ".join(f"`{o}`" for o in owners)]
    if reviewers and reviewers.get("unowned"):
        lines += ["", f"_{len(reviewers['unowned'])} changed path(s) have no CODEOWNERS owner._"]
    for note in notes or []:
        lines.append(f"\n> {neutralize(clean_line(note, 300))}")
    lines += [
        "",
        "---",
        f"Opened as a **draft** by the Harness for run `{clean_line(run_id, 64)}`. "
        "Merging is a manual human action in your Git provider.",
    ]
    if commit_shas:
        lines.append("Commits: " + ", ".join(f"`{s[:12]}`" for s in commit_shas[:20]))
    return {"title": title, "body": "\n".join(lines) + "\n"}


class NonDraftError(RuntimeError):
    """The provider created a non-draft PR; it exists and needs manual conversion."""

    def __init__(self, number: int, url: str):
        super().__init__(f"provider returned a non-draft PR #{number}")
        self.number = number
        self.url = url


class PRProvider(Protocol):
    async def find_open_pr(self, repo: str, branch: str) -> dict | None: ...
    async def open_draft_pr(
        self, repo: str, branch: str, base: str, title: str, body_md: str
    ) -> dict: ...
    async def request_reviewers(
        self, repo: str, number: int, reviewers: list[str], team_reviewers: list[str]
    ) -> dict: ...


async def ensure_draft_pr(
    provider: PRProvider, repo: str, branch: str, base: str, title: str, body_md: str
) -> dict[str, Any]:
    """Idempotent: an open PR for the head branch is reused, never duplicated."""
    existing = await provider.find_open_pr(repo, branch)
    if existing is not None:
        return {**existing, "reused": True}
    try:
        pr = await provider.open_draft_pr(repo, branch, base, title, body_md)
    except Exception:
        # lost a race with another caller: the PR now exists
        existing = await provider.find_open_pr(repo, branch)
        if existing is not None:
            return {**existing, "reused": True}
        raise
    if not pr.get("draft"):
        raise NonDraftError(int(pr.get("number", 0)), str(pr.get("url", "")))
    return {**pr, "reused": False}
