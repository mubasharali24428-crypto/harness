"""Git-history-aware retrieval (F012).

Answers "what changed in this repo, and what do the commits say?" for a
grounding workflow that currently only sees the working tree. This is
deliberately a bounded, read-only ``git log`` retrieval — no index, no
persistence, nothing to invalidate:

* ``search_history(root, query, path=...)`` — commits whose message matches
  any token of ``query`` (case-insensitive, FIXED-STRING — never regex, so a
  query can never smuggle pattern syntax), optionally restricted to commits
  touching ``path``. Blank query + no path ⇒ the most recent commits.
* Rename/delete/add are surfaced from ``--name-status`` records, so history
  retrieval can explain a missing file (``D``) or follow a move (``R``).

Safety properties (mirrored in tests):

* argv-array subprocess only (no shell): user text reaches git as a single
  ``--grep=<token>`` argument or a pathspec after ``--``.
* ``path`` is validated repo-RELATIVE: absolute paths and ``..`` segments are
  rejected before git runs.
* hard ``timeout`` (default 10s) with process kill; ``limit`` capped by the
  caller-facing schema (API enforces ≤ 50).
* a non-git directory or empty-history repo returns ``([], reason)`` — a
  typed empty answer, never an exception the surface would 500 on.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILES_PER_COMMIT = 50
DEFAULT_LIMIT = 20
DEFAULT_TIMEOUT_SECONDS = 10.0

# git name-status letters; rename/copy carry a score suffix (R100, C75).
_RENAME_STATUSES = frozenset("RC")


@dataclass(frozen=True)
class CommitFile:
    """One name-status entry of a commit."""

    status: str  # single letter: A/M/D/R/C/T/U/X
    path: str  # new path for renames/copies


@dataclass(frozen=True)
class HistoryCommit:
    """One retrieved commit."""

    sha: str
    subject: str
    author: str
    date_iso: str
    files: list[CommitFile] = field(default_factory=list)
    files_truncated: int = 0  # entries dropped beyond MAX_FILES_PER_COMMIT


class InvalidRepoPathError(ValueError):
    """Caller supplied a path that must never reach git (absolute/..)."""


def validate_relative_path(path: str) -> str:
    """Return a normalized repo-relative path or raise.

    ``git log -- <path>`` pathspecs can themselves start with ``:`` and carry
    magic; requiring a plain relative path keeps user text from reaching git
    as anything but a literal trailing pathspec.
    """
    if not path or path.startswith("/") or path.startswith(":") or path.startswith("-"):
        raise InvalidRepoPathError(f"path must be repo-relative, non-flag: {path!r}")
    parts = Path(path).parts
    if ".." in parts or "." in parts:
        raise InvalidRepoPathError(f"path must not traverse: {path!r}")
    return "/".join(parts)


def is_git_repo(root: str | Path) -> bool:
    """True when ``root`` is inside a git work tree."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _parse_name_status(rest: str) -> tuple[list[CommitFile], int]:
    """Parse the NUL-separated name-status tail of one commit block.

    git terminates the pretty-format head with ``\\0`` and then emits a
    literal ``\\n`` before the first status record (even under ``-z``), so
    the first token arrives as ``"\\nM"`` — strip only that separator
    newline; path bytes are otherwise left untouched.
    """
    tokens = [t.lstrip("\n\r") for t in rest.split("\0")]
    tokens = [t for t in tokens if t]
    files: list[CommitFile] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        if not status or not (status[0].isalpha() and status.isascii()):
            i += 1
            continue
        letter = status[0].upper()
        if letter in _RENAME_STATUSES and i + 2 < len(tokens):
            # ``R100\0old\0new`` — the NEW path is the one that exists now.
            files.append(CommitFile(status=letter, path=tokens[i + 2]))
            i += 3
        elif i + 1 < len(tokens):
            files.append(CommitFile(status=letter, path=tokens[i + 1]))
            i += 2
        else:
            i += 1
    truncated = 0
    if len(files) > MAX_FILES_PER_COMMIT:
        truncated = len(files) - MAX_FILES_PER_COMMIT
        files = files[:MAX_FILES_PER_COMMIT]
    return files, truncated


def _parse_log_output(stdout: str) -> list[HistoryCommit]:
    """Parse ``%x01sha%x02date%x02author%x02subject\0\n<status>\0<path>\0…``."""
    commits: list[HistoryCommit] = []
    for block in stdout.split("\x01"):
        if not block:
            continue
        head, _, rest = block.partition("\0")
        parts = head.split("\x02")
        if len(parts) < 4:
            continue
        files, truncated = _parse_name_status(rest)
        commits.append(
            HistoryCommit(
                sha=parts[0],
                date_iso=parts[1],
                author=parts[2],
                subject=parts[3],
                files=files,
                files_truncated=truncated,
            )
        )
    return commits


def search_history(
    root: str | Path,
    query: str | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
    path: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[list[HistoryCommit], str | None]:
    """Bounded history retrieval; returns ``(commits, reason)``.

    ``reason`` is None on success; otherwise a stable machine string
    (``not_a_git_repo`` | ``empty_history`` | ``git_unavailable`` |
    ``git_timeout`` | ``git_failed``) alongside an empty commit list.
    """
    root = str(root)
    if not is_git_repo(root):
        return [], "not_a_git_repo"

    argv = [
        "git",
        "-C",
        root,
        "-c",
        "core.quotepath=false",
        "log",
        f"--max-count={max(1, int(limit))}",
        "--format=%x01%H%x02%aI%x02%an%x02%s",
        "--name-status",
        "-z",
    ]
    tokens = [t for t in (query or "").split() if t]
    if tokens:
        argv.append("-i")
        argv.append("--fixed-strings")
        argv.extend(f"--grep={t}" for t in tokens)
    if path is not None and path != "":
        argv.extend(["--", validate_relative_path(path)])

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_seconds
        )
    except subprocess.TimeoutExpired:
        return [], "git_timeout"
    except OSError:
        return [], "git_unavailable"

    if proc.returncode != 0:
        stderr = proc.stderr or ""
        if "does not have any commits yet" in stderr or "unknown revision" in stderr:
            return [], "empty_history"
        return [], "git_failed"
    return _parse_log_output(proc.stdout), None


def head_commit_sha(
    root: str | Path, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> str | None:
    """Current HEAD sha (or None) — freshness/monitoring signal for history."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


__all__ = [
    "CommitFile",
    "HistoryCommit",
    "InvalidRepoPathError",
    "is_git_repo",
    "search_history",
    "head_commit_sha",
    "validate_relative_path",
    "MAX_FILES_PER_COMMIT",
    "DEFAULT_LIMIT",
    "DEFAULT_TIMEOUT_SECONDS",
]
