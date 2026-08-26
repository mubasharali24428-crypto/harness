"""Language-aware chunking for codebase ingestion.

Start scope (M21-X1): deterministic line-window chunks per file for
.py/.ts/.tsx/.js/.jsx/.md/.sql/.json — AST-aware splitting is explicitly Later.
A chunker must be pure and deterministic: the SAME bytes always yield the SAME
chunks, because incremental re-index keys off chunk_hash equality (service.py).

Chunk = {lang, start_line, end_line, content} with 1-indexed inclusive line
numbers, so a scored hit can be opened at an exact editor location (W21-C1
contract: scored chunks carry paths + line ranges).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Files we index. Everything else is skipped by the walker (binaries, lockfiles,
# generated dirs) rather than force-fed to the embedding backend.
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".md": "markdown",
    ".sql": "sql",
    ".json": "json",
}

# Directory names never indexed anywhere in the tree.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        "vendor",
    }
)

# Never-index file names (lockfiles/noise that would pollute retrieval).
SKIP_FILES = frozenset({"package-lock.json", "yarn.lock", "poetry.lock", "uv.lock"})

DEFAULT_WINDOW = 120  # lines per chunk
DEFAULT_OVERLAP = 20  # lines of overlap between consecutive windows


@dataclass(frozen=True)
class Chunk:
    """One indexed unit of a file. Line numbers are 1-indexed and INCLUSIVE."""

    path: str  # repo-relative POSIX path
    lang: str  # language tag from SUPPORTED_EXTENSIONS
    start_line: int
    end_line: int
    content: str

    @property
    def hash_input(self) -> str:
        """Exact string fed to chunk_hash: path + window bounds + content.

        Bounds are part of identity on purpose — a pure append below an old
        last window legitimately changes that window's end_line, and callers
        should re-embed it rather than silently keep stale bounds.
        """
        return f"{self.path}\n{self.start_line}\n{self.end_line}\n{self.content}"

    @property
    def chunk_hash(self) -> str:
        """Stable sha256 identity used for incremental skip decisions."""
        import hashlib

        return hashlib.sha256(self.hash_input.encode("utf-8")).hexdigest()


def lang_for(path: str) -> str | None:
    """Language tag for a repo-relative path, or None if we don't index it."""
    return SUPPORTED_EXTENSIONS.get(os.path.splitext(path)[1].lower())


def should_index(path: str) -> bool:
    """True when a repo-relative path should be ingested."""
    if os.path.basename(path) in SKIP_FILES or lang_for(path) is None:
        return False
    return not any(part in SKIP_DIRS for part in path.split("/"))


PathLike = str | Path


def iter_repo_files(root: PathLike, followlinks: bool = False) -> list[str]:
    """Walk `root` and return indexable repo-relative POSIX paths, sorted.

    Deterministic order (sorted walk + sorted names) so two runs over the same
    tree agree — chunk_hash comparisons in service.py depend on it.
    """
    import pathlib

    base = pathlib.Path(root)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=followlinks):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            rel = (pathlib.Path(dirpath) / name).relative_to(base).as_posix()
            if should_index(rel):
                found.append(rel)
    return found


def chunk_file(
    root: PathLike,
    rel_path: str,
    *,
    window: int = DEFAULT_WINDOW,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Split one file into overlapping line-window chunks.

    - Empty files yield zero chunks (nothing to embed).
    - A file smaller than `window` yields exactly one chunk covering all lines.
    - Windows advance by (window - overlap); the final chunk always extends to
      EOF so no trailing lines are ever dropped.
    """
    return chunk_text(
        rel_path,
        Path(root, rel_path).read_text(encoding="utf-8", errors="replace"),
        window=window,
        overlap=overlap,
    )


def chunk_text(
    rel_path: str,
    text: str,
    *,
    window: int = DEFAULT_WINDOW,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Line-window chunking over in-memory text (chunk_file's core)."""
    lang = lang_for(rel_path)
    if lang is None:
        raise ValueError(f"unsupported extension: {rel_path}")
    if window <= overlap:
        raise ValueError("window must exceed overlap")

    # errors="replace" keeps binary-ish .py/.md stragglers indexable; NULs are
    # noise for embeddings either way.
    lines = text.splitlines()
    if not lines:
        return []

    step = window - overlap
    chunks: list[Chunk] = []
    start = 0  # 0-indexed; emitted as start+1
    n = len(lines)
    while True:
        end = min(start + window, n)
        content = "\n".join(lines[start:end])
        chunks.append(
            Chunk(
                path=rel_path,
                lang=lang,
                start_line=start + 1,
                end_line=end,
                content=content,
            )
        )
        if end >= n:
            return chunks
        start += step
