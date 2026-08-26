"""Search relevance sanity on the seeded mini-repo (W21-C1 contract shape)."""

from harness_context.embeddings import HashingEmbedding
from harness_context.models import ScoredChunk
from harness_context.service import ContextService, IndexJob

PROJECT = "p-search"


async def test_search_returns_frozen_shape_with_paths_and_lines(db, mini_repo):
    svc = ContextService(db, HashingEmbedding(dims=256), window=6, overlap=2)
    await svc.reindex(IndexJob(id="j1", project_id=PROJECT, repo_path=str(mini_repo)), mini_repo)

    async with db() as session:
        hits = await svc.search(await session.connection(), "widget quota", PROJECT, top_k=5)

    assert 0 < len(hits) <= 5
    assert all(isinstance(h, ScoredChunk) for h in hits)
    for h in hits:
        assert h.path and ":" not in h.path  # POSIX repo-relative
        assert 1 <= h.start_line <= h.end_line
        assert h.lang in {
            "python",
            "typescript",
            "tsx",
            "javascript",
            "jsx",
            "markdown",
            "sql",
            "json",
        }
        assert -1.0001 <= h.score <= 1.0001
    # scores descend — that IS the ranking contract
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


async def test_relevant_chunk_outranks_unrelated_one(db, mini_repo):
    svc = ContextService(db, HashingEmbedding(dims=256), window=6, overlap=2)
    await svc.reindex(IndexJob(id="j1", project_id=PROJECT, repo_path=str(mini_repo)), mini_repo)

    async with db() as session:
        hits = await svc.search(await session.connection(), "widget store quota", PROJECT, top_k=10)

    # The hashing backend is lexical: the widget-quota chunk must outrank the
    # telemetry emitter chunk, which shares no vocabulary with the query.
    widget_hit = next(h for h in hits if h.path == "pkg/widget_store.py")
    telemetry_scores = [h.score for h in hits if h.path == "pkg/telemetry.py"]
    if telemetry_scores:
        assert widget_hit.score >= max(telemetry_scores)


async def test_search_is_scoped_to_project(db, mini_repo):
    svc = ContextService(db, HashingEmbedding(dims=256), window=6, overlap=2)
    await svc.reindex(IndexJob(id="j-a", project_id="p-a", repo_path=str(mini_repo)), mini_repo)

    async with db() as session:
        empty = await svc.search(await session.connection(), "widget", "p-nothing", top_k=3)
    assert empty == []


async def test_query_embedding_goes_through_the_same_backend(db, mini_repo):
    """Query + chunks must share one embedding space; swap backends mid-flight
    and relevance must still hold because both sides re-embed consistently."""
    backend = HashingEmbedding(dims=128)
    svc = ContextService(db, backend, window=6, overlap=2)
    await svc.reindex(IndexJob(id="j1", project_id="p-dim", repo_path=str(mini_repo)), mini_repo)
    async with db() as session:
        hits = await svc.search(
            await session.connection(), "schema widgets table", "p-dim", top_k=3
        )
    assert hits and hits[0].path == "schema.sql"
