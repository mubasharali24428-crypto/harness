"""Language-aware chunking for codebase ingestion.

Start scope (M21-X1): deterministic line-window chunks per file for
.ts/.tsx/.js/.jsx/.md/.sql/.json. M23-Z2 bet A upgraded ``.py`` to AST-aware
chunking: top-level statement spans from the ``ast`` module (functions/classes/
assignments stay intact with their decorators and docstrings) replace blind
line windows. A .py file that fails to parse (or any non-.py language) still
goes through the original overlapping line windows, so ingestion can never
regress on odd bytes.

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

# AST chunking (M23-Z2 bet A): a .py top-level statement larger than this many
# lines is split back into overlapping windows so no single monster function
# can balloon one embedding's text.
MAX_AST_CHUNK_LINES = 400


def _make_chunk(rel_path: str, lang: str, start0: int, end0: int, lines) -> Chunk:
    """Chunk from 0-indexed inclusive [start0, end0] line bounds."""
    return Chunk(
        path=rel_path,
        lang=lang,
        start_line=start0 + 1,
        end_line=end0 + 1,
        content="\n".join(lines[start0 : end0 + 1]),
    )


def _ast_chunk_or_none(rel_path: str, lang: str, start0: int, end0: int, lines):
    """AST-path chunk factory; whitespace-only spans (blank separators between
    top-level statements) are skipped — they tokenize to nothing."""
    if end0 < start0:
        return None
    content = "\n".join(lines[start0 : end0 + 1])
    if not content.strip():
        return None
    return Chunk(
        path=rel_path,
        lang=lang,
        start_line=start0 + 1,
        end_line=end0 + 1,
        content=content,
    )


def _window_chunks(rel_path, lang, lines, *, window: int, overlap: int) -> list[Chunk]:
    """Overlapping line windows over in-memory lines (the original splitter)."""
    step = window - overlap
    chunks: list[Chunk] = []
    start = 0  # 0-indexed; emitted as start+1
    n = len(lines)
    while True:
        end = min(start + window, n)
        chunks.append(_make_chunk(rel_path, lang, start, end - 1, lines))
        if end >= n:
            return chunks
        start += step


def _py_statement_spans(lines) -> list[tuple[int, int]]:
    """Top-level statement spans of a parsed .py file, 0-indexed INCLUSIVE.

    Uses each node's full span (decorated_def includes decorators; class
    bodies include every method). Span ends are clamped into the file's real
    line range because ``ast`` can report end_lineno one past a final line
    that lacks its newline. Returns [] when the source does not parse.
    """
    import ast

    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return []
    n = len(lines)
    spans = [
        (
            max(0, getattr(node, "lineno", 1) - 1 - _leading_decorators(node)),
            min(n - 1, node.end_lineno - 1),
        )
        for node in tree.body
    ]
    # Merge overlaps defensively (a multiline header can swallow the next
    # statement's first line): earlier span wins, later span starts after it.
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            continue
        merged.append((start, end))
    return merged


def _leading_decorators(node) -> int:
    """Decorator-line count for FunctionDef/AsyncFunctionDef/ClassDef nodes."""
    return len(getattr(node, "decorator_list", ()) or ())


def _py_ast_chunks(rel_path, lang, lines, *, window: int, overlap: int) -> list[Chunk]:
    """AST-boundary .py chunks with window-split fallback inside one statement."""
    chunks: list[Chunk] = []
    prev_end = -1  # exclusive; orphan lines before/after statements ride along
    for start, end in _py_statement_spans(lines):
        if start > prev_end + 1:
            gap = _ast_chunk_or_none(rel_path, lang, prev_end + 1, start - 1, lines)
            if gap is not None:
                chunks.append(gap)
        if end - start + 1 > MAX_AST_CHUNK_LINES:
            # Oversize statement: overlap-windows JUST these lines so no
            # embedding balloons, then resume clean statement boundaries.
            chunks.extend(
                _window_chunks(
                    rel_path, lang, lines[start : end + 1], window=window, overlap=overlap
                )
            )
        else:
            chunks.append(_make_chunk(rel_path, lang, start, end, lines))
        prev_end = end
    if prev_end < len(lines) - 1:
        tail = _ast_chunk_or_none(rel_path, lang, prev_end + 1, len(lines) - 1, lines)
        if tail is not None:
            chunks.append(tail)
    return chunks


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
    """Split one file into chunks (AST statement boundaries for .py; else
    overlapping line windows).

    - Empty files yield zero chunks (nothing to embed).
    - Windows advance by (window - overlap); the final window always extends
      to EOF so no trailing lines are ever dropped.
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
    """Chunk in-memory text: AST boundaries for .py, line windows otherwise."""
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

    if lang == "python":
        return _py_ast_chunks(rel_path, lang, lines, window=window, overlap=overlap)
    return _window_chunks(rel_path, lang, lines, window=window, overlap=overlap)


def chunk_python_text(
    rel_path: str,
    text: str,
) -> list[tuple[Chunk, str]]:
    """AST-aware .py chunking with the line-window fallback made EXPLICIT.

    Returns the chunks plus how they were produced so tests (and the M23-Z2
    evidence probe) can pin the contract directly:
    ``[(Chunk, 'ast' | 'window'), ...]``.
    """
    lang = lang_for(rel_path)
    if lang != "python":
        raise ValueError(f"not a python path: {rel_path}")

    lines = text.splitlines()
    if not lines or _py_statement_spans(lines) == []:
        tagged = [
            (c, "window")
            for c in _window_chunks(
                rel_path, "python", lines, window=DEFAULT_WINDOW, overlap=DEFAULT_OVERLAP
            )
        ]
        return tagged

    spans = _py_statement_spans(lines)
    out: list[tuple[Chunk, str]] = []
    prev_end = -1
    for start, end in spans:
        if start > prev_end + 1:
            gap = _ast_chunk_or_none(rel_path, lang, prev_end + 1, start - 1, lines)
            if gap is not None:
                out.append((gap, "ast"))
        size = end - start + 1
        if size > MAX_AST_CHUNK_LINES:
            out.extend(
                (c, "window")
                for c in _window_chunks(
                    rel_path,
                    lang,
                    lines[start : end + 1],
                    window=DEFAULT_WINDOW,
                    overlap=DEFAULT_OVERLAP,
                )
            )
        else:
            out.append((_make_chunk(rel_path, lang, start, end, lines), "ast"))
        prev_end = end
    if prev_end < len(lines) - 1:
        tail = _ast_chunk_or_none(rel_path, lang, prev_end + 1, len(lines) - 1, lines)
        if tail is not None:
            out.append((tail, "ast"))
    return out
