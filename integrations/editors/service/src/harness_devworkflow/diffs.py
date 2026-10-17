"""Unified-diff engine for the developer-workflow code surface.

Pure functions (no I/O) so every integrity property — stale bases, hunk
filtering, rejected-hunk integrity, overlap rejection — is unit-testable.

Compatibility note: Section 5's applier (harness_sandbox.diff_engine) is
content-matching, not line-number based, and normalizes files to a single
trailing newline. This module normalizes content the same way so read-back
verification after apply is exact.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from itertools import pairwise

MAX_FILE_BYTES = 1_048_576  # 1 MiB: text code-surface ceiling, honestly enforced.
CONTEXT_LINES = 3


class DiffError(ValueError):
    """Raised for undiffable/binary/oversized content or bad hunk math."""


class OverlappingHunksError(DiffError):
    """Raised when hunk ranges overlap — curating them independently is unsafe."""


@dataclass
class Hunk:
    """One unified-diff hunk, line-oriented relative to the base document.

    `old_start`/`new_start` are 1-based line numbers; a creation hunk uses
    old_start=0, old_count=0 (unified-diff convention for new files).
    `lines` items are (kind, text) with kind in {"ctx", "add", "del"}.
    """

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]] = field(default_factory=list)

    @property
    def old_range(self) -> tuple[int, int]:
        """Half-open (start, end) line-index range in the base document (0-based)."""
        start = max(self.old_start - 1, 0)
        return (start, start + self.old_count)

    @property
    def changed_old_indices(self) -> list[int]:
        """Base-line indices this hunk deletes (only meaningful when old_count > 0)."""
        start = self.old_start - 1
        idx = start
        out: list[int] = []
        for kind, _ in self.lines:
            if kind in ("ctx", "del"):
                if kind == "del":
                    out.append(idx)
                idx += 1
        return out

    @property
    def changed_old_range(self) -> tuple[int, int] | None:
        """Min..max+1 of changed base lines, or None for pure insertions."""
        idxs = self.changed_old_indices
        if not idxs:
            return None
        return (idxs[0], idxs[-1] + 1)


def normalize_text(content: str) -> str:
    """Files in the code surface always end with exactly one trailing newline.

    Mirrors Section 5's writer (`"\\n".join(lines) + "\\n"`), so hash
    comparisons before/after apply are exact.
    """
    if content is None:
        raise DiffError("content is required")
    if "\x00" in content:
        raise DiffError("binary content (NUL byte) is not supported")
    if len(content.encode("utf-8", errors="surrogatepass")) > MAX_FILE_BYTES:
        raise DiffError(f"content exceeds {MAX_FILE_BYTES} bytes")
    if content == "":
        return ""
    body = content.rstrip("\n")
    return body + "\n"


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="surrogatepass")).hexdigest()


def split_lines(text: str) -> list[str]:
    """Line list without trailing newlines, matching Section 5's splitlines()."""
    return text.splitlines()


def make_hunks(old_text: str, new_text: str, context: int = CONTEXT_LINES) -> list[Hunk]:
    """Compute non-overlapping hunks transforming old -> new (both normalized)."""
    old_lines = split_lines(old_text)
    new_lines = split_lines(new_text)
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    groups = matcher.get_grouped_opcodes(context)
    hunks: list[Hunk] = []
    for group in groups:
        lines: list[tuple[str, str]] = []
        old_start = group[0][1]
        old_end = group[-1][2]
        new_start = group[0][3]
        new_end = group[-1][4]
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for line in old_lines[i1:i2]:
                    lines.append(("ctx", line))
            else:
                for line in old_lines[i1:i2]:
                    lines.append(("del", line))
                for line in new_lines[j1:j2]:
                    lines.append(("add", line))
        hunks.append(
            Hunk(
                old_start=old_start + 1,
                old_count=old_end - old_start,
                new_start=new_start + 1,
                new_count=new_end - new_start,
                lines=lines,
            )
        )
    assert_nonoverlapping(hunks)
    return hunks


def creation_hunk(new_text: str) -> Hunk:
    """A hunk that creates a file from nothing (old side is empty)."""
    lines = [("add", line) for line in split_lines(new_text)]
    return Hunk(old_start=0, old_count=0, new_start=1, new_count=len(lines), lines=lines)


def make_hunks_with_creation(old_text: str | None, new_text: str) -> list[Hunk]:
    """Hunks for an edit where old_text None means "file does not exist yet"."""
    if old_text is None:
        return [creation_hunk(new_text)]
    return make_hunks(old_text, new_text)


def assert_nonoverlapping(hunks: list[Hunk]) -> None:
    """Reject hunk sets whose base ranges intersect (independent curation unsafe)."""
    ranges = [h.old_range for h in hunks if h.old_count > 0]
    ranges.sort()
    for (_, prev_end), (start, _) in pairwise(ranges):
        if start < prev_end:
            raise OverlappingHunksError("hunk base ranges overlap")


def edits_overlap(a_changed: list[tuple[int, int]], b_changed: list[tuple[int, int]]) -> bool:
    """True when any changed base range of edit A intersects any of edit B."""
    for a_start, a_end in a_changed:
        for b_start, b_end in b_changed:
            if a_start < b_end and b_start < a_end:
                return True
    return False


def changed_ranges(hunks: list[Hunk]) -> list[tuple[int, int]]:
    """Changed base ranges across hunks (pure insertions are point ranges)."""
    out: list[tuple[int, int]] = []
    for h in hunks:
        r = h.changed_old_range
        if r is None:
            # Pure insertion: point at the base position, zero-width range.
            pos = max(h.old_start - 1, 0)
            out.append((pos, pos))
        else:
            out.append(r)
    return out


def apply_accepted_hunks(old_text: str, hunks: list[Hunk], accepted: set[int]) -> str:
    """Rebuild the target document keeping ONLY accepted hunks (by index).

    Rejected hunks contribute their base lines unchanged — this is the
    rejected-hunk integrity guarantee: what the user rejected stays intact.
    """
    assert_nonoverlapping(hunks)
    base = split_lines(old_text)
    out: list[str] = []
    cursor = 0
    ordered = sorted(enumerate(hunks), key=lambda p: p[1].old_range)
    for index, hunk in ordered:
        start, end = hunk.old_range
        if start < cursor:
            raise OverlappingHunksError("hunk ranges overlap during apply")
        out.extend(base[cursor:start])
        if index in accepted:
            for kind, text in hunk.lines:
                if kind in ("ctx", "add"):
                    out.append(text)
        else:
            for kind, text in hunk.lines:
                if kind in ("ctx", "del"):
                    out.append(text)
        cursor = end
    out.extend(base[cursor:])
    joined = "\n".join(out)
    return joined + "\n" if joined else ""


def render_unified(path: str, hunks: list[Hunk]) -> str:
    """Render hunks as a unified diff using their exact difflib line numbers."""
    lines = [f"--- a/{path}", f"+++ b/{path}"]
    for hunk in hunks:
        old_advance = sum(1 for k, _ in hunk.lines if k in ("ctx", "del"))
        new_advance = sum(1 for k, _ in hunk.lines if k in ("ctx", "add"))
        old_start = hunk.old_start if hunk.old_count else 0
        lines.append(f"@@ -{old_start},{old_advance} +{hunk.new_start},{new_advance} @@")
        for kind, text in hunk.lines:
            prefix = {"ctx": " ", "add": "+", "del": "-"}[kind]
            lines.append(prefix + text)
    return "\n".join(lines) + "\n"


def diff_base_to_target(path: str, base_text: str | None, target_text: str) -> str:
    """The exact diff to hand Section 5's apply_edit for base -> target."""
    hunks = make_hunks_with_creation(base_text, target_text)
    return render_unified(path, hunks)


class OverlappingEditsError(DiffError):
    """Two edits of the same base touch the same or adjacent base lines."""


def merge_disjoint_edits(base_text: str, target_a: str, target_b: str) -> str:
    """Combine two edits made against the SAME base when they touch disjoint lines.

    Line-level opcodes of base->A and base->B are merged; any intersection (including
    two insertions at one position) is ambiguous and rejected rather than guessed.
    """
    base = split_lines(base_text)

    def ops(target: str) -> list[tuple[int, int, list[str]]]:
        t = split_lines(target)
        sm = difflib.SequenceMatcher(a=base, b=t, autojunk=False)
        return [(i1, i2, t[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]

    merged = sorted(ops(target_a) + ops(target_b), key=lambda o: (o[0], o[1]))
    for (a1, a2, _), (b1, _b2, _r) in pairwise(merged):
        if b1 < a2 or b1 == a1:  # intersecting, or two edits starting at the same line
            raise OverlappingEditsError("edits overlap")
    out: list[str] = []
    cursor = 0
    for i1, i2, repl in merged:
        out.extend(base[cursor:i1])
        out.extend(repl)
        cursor = i2
    out.extend(base[cursor:])
    joined = "\n".join(out)
    return joined + "\n" if joined else ""
