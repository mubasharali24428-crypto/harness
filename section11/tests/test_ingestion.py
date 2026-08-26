"""Ingestion round-trip + incremental re-index proofs (M21-X1 deliverable 3)."""

from pathlib import Path

from sqlalchemy import text

from harness_context.chunking import chunk_file, iter_repo_files
from harness_context.embeddings import HashingEmbedding
from harness_context.service import ContextService, IndexJob

PROJECT = "p-roundtrip"


async def _service(db) -> ContextService:
    return ContextService(db, HashingEmbedding(dims=256), window=6, overlap=2)


def _expected_chunks(root: Path) -> int:
    # Same geometry as the services under test (window=6/overlap=2).
    return sum(
        len(chunk_file(root, rel, window=6, overlap=2)) for rel in iter_repo_files(root)
    )


async def _row_count(db, where="TRUE", params=None):
    async with db() as s:
        n = (
            await s.execute(
                text(f"SELECT COUNT(*) FROM context_chunks WHERE {where}"), params or {}
            )
        ).scalar_one()
    return int(n)


async def test_round_trip_indexes_supported_files_only(db, mini_repo):
    svc = await _service(db)
    job = IndexJob(id="j1", project_id=PROJECT, repo_path=str(mini_repo))
    await svc.reindex(job, mini_repo)

    # README.md + 2 .py + data.json + schema.sql = 5; junk.js/skipme.txt never enter.
    assert job.files_indexed == 5
    assert job.chunks_indexed == _expected_chunks(mini_repo)
    assert await _row_count(db, "project_id = :p", {"p": PROJECT}) == job.chunks_indexed

    async with db() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT path, lang FROM context_chunks "
                    "WHERE project_id = :p ORDER BY path"
                ),
                {"p": PROJECT},
            )
        ).fetchall()
    paths = {r[0] for r in rows}
    assert paths == {
        "README.md",
        "data.json",
        "pkg/telemetry.py",
        "pkg/widget_store.py",
        "schema.sql",
    }
    assert {r[1] for r in rows} == {"markdown", "json", "python", "sql"}
    # every stored chunk carries its sha256 identity
    async with db() as s:
        hashes = (
            await s.execute(text("SELECT chunk_hash FROM context_chunks"))
        ).scalars().all()
    assert all(len(h) == 64 for h in hashes)


async def test_rerun_skips_every_unchanged_file_with_zero_embeds(db, mini_repo):
    svc = await _service(db)
    job1 = IndexJob(id="j1", project_id=PROJECT, repo_path=str(mini_repo))
    await svc.reindex(job1, mini_repo)
    embeds_after_first = svc.embed_calls

    job2 = IndexJob(id="j2", project_id=PROJECT, repo_path=str(mini_repo))
    await svc.reindex(job2, mini_repo)

    assert job2.files_indexed == 0
    assert job2.files_skipped == 5
    assert job2.chunks_indexed == 0
    # THE PROOF: not one byte went to the embedding backend on the second run.
    assert svc.embed_calls == embeds_after_first
    assert await _row_count(db, "project_id = :p", {"p": PROJECT}) == job1.chunks_indexed


async def test_edit_reembeds_only_touched_file_and_prunes_stale_windows(db, mini_repo):
    svc = await _service(db)
    await svc.reindex(IndexJob(id="j1", project_id=PROJECT, repo_path=str(mini_repo)), mini_repo)
    before = svc.embed_calls

    # Rewrite telemetry.py with MORE lines so its window geometry changes.
    target = mini_repo / "pkg" / "telemetry.py"
    target.write_text(
        "# Telemetry emitter: ships metrics over UDP.\n" * 11, encoding="utf-8"
    )
    new_chunks = chunk_file(mini_repo, "pkg/telemetry.py", window=6, overlap=2)

    job2 = IndexJob(id="j2", project_id=PROJECT, repo_path=str(mini_repo))
    await svc.reindex(job2, mini_repo)

    assert job2.files_skipped == 4  # everything else proven unchanged by hash
    assert job2.files_indexed == 1
    assert svc.embed_calls - before == len(new_chunks)
    # Stale windows pruned; table holds exactly the fresh geometry.
    assert await _row_count(db, "path = 'pkg/telemetry.py'") == len(new_chunks)


async def test_deleted_file_is_pruned_from_the_index(db, mini_repo):
    svc = await _service(db)
    await svc.reindex(IndexJob(id="j1", project_id=PROJECT, repo_path=str(mini_repo)), mini_repo)
    assert await _row_count(db, "path = 'schema.sql'") > 0

    (mini_repo / "schema.sql").unlink()
    job2 = IndexJob(id="j2", project_id=PROJECT, repo_path=str(mini_repo))
    await svc.reindex(job2, mini_repo)

    assert job2.files_pruned >= 1
    assert await _row_count(db, "path = 'schema.sql'") == 0
    assert job2.files_skipped == 4


async def test_projects_are_isolated(db, mini_repo):
    svc = await _service(db)
    await svc.reindex(IndexJob(id="j1", project_id="p-a", repo_path=str(mini_repo)), mini_repo)
    await svc.reindex(IndexJob(id="j2", project_id="p-b", repo_path=str(mini_repo)), mini_repo)
    a = await _row_count(db, "project_id = 'p-a'")
    b = await _row_count(db, "project_id = 'p-b'")
    assert a == b and a > 0
