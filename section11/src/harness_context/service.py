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
import pathlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import harness_contracts
from harness_contracts import tier_caps
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .chunking import chunk_file, chunk_text  # module-level: the incremental test seam
from .classify import classify_repo
from .docs import (
    MAX_DOCS_PER_REQUEST,
    DocIngestReport,
    collect_folder_docs,
    fetch_url,
    url_doc_rel_path,
)
from .embeddings import EmbeddingBackend
from .manifest import FreshnessReport, build_freshness_report
from .models import IndexStatus, ScoredChunk
from .secret_scan import scrub_secret_text
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


class TierCapError(Exception):
    """M22-M2: the team's plan cap refused this index operation.

    Carries the structured upgrade payload from
    harness_contracts.cap_hit_wire ({error, upgrade}) so the API layer can
    answer 402 with a renderable CTA — never a bare dead-end denial.
    """

    def __init__(self, wire: dict) -> None:
        super().__init__(wire["error"]["message"])
        self.wire = wire


def _chunk_count(rows) -> int:
    """Stored-chunk count from load_hashes' (path -> {bounds: hash}) map."""
    return sum(len(bounds) for bounds in rows.values())


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
        """Create the job row (in-memory) and schedule the run; returns 'pending' view.

        M22-M2: raises TierCapError when the owning team's plan caps the
        context index size — BEFORE any embedding work is scheduled.
        """
        import pathlib

        root = pathlib.Path(repo_path)
        if not root.is_dir():
            raise RepoIndexError(f"repo_path is not a directory: {repo_path}")
        if project_id in self._project_locks and self._project_locks[project_id].locked():
            raise RepoIndexError(f"an index job is already running for project {project_id}")

        await self._enforce_context_cap(project_id)

        lock = self._project_locks.setdefault(project_id, asyncio.Lock())
        job = IndexJob(id=str(uuid.uuid4()), project_id=project_id, repo_path=str(root))
        self.jobs[job.id] = job
        asyncio.get_running_loop().create_task(self._run_index(job, root, lock))
        return job.to_status()

    async def _enforce_context_cap(self, project_id: str) -> None:
        """M22-M2: refuse indexing when the team's tier caps the index size.

        Resolves the cap through harness_contracts.tier_caps (tier default,
        env override, per-team billing_plans 0031 override columns). A
        missing/unreadable plan row means the FREE-tier default applies
        (enforcement never depends on PUT /billing/plan having happened).
        """
        async with self.session_factory() as session:
            existing = await self.store.load_hashes(session, project_id)
            try:
                row = (
                    await session.execute(
                        text(
                            "SELECT plan, runs_per_day_override,"
                            " context_index_max_chunks_override"
                            " FROM billing_plans WHERE team_id = ("
                            "SELECT p.team_id FROM projects p WHERE p.id = :pid)"
                        ),
                        {"pid": project_id},
                    )
                ).first()
            except Exception:  # noqa: BLE001 — absent table ⇒ free defaults
                row = None
                if session.in_transaction():
                    await session.rollback()
        used = _chunk_count(existing)
        overrides = (
            (
                int(row.runs_per_day_override)
                if row.runs_per_day_override is not None
                else None,
                int(row.context_index_max_chunks_override)
                if row.context_index_max_chunks_override is not None
                else None,
            )
            if row is not None
            else (None, None)
        )
        caps = tier_caps(
            row.plan if row is not None else "free",
            runs_per_day_override=overrides[0],
            context_index_max_chunks_override=overrides[1],
        )
        limit = caps.context_index_max_chunks
        if limit is not None and used >= limit:
            raise TierCapError(
                harness_contracts.cap_hit_wire(
                    tier=caps.tier,
                    cap=limit,
                    cap_field="contextIndexMaxChunks",
                    used=used,
                    message=(
                        f"{caps.tier.capitalize()} plan index limit reached:"
                        f" {used} of {limit} stored chunks for this project."
                        + (
                            f" Upgrade to {caps.upgrade_to.capitalize()} for an"
                            " unlimited index."
                            if caps.upgrade_to
                            else ""
                        )
                    ),
                )
            )

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
        """One full incremental pass over `root` into job.project_id's index.

        Classification (new/changed/unchanged/deleted) comes from
        :func:`harness_context.classify.classify_repo` — the same single
        source of truth the freshness report uses, so index math and reported
        staleness can never disagree.
        """
        job.status = "indexing"
        root = pathlib.Path(root)

        async with self.session_factory() as session:
            async with session.begin():
                existing = await self.store.load_hashes(session, job.project_id)
                result = classify_repo(
                    root,
                    existing,
                    window=self.window,
                    overlap=self.overlap,
                    # Module-global lookup (NOT a local import) so the
                    # established seam of monkeypatching service.chunk_file
                    # keeps steering the re-index geometry.
                    chunk_file=chunk_file,
                )

                job.files_skipped = len(result.unchanged)

                # Prune files that vanished from disk since last index.
                for gone_path in result.deleted:
                    job.files_pruned += await self.store.delete_file(
                        session, job.project_id, gone_path
                    )

                # Embed ONLY genuinely new/changed chunks, then write.
                for rel in sorted(result.pending):
                    chunks = result.pending[rel]
                    embeddings = await self._embed_many([c.content for c in chunks])
                    for chunk, vec in zip(chunks, embeddings, strict=True):
                        await self.store.upsert_chunk(
                            session, project_id=job.project_id, chunk=chunk, embedding=vec
                        )
                    bounds = result.stale_bounds.get(rel) or set()
                    if bounds:
                        await self.store.delete_pairs(session, job.project_id, rel, bounds)
                    job.files_indexed += 1
                    job.chunks_indexed += len(chunks)

    # -------------------------------------------------------------- freshness

    async def index_docs(
        self,
        project_id: str,
        *,
        folder: str | None = None,
        urls: list[str] | None = None,
    ) -> DocIngestReport:
        """F020: ingest documentation from ONE explicit source into the index.

        Folder sources are walked with the same secret-file gate code
        ingestion uses; URL sources are fetched with a real HTTP(S) client and
        every failure is recorded per-URL without aborting the rest. All text
        is secret-scrubbed BEFORE chunking (content, chunk_hash and embedding
        see only the redacted form — same law as repo ingestion), then stored
        under the ``docs/`` namespace so retrieval provenance stays legible.
        Re-ingesting the same doc replaces its chunks (delete_file + insert).
        """
        await self._enforce_context_cap(project_id)
        report = DocIngestReport()
        sources: list[tuple[str, str]] = []

        if folder:
            collected, skipped = collect_folder_docs(pathlib.Path(folder))
            sources.extend(collected)
            report.skipped.extend(skipped)

        for url in urls or []:
            if len(sources) >= MAX_DOCS_PER_REQUEST:
                report.skipped.append({"path": url, "reason": "over_request_limit"})
                continue
            try:
                text_doc = await fetch_url(url)
            except Exception as exc:  # noqa: BLE001 — one bad URL never kills the batch
                report.errors.append(
                    {"source": url, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            sources.append((url_doc_rel_path(url), text_doc))

        async with self.session_factory() as session:
            async with session.begin():
                for rel, text_doc in sources:
                    try:
                        chunks = chunk_text(
                            rel,
                            scrub_secret_text(text_doc),
                            window=self.window,
                            overlap=self.overlap,
                        )
                    except ValueError as exc:
                        report.skipped.append({"path": rel, "reason": str(exc)})
                        continue
                    if not chunks:
                        report.skipped.append({"path": rel, "reason": "empty"})
                        continue
                    await self.store.delete_file(session, project_id, rel)
                    embeddings = await self._embed_many([c.content for c in chunks])
                    for chunk, vec in zip(chunks, embeddings, strict=True):
                        await self.store.upsert_chunk(
                            session, project_id=project_id, chunk=chunk, embedding=vec
                        )
                    report.files_indexed += 1
                    report.chunks_indexed += len(chunks)
        return report

    async def freshness(self, project_id: str, root) -> FreshnessReport:
        """Content-addressed freshness report for one indexed repo root.

        Same classification, no embeddings: cheap enough to call from a
        status surface. Does not mutate the index.
        """
        root = pathlib.Path(root)
        async with self.session_factory() as session:
            existing = await self.store.load_hashes(session, project_id)
            result = classify_repo(
                root,
                existing,
                window=self.window,
                overlap=self.overlap,
                chunk_file=chunk_file,
            )
        return build_freshness_report(
            project_id=project_id,
            repo_path=str(root),
            existing=existing,
            classification=result,
        )

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

    async def search_multi(
        self,
        conn: AsyncSession,
        query: str,
        project_ids: list[str],
        top_k: int = 8,
    ) -> dict[str, list[ScoredChunk]]:
        """F013 — one query across several projects' indexes.

        The query is embedded ONCE and scored independently per project, so
        results are comparable across repositories. Authorization is the
        API layer's job (each project_id must be proven to belong to the
        caller's team BEFORE this is called); this method performs no access
        control of its own — callers that bypass the API are in-process code.
        """
        [query_vec] = await self._embed_many([query])
        out: dict[str, list[ScoredChunk]] = {}
        for project_id in project_ids:
            rows = await self.store.fetch_vectors(conn, project_id)
            scored = [(row, _cosine(query_vec, row["vec"])) for row in rows]
            scored.sort(key=lambda pair: pair[1], reverse=True)
            out[project_id] = [
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
        return out

    # ------------------------------------------------------------- ranking

    async def rank(
        self,
        conn: AsyncSession,
        goal: str,
        project_id: str,
        *,
        max_tokens: int = 8000,
        top_k: int = 12,
    ):
        """F016 — goal -> ranked, token-budgeted bundle (see ranking.py)."""
        from .ranking import derive_queries
        from .ranking import rank as _rank

        queries = derive_queries(goal)
        if not queries:
            from .ranking import RankedContext

            return RankedContext(
                project_id=project_id, queries=[], max_tokens=max_tokens
            )
        query_vecs = await self._embed_many(queries)
        rows = await self.store.fetch_vectors(conn, project_id)
        bundle = _rank(
            rows, query_vecs, queries, max_tokens=max_tokens, top_k=top_k
        )
        bundle.project_id = project_id
        return bundle

    # ----------------------------------------------------------- repo registry

    def latest_job_root(self, project_id: str) -> str | None:
        """repo_path of the most recent index job for a project (or None).

        The in-memory job registry is this service's only repo-root memory
        (same single-process posture as job status). Routes that need real
        filesystem access (git history, freshness) resolve roots through this
        and fail closed when no job is known — never a caller-supplied path.
        Durability across restarts is an integration item reported as
        NEEDS INTEGRATION (durable repo-root column owned by the migration
        lane); it does not block this session's functionality.
        """
        project_jobs = [j for j in self.jobs.values() if j.project_id == project_id]
        if not project_jobs:
            return None
        return max(project_jobs, key=lambda j: j.started_at).repo_path

    def is_known_repo(self, project_id: str, repo_path: str) -> bool:
        """True when ``repo_path`` is a root this project has indexed.

        Paths are compared canonically (:func:`os.path.realpath`) so a
        symlinked equivalent of a known root still matches, while an
        unrelated path never does.
        """
        import os

        candidate = os.path.realpath(repo_path)
        return any(
            j.project_id == project_id and os.path.realpath(j.repo_path) == candidate
            for j in self.jobs.values()
        )
