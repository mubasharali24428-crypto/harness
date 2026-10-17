"""Repo classification for incremental indexing (F011 core; service-agnostic).

One pass over a repo root decides, per file, which of the four fresh/stale
states it is in — by comparing freshly computed chunk hashes (pure function,
scrubbed text — see ``chunking.chunk_file``) against the stored index hashes:

    unchanged — identical chunk multiset; re-index SKIPS it (no re-embed)
    changed   — different chunk multiset; stale bounds to prune are computed
    new       — no stored chunks for the path
    deleted   — stored path absent from disk

This module is the SINGLE source of truth shared by the re-indexer
(``service.reindex``) and the freshness report (``manifest.py``): the report
can never disagree with what an incremental re-index would actually do.
"""

from __future__ import annotations

import pathlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from .chunking import Chunk, iter_repo_files
from .chunking import chunk_file as _default_chunk_file


@dataclass
class RepoClassification:
    """One classification pass over a repo root."""

    #: Files needing (re)embedding: rel path -> freshly computed chunks.
    pending: dict[str, list[Chunk]] = field(default_factory=dict)
    #: Stale windows to prune per path (present in index, absent from fresh chunks).
    stale_bounds: dict[str, set[tuple[int, int]]] = field(default_factory=dict)
    #: Paths whose chunks are byte-identical to the stored index.
    unchanged: list[str] = field(default_factory=list)
    #: Paths present in the index but gone from disk.
    deleted: list[str] = field(default_factory=list)
    #: Subsets of ``pending`` for reporting.
    changed: set[str] = field(default_factory=set)
    new: set[str] = field(default_factory=set)


def classify_repo(
    root: pathlib.Path | str,
    existing: dict[str, dict[tuple[int, int], str]],
    *,
    window: int,
    overlap: int,
    chunk_file: Callable[..., list[Chunk]] | None = None,
) -> RepoClassification:
    """Classify every indexable file under ``root`` against stored hashes.

    ``chunk_file`` is injectable so the CALLER's module-global binding is used
    (``service.reindex`` passes its own ``chunk_file`` global), keeping the
    established test seam of monkeypatching ``service.chunk_file`` effective
    for the incremental-migration tests.
    """
    chunker = chunk_file or _default_chunk_file
    rel_paths = iter_repo_files(root)
    result = RepoClassification()
    seen: set[str] = set()

    for rel in rel_paths:
        seen.add(rel)
        chunks = chunker(root, rel, window=window, overlap=overlap)
        old = existing.get(rel, {})
        new_hashes = Counter(c.chunk_hash for c in chunks)
        old_hashes = Counter(old.values())
        if new_hashes == old_hashes:
            # Identical multiset — including the both-empty case (a file that
            # is and stays empty), which must be idempotent rather than
            # reporting work on every pass.
            result.unchanged.append(rel)
            continue
        if not old:
            # No stored rows (genuinely new, or previously pruned) — nothing
            # to prune, everything to write.
            result.new.add(rel)
            result.pending[rel] = chunks
            continue
        # Content differs and something is stored: prune every stored bound
        # that did not survive; an emptied file prunes all of them.
        result.changed.add(rel)
        result.pending[rel] = chunks
        keep = {(c.start_line, c.end_line) for c in chunks} & set(old)
        result.stale_bounds[rel] = set(old) - keep

    result.deleted = sorted(set(existing) - seen)
    return result
