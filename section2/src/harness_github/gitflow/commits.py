"""F033: explainable commit drafting + a safe commit of exactly the named paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .pathpolicy import PathPolicyError, check_paths
from .text import clean_block, clean_line
from .worktrees import WorktreeError, run_git

_TRAILER_VALUE = re.compile(r"[^\w .@+'-]")
BOT_NAME = "Harness"
BOT_EMAIL = "harness@users.noreply.invalid"


@dataclass
class Change:
    path: str
    status: str  # A | M | D
    additions: int = 0
    deletions: int = 0


def _kind(paths: list[str]) -> tuple[str, str]:
    """(conventional type, the evidence-based reason). Heuristic, and says so."""
    lowered = [p.lower() for p in paths]
    if all(p.endswith((".md", ".rst", ".txt")) or p.startswith("docs/") for p in lowered):
        return "docs", "every changed path is documentation"
    if all("/tests/" in f"/{p}" or "test_" in p or ".test." in p or ".spec." in p for p in lowered):
        return "test", "every changed path is a test file"
    if all(p.startswith(".github/") or "ci" in p.split("/")[0] for p in lowered):
        return "ci", "every changed path is CI configuration"
    return "chore", "mixed or source changes; type not inferred from content"


def _ident(value: str, fallback: str) -> str:
    cleaned = _TRAILER_VALUE.sub("", " ".join(value.split()))[:80].strip()
    return cleaned or fallback


def draft_commit_message(
    *,
    run_id: str,
    goal: str,
    changes: list[Change],
    evidence: list[dict] | None = None,
    author_name: str | None = None,
    author_email: str | None = None,
) -> dict:
    if not changes:
        raise ValueError("nothing to commit")
    kind, basis = _kind([c.path for c in changes])
    subject_goal = clean_line(goal or "update", 60) or "update"
    subject = f"{kind}: {subject_goal}"
    lines = [subject, ""]
    if goal and len(goal.strip()) > 60:
        lines += [clean_block(goal, 600), ""]
    lines.append("Changes:")
    for c in changes[:20]:
        lines.append(f"- {c.status} {clean_line(c.path, 120)} (+{c.additions}/-{c.deletions})")
    if len(changes) > 20:
        lines.append(f"- … and {len(changes) - 20} more files")
    lines += ["", f"Basis: type '{kind}' because {basis}."]
    if evidence:
        passed = [e for e in evidence if e.get("status") == "passed"]
        lines.append(
            f"Verification: {len(passed)}/{len(evidence)} recorded checks passed"
            + ("" if len(passed) == len(evidence) else " (not all green)")
            + "."
        )
    else:
        lines.append("Verification: none recorded for this change.")
    trailers = [f"Harness-Run: {_ident(run_id, 'unknown')}"]
    if author_name and author_email and "@" in author_email:
        trailers.append(
            f"Co-authored-by: {_ident(author_name, 'unknown')} "
            f"<{_TRAILER_VALUE.sub('', author_email)[:120]}>"
        )
    message = "\n".join(lines + ["", *trailers]) + "\n"
    return {"subject": subject, "message": message, "type": kind, "basis": basis}


def commit_paths(
    worktree: Path,
    paths: list[str],
    message: str,
    *,
    author_name: str,
    author_email: str,
    forbidden: list[str] | None = None,
) -> dict:
    """Stage ONLY `paths` and commit. Never `add -A`; hooks are disabled on the command line."""
    try:
        clean = check_paths(paths, forbidden or [])
    except PathPolicyError as exc:
        raise WorktreeError("PATH_REJECTED", str(exc)) from exc
    if not clean:
        raise WorktreeError("NOTHING_TO_COMMIT", "no paths given")
    add = run_git(worktree, "add", "--", *clean)
    if add.returncode:
        raise WorktreeError("GIT_FAILED", f"git add failed: {add.stderr.strip()[-200:]}")
    staged = run_git(worktree, "diff", "--cached", "--name-only", "-z", "--", *clean).stdout
    if not staged.strip("\0"):
        raise WorktreeError("NOTHING_TO_COMMIT", "the named paths have no changes")
    env = {
        "GIT_AUTHOR_NAME": _ident(author_name, BOT_NAME),
        "GIT_AUTHOR_EMAIL": _TRAILER_VALUE.sub("", author_email) or BOT_EMAIL,
        "GIT_COMMITTER_NAME": BOT_NAME,
        "GIT_COMMITTER_EMAIL": BOT_EMAIL,
    }
    proc = run_git(worktree, "commit", "-F", "-", env=env, input_text=message)
    if proc.returncode:
        raise WorktreeError("GIT_FAILED", f"git commit failed: {proc.stderr.strip()[-200:]}")
    sha = run_git(worktree, "rev-parse", "HEAD").stdout.strip()
    return {"commitSha": sha, "files": sorted(p for p in staged.split("\0") if p)}
