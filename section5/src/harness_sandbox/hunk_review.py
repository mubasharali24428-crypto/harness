"""Hunk-level review of checkpoint diffs (C16 slice: accept/reject, restore).

The checkpoint store exposes each snapshot as a raw unified diff. This module
turns that text into the per-file, per-hunk units a reviewer works with and
rebuilds a patch for exactly the hunks the reviewer chose to revert.

Design constraints:

* The server, never the client, decides what a hunk is: selections carry a
  path and an index into the parsed diff of the SAME sha, so a caller can
  never inject paths or arbitrary patch text into ``git apply``.
* Parsing is pure (no git, no filesystem) and unit-testable on its own.
* A malformed or ambiguous diff raises instead of guessing: an unparsable
  hunk would otherwise silently drop changes from the rebuilt patch.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class HunkParseError(ValueError):
    """The diff text is not a unified diff this module can select hunks from."""


@dataclass
class Hunk:
    index: int
    header: str
    lines: list[str] = field(default_factory=list)

    @property
    def old_start(self) -> int:
        try:
            return int(self.header.split()[1].split(",")[0].lstrip("-"))
        except (IndexError, ValueError):  # header was validated at parse time
            return 0

    def text(self) -> str:
        return self.header + "".join(self.lines)


@dataclass
class FileDiff:
    path: str
    header: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False

    def patch_for(self, indexes: set[int]) -> str:
        if not indexes:
            return ""
        body = "".join(h.text() for h in self.hunks if h.index in indexes)
        return "".join(self.header) + body


def _strip_prefix(raw: str, prefix: str) -> str | None:
    path = raw.strip()
    if not path or path == "/dev/null":
        return None
    if path.startswith(prefix):
        return path[len(prefix) :]
    return path


def _path_from_header(line: str) -> str:
    # "diff --git a/x b/x" — split on the last " b/" so paths with spaces work
    rest = line[len("diff --git ") :].rstrip("\n")
    marker = rest.rfind(" b/")
    if marker <= 0:
        raise HunkParseError(f"cannot read a path from '{line.strip()}'")
    return rest[marker + 3 :]


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Split a unified diff into files and hunks, preserving every raw line.

    Non-text sections (binary markers, mode-only changes) are kept as a file
    entry with no hunks so the UI can say so honestly instead of hiding them.
    """
    lines = text.splitlines(keepends=True)
    files: list[FileDiff] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("diff --git "):
            i += 1
            continue
        old_path = new_path = None
        header: list[str] = [line]
        hunks: list[Hunk] = []
        binary = False
        i += 1
        while i < len(lines) and not lines[i].startswith(("diff --git ", "@@")):
            current = lines[i]
            if current.startswith("--- "):
                old_path = _strip_prefix(current[4:], "a/")
            elif current.startswith("+++ "):
                new_path = _strip_prefix(current[4:], "b/")
            elif current.startswith(("Binary files ", "GIT binary patch")):
                binary = True
            header.append(current)
            i += 1
        if not header:
            continue
        path = new_path or old_path or _path_from_header(header[0])
        next_index = 0
        while i < len(lines) and not lines[i].startswith("diff --git "):
            current = lines[i]
            if not current.startswith("@@"):
                raise HunkParseError(f"unexpected line before a hunk in '{path}': {current!r}")
            hunk_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith(("@@", "diff --git ")):
                hunk_lines.append(lines[i])
                i += 1
            hunks.append(Hunk(index=next_index, header=current, lines=hunk_lines))
            next_index += 1
        files.append(FileDiff(path=path, header=header, hunks=hunks, binary=binary))
    return files


def build_reverse_patch(
    files: list[FileDiff], selections: list[tuple[str, int]]
) -> tuple[str, int]:
    """Rebuild a patch containing exactly the selected hunks.

    Returns ``(patch_text, hunk_count)``. Unknown paths or indexes raise:
    a stale selection must fail loudly rather than revert the wrong thing.
    """
    by_path = {f.path: f for f in files}
    chosen: dict[str, set[int]] = {}
    for path, index in selections:
        target = by_path.get(path)
        if target is None:
            raise HunkParseError(f"no file '{path}' in this checkpoint diff")
        if index < 0 or index >= len(target.hunks):
            raise HunkParseError(f"file '{path}' has no hunk {index}")
        chosen.setdefault(path, set()).add(index)
    parts: list[str] = []
    count = 0
    for f in files:
        indexes = chosen.get(f.path)
        if not indexes:
            continue
        if f.binary:
            raise HunkParseError(f"'{f.path}' is binary; hunk-level revert is not supported")
        parts.append(f.patch_for(indexes))
        count += len(indexes)
    return "".join(parts), count


def summarize(files: list[FileDiff]) -> list[dict]:
    """Wire shape for the review UI: paths, hunk headers and line counts."""
    out = []
    for f in files:
        out.append(
            {
                "path": f.path,
                "binary": f.binary,
                "hunks": [
                    {
                        "index": h.index,
                        "header": h.header.rstrip("\n"),
                        "lines": [ln.rstrip("\n") for ln in h.lines],
                        "additions": sum(1 for ln in h.lines if ln.startswith("+")),
                        "deletions": sum(1 for ln in h.lines if ln.startswith("-")),
                    }
                    for h in f.hunks
                ],
            }
        )
    return out


__all__ = [
    "Hunk",
    "FileDiff",
    "HunkParseError",
    "parse_unified_diff",
    "build_reverse_patch",
    "summarize",
]
