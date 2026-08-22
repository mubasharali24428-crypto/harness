"""Parse unified diff text into structured ChangedFile records.

Self-contained parser — no external VCS calls. Handles the common subset
of unified diff format produced by `git diff` and GitHub's diff endpoint.
"""

import re

from .models import ChangedFile, ChangeStatus

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(diff_text: str) -> list[ChangedFile]:
    """Parse a unified diff into a list of ChangedFile records.

    Handles new files (--- /dev/null), deleted files, renames, and
    modifications. Each file entry captures additions and deletions counts.
    """
    files: list[ChangedFile] = []
    current_path: str | None = None
    old_path: str | None = None
    current_status: ChangeStatus = ChangeStatus.MODIFIED
    current_rename_from: str | None = None
    additions = 0
    deletions = 0

    def _flush() -> None:
        nonlocal current_path, old_path, current_status
        nonlocal current_rename_from, additions, deletions
        path = current_path or old_path
        if path is not None:
            files.append(
                ChangedFile(
                    path=path,
                    status=current_status,
                    additions=additions,
                    deletions=deletions,
                    rename_from=current_rename_from,
                )
            )
        current_path = None
        old_path = None
        current_status = ChangeStatus.MODIFIED
        current_rename_from = None
        additions = 0
        deletions = 0

    lines = diff_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("diff --git "):
            _flush()
            match = re.match(r"diff --git a/(.+?) b/(.+)", line)
            if match:
                current_path = match.group(2)
            i += 1
            continue

        if line.startswith("rename from "):
            current_rename_from = line[len("rename from "):].strip()
            current_status = ChangeStatus.RENAMED
            i += 1
            continue

        if line.startswith("new file mode"):
            current_status = ChangeStatus.ADDED
            i += 1
            continue

        if line.startswith("deleted file mode"):
            current_status = ChangeStatus.REMOVED
            i += 1
            continue

        if line.startswith("similarity index"):
            current_status = ChangeStatus.RENAMED
            i += 1
            continue

        if line.startswith("--- "):
            if line[4:].strip() not in ("/dev/null", "") and not line.startswith("--- /dev/null"):
                candidate = line[4:].strip()
                if candidate.startswith("a/"):
                    candidate = candidate[2:]
                old_path = candidate
            i += 1
            continue
        if line.startswith("+++ "):
            if line[4:].strip() == "/dev/null":
                current_status = ChangeStatus.REMOVED
            i += 1
            continue

        if line.startswith("@@"):
            i += 1
            continue

        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

        i += 1

    _flush()
    return files


def extract_unchanged_context(diff_text: str, max_context: int = 5) -> str | None:
    """Extract unchanged context lines surrounding modifications.

    Returns a formatted string of representative unchanged context lines.
    """
    context_lines: list[str] = []
    in_hunk = False
    buffer: list[str] = []

    for line in diff_text.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            buffer = []
            continue
        if not in_hunk:
            continue
        if line.startswith(" ") and not line.startswith("+++") and not line.startswith("---"):
            stripped = line[1:].strip()
            if stripped:
                buffer.append(stripped)
        elif line.startswith("+") or line.startswith("-"):
            context_lines.extend(buffer[-3:])
            buffer = []

    context_lines = list(dict.fromkeys(context_lines))[:max_context]
    if not context_lines:
        return None
    return "; ".join(context_lines)


def summarize_diff(diff_text: str) -> dict:
    """Lightweight summary: total files, total additions, total deletions."""
    files = parse_diff(diff_text)
    total_add = sum(f.additions for f in files)
    total_del = sum(f.deletions for f in files)
    return {
        "total_files": len(files),
        "total_additions": total_add,
        "total_deletions": total_del,
        "files": files,
    }
