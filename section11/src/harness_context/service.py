"""Context indexing service — ingestion, incremental re-index, retrieval.

This module owns the W21-C1 search contract:

    search(query, project_id, top_k) -> list[ScoredChunk]

X2 (@refs resolver) and X4 (RAG planner) build on exactly this call signature;
keep it small and final.

Incremental law: a file whose freshly computed chunk hashes are byte-identical
to what is stored is SKIPPED entirely (no re-embed). Files deleted from disk
are pruned from the index. Both properties are proven in tests/test_ingestion.py.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .chunking import Chunk, chunk_file, iter_repo_files
from .embeddings import EmbeddingBackend
from .models import IndexStatus, ScoredChunk
from .store import ContextStore


def _cosine(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity; zero vectors score 0.0."""
    if not a or not b:
        return 0.0
    num = sum(x * y for x, y in zip(a, b, strict=True))
    if num == 0.0:
        return 0.0
    da = sum(x * x for x in a) ** 0.5
    db = sum(x * x for x in b) ** 0.5
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


@dataclass
class IndexJob:
    """In-memory job record backing GET /context/index/{job_id}."""

    id: str
    project_id: str
    repo_path: str
    status: str = "pending"  # pending | indexing | completed | failed
    files_indexed: int = 0
    chunks_indexed: int = 0
    files_skipped: int = 0
    files_pruned: int = 0
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_status(self) -> IndexStatus:
        return IndexStatus(
            jobId=self.id,
            projectId=self.project_id,
            status=self.status,
            repoPath=self.repo_path,
            filesIndexed=self.files_indexed,
            chunksIndexed=self.chunks_indexed,
            filesSkipped=self.files_skipped,
            filesPruned=self.files_pruned,
            error=self.error,
        )


class RepoIndexError(ValueError):
    """Caller-input problem (bad repo_path etc.) -> HTTP 4xx, not 500."""


class ContextService:
    """Owns the index lifecycle. One service per app; jobs tracked in-memory."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        backend: EmbeddingBackend,
        *,
        window: int = 120,
        overlap: int = 20,
    ) -> None:
        self.session_factory = session_factory
        self.backend = backend
        self.store = ContextStore()
        self.window = window
        self.overlap = overlap
        self.jobs: dict[str, IndexJob] = {}
        self._project_locks: dict[str, asyncio.Lock] = {}
        self.embed_calls = 0  # texts handed to the backend (skip-proof metric)

    # ------------------------------------------------------------------ jobs

    def get_job(self, job_id: str) -> IndexStatus | None:
        job = self.jobs.get(job_id)
        return job.to_status() if job else None

    async def start_index(self, repo_path: str, project_id: str) -> IndexStatus:
        """Create the job row (in-memory) and schedule the run; returns 'pending' view."""
        import pathlib

        root = pathlib.Path(repo_path)
        if not root.is_dir():
            raise RepoIndexError(f"repo_path is not a directory: {repo_path}")
        if project_id in self._project_locks and self._project_locks[project_id].locked():
            raise RepoIndexError(f"an index job is already running for project {project_id}")

        lock = self._project_locks.setdefault(project_id, asyncio.Lock())
        job = IndexJob(id=str(uuid.uuid4()), project_id=project_id, repo_path=str(root))
        self.jobs[job.id] = job
        asyncio.get_running_loop().create_task(self._run_index(job, root, lock))
        return job.to_status()

    async def _run_index(self, job: IndexJob, root, lock: asyncio.Lock) -> None:
        async with lock:
            try:
                await self.reindex(job, root)
                job.status = "completed"
            except Exception as exc:  # noqa: BLE001 — job boundary; error goes to the wire
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------- ingestion

    async def reindex(self, job: IndexJob, root) -> None:
        """One full incremental pass over `root` into job.project_id's index."""
        job.status = "indexing"
        rel_paths = iter_repo_files(root)

        async with self.session_factory() as session:
            async with session.begin():
                existing = await self.store.load_hashes(session, job.project_id)

                # Pass 1 — classify each present file as skipped or needing work.
                pending: dict[str, list[Chunk]] = {}
                stale_bounds: dict[str, set[tuple[int, int]]] = {}
                seen_paths: set[str] = set()

                for rel in rel_paths:
                    seen_paths.add(rel)
                    chunks = chunk_file(root, rel, window=self.window, overlap=self.overlap)
                    old = existing.get(rel, {})
                    new_hashes = Counter(c.chunk_hash for c in chunks)
                    old_hashes = Counter(old.values())
                    if new_hashes and new_hashes == old_hashes:
                        job.files_skipped += 1
                        continue
                    pending[rel] = chunks
                    # Windows we must NOT prune: any bound surviving identically.
                    keep = {(c.start_line, c.end_line) for c in chunks} & set(old)
                    stale_bounds[rel] = set(old) - keep

                # Pass 1b — prune files that vanished from disk since last index.
                for gone_path in sorted(set(existing) - seen_paths):
                    job.files_pruned += await self.store.delete_file(
                        session, job.project_id, gone_path
                    )

                # Pass 2 — embed ONLY genuinely new/changed chunks, then write.
                for rel in sorted(pending):
                    chunks = pending[rel]
                    embeddings = await self._embed_many([c.content for c in chunks])
                    for chunk, vec in zip(chunks, embeddings, strict=True):
                        await self.store.upsert_chunk(
                            session, project_id=job.project_id, chunk=chunk, embedding=vec
                        )
                    bounds = stale_bounds.get(rel) or set()
                    if bounds:
                        await self.store.delete_pairs(session, job.project_id, rel, bounds)
                    job.files_indexed += 1
                    job.chunks_indexed += len(chunks)

    async def _embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.embed_calls += len(texts)
        return await self.backend.embed(texts)

    # -------------------------------------------------------------- retrieval

    async def search(
        self, conn: AsyncSession, query: str, project_id: str, top_k: int = 8
    ) -> list[ScoredChunk]:
        """W21-C1 frozen contract: scored chunks with paths + line ranges."""
        [query_vec] = await self._embed_many([query])
        rows = await self.store.fetch_vectors(conn, project_id)
        scored = [
            (row, _cosine(query_vec, row["vec"])) for row in rows
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [
            ScoredChunk(
                path=row["path"],
                lang=row["lang"],
                startLine=row["startLine"],
                endLine=row["endLine"],
                content=row["content"],
                score=score,
            )
            for row, score in scored[: max(1, top_k)]
        ]
