"""Read-only AST search over Python sources (W4 spike).

The spike answers one question before anyone mounts a code-intelligence tool in
the worker: **can a parser-based search be useful and still be unable to leave
the workspace?** It parses files with :mod:`ast` (no import, no execution), reads
only regular files under one root, and has hard caps on files, bytes, results and
elapsed time.

Deliberate limits of the spike, stated so they are not mistaken for the product:

* Python only. A real tool needs per-language parsers (tree-sitter in W7); this
  one proves the boundary and the ranking shape, not language coverage.
* Definitions and module-level assignments only. No call graph, no references.
* Not mounted into the engine. It runs as a stand-alone, ``-I`` process inside
  the worker in the W4 drill; mounting it is a later packaging decision.

    python3 -I ast_search.py --root <dir> --query <text> [--kind function]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SUFFIXES = frozenset({".py"})
SKIP_DIRS = frozenset(
    {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
     ".mypy_cache", ".pytest_cache", ".ruff_cache", ".checkpoints"}
)
MAX_FILES = 2000
MAX_FILE_BYTES = 1_000_000
MAX_RESULTS = 200
MAX_SECONDS = 5.0
KINDS = ("function", "method", "class", "constant")


class AstSearchError(ValueError):
    """The request or the root is not something this search can honour."""


def _inside(root: Path, target: Path) -> bool:
    root_real = os.path.realpath(root)
    target_real = os.path.realpath(target)
    return target_real == root_real or target_real.startswith(root_real + os.sep)


def _definitions(tree: ast.AST) -> Iterator[tuple[str, str, str, int]]:
    """(kind, name, qualname, line) for every definition, in source order."""

    def walk(body: list[ast.stmt], prefix: str, in_class: bool) -> Iterator[
        tuple[str, str, str, int]
    ]:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if in_class else "function"
                qual = f"{prefix}{node.name}"
                yield kind, node.name, qual, node.lineno
                yield from walk(node.body, qual + ".", False)
            elif isinstance(node, ast.ClassDef):
                qual = f"{prefix}{node.name}"
                yield "class", node.name, qual, node.lineno
                yield from walk(node.body, qual + ".", True)

    yield from walk(tree.body, "", False)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield "constant", target.id, target.id, node.lineno
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield "constant", node.target.id, node.target.id, node.lineno


def search(
    root: str | Path,
    query: str,
    kind: str | None = None,
    *,
    max_results: int = MAX_RESULTS,
    max_seconds: float = MAX_SECONDS,
) -> dict[str, Any]:
    """Find definitions whose name or qualified name contains ``query``.

    Raises :class:`AstSearchError` for an unusable root/query. Unreadable or
    unparseable files are recorded in ``errors`` and do not abort the search —
    one broken file must not hide every other match.
    """
    root_p = Path(root)
    if not root_p.is_dir():
        raise AstSearchError(f"root is not a directory: {root_p}")
    text = (query or "").strip()
    if not text:
        raise AstSearchError("query is required")
    if kind is not None and kind not in KINDS:
        raise AstSearchError(f"kind must be one of {KINDS}")
    if max_results <= 0 or max_seconds <= 0:
        raise AstSearchError("max_results and max_seconds must be positive")

    needle = text.lower()
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    scanned = bytes_read = 0
    truncated = False

    for current, dirnames, filenames in os.walk(root_p, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if time.monotonic() - started > max_seconds:
                truncated = True
                break
            path = Path(current) / name
            if path.suffix not in SUFFIXES:
                continue
            rel = path.relative_to(root_p).as_posix()
            if path.is_symlink() or not _inside(root_p, path):
                skipped.append(rel)  # never follow a link out of the workspace
                continue
            try:
                size = path.stat().st_size
            except OSError:
                skipped.append(rel)
                continue
            if size > MAX_FILE_BYTES:
                skipped.append(rel)
                continue
            scanned += 1
            bytes_read += size
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                errors.append({"path": rel, "error": type(exc).__name__})
                continue
            try:
                tree = ast.parse(source, filename=str(path))
            except (SyntaxError, ValueError) as exc:
                errors.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})
                continue
            for found_kind, found_name, qualname, line in _definitions(tree):
                if kind is not None and found_kind != kind:
                    continue
                if needle not in found_name.lower() and needle not in qualname.lower():
                    continue
                results.append({
                    "path": rel, "line": line, "kind": found_kind,
                    "name": found_name, "qualname": qualname,
                })
                if len(results) >= max_results:
                    truncated = True
                    break
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

    results.sort(key=lambda r: (r["path"], r["line"], r["kind"], r["qualname"]))
    return {
        "root": str(root_p),
        "query": text,
        "kind": kind,
        "scannedFiles": scanned,
        "bytesRead": bytes_read,
        "skipped": skipped,
        "errors": errors,
        "results": results,
        "truncated": truncated,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "limits": {"files": MAX_FILES, "fileBytes": MAX_FILE_BYTES,
                   "results": max_results, "seconds": max_seconds},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="read-only AST definition search")
    ap.add_argument("--root", required=True)
    ap.add_argument("--query", required=True)
    ap.add_argument("--kind", choices=KINDS, default=None)
    ap.add_argument("--max-results", type=int, default=MAX_RESULTS)
    ap.add_argument("--max-seconds", type=float, default=MAX_SECONDS)
    args = ap.parse_args(argv)
    try:
        out = search(args.root, args.query, args.kind,
                     max_results=args.max_results, max_seconds=args.max_seconds)
    except AstSearchError as exc:
        print(json.dumps({"error": {"code": "BAD_SEARCH", "message": str(exc)}}))
        return 2
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
