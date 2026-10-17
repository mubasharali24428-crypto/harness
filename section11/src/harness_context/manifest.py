"""Content-addressed freshness report for the context index (F011).

The index is only trustworthy if callers can SEE staleness. This module turns
the same classification the incremental re-indexer uses (``service.classify_repo``
— one source of truth, so the report can never disagree with what a re-index
would do) into a per-file report:

    unchanged — chunk hashes on disk are byte-identical to the stored index
    changed   — file exists in the index and on disk with different chunks
    new       — file on disk, no stored chunks for it (or previously pruned)
    deleted   — stored chunks for a path that no longer exists on disk

Freshness is CONTENT-addressed: it compares sha256 chunk identities, never
mtime, so a ``touch`` does not fake staleness and a same-mtime edit cannot
hide. The report costs one full chunking walk (no embedding) — the same work
an incremental re-index performs before deciding to skip.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classify import RepoClassification


@dataclass
class FileChange:
    """One stale file in the report."""

    path: str
    status: str  # "changed" | "new" | "deleted"
    chunks_before: int
    chunks_after: int


@dataclass
class FreshnessReport:
    """Per-project freshness snapshot; wire shape via :meth:`to_wire`."""

    project_id: str
    repo_path: str
    unchanged: int = 0
    changed: int = 0
    new: int = 0
    deleted: int = 0
    pending_chunks: int = 0  # chunks an incremental re-index would (re)embed
    changes: list[FileChange] = field(default_factory=list)

    @property
    def files_total(self) -> int:
        """Indexable files currently on disk (per classification)."""
        return self.unchanged + self.changed + self.new

    @property
    def is_fresh(self) -> bool:
        return self.changed == 0 and self.new == 0 and self.deleted == 0

    def to_wire(self) -> dict:
        return {
            "projectId": self.project_id,
            "repoPath": self.repo_path,
            "filesTotal": self.files_total,
            "unchanged": self.unchanged,
            "changed": self.changed,
            "new": self.new,
            "deleted": self.deleted,
            "pendingChunks": self.pending_chunks,
            "isFresh": self.is_fresh,
            "changes": [
                {
                    "path": c.path,
                    "status": c.status,
                    "chunksBefore": c.chunks_before,
                    "chunksAfter": c.chunks_after,
                }
                for c in self.changes
            ],
        }


def build_freshness_report(
    *,
    project_id: str,
    repo_path: str,
    existing: dict[str, dict[tuple[int, int], str]],
    classification: RepoClassification,
) -> FreshnessReport:
    """Assemble the report from stored hashes + one classification pass."""
    report = FreshnessReport(project_id=project_id, repo_path=repo_path)

    report.unchanged = len(classification.unchanged)

    for rel in sorted(classification.changed):
        report.changed += 1
        before = len(existing.get(rel, {}))
        after = len(classification.pending.get(rel, []))
        report.pending_chunks += after
        report.changes.append(
            FileChange(path=rel, status="changed", chunks_before=before, chunks_after=after)
        )

    for rel in sorted(classification.new):
        report.new += 1
        after = len(classification.pending.get(rel, []))
        report.pending_chunks += after
        report.changes.append(
            FileChange(path=rel, status="new", chunks_before=0, chunks_after=after)
        )

    for rel in sorted(classification.deleted):
        report.deleted += 1
        report.changes.append(
            FileChange(
                path=rel,
                status="deleted",
                chunks_before=len(existing.get(rel, {})),
                chunks_after=0,
            )
        )

    return report
