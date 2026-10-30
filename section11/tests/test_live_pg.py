"""Live-PG proof: full migrate() walk (0001→0030) on a throwaway pgvector
container, then a REAL vector round-trip + frozen search against the actual
vector(256) column and HNSW index.

Skipped cleanly when Docker or the pgvector/pgvector:pg18 image is absent —
the SQLite suite above carries the portable truth; this module is the
fresh-install law probe (duplicate-prefix crashes are invisible to SQLite
fixtures, per W5 close-out lessons).
"""

import asyncio
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parents[2]
IMAGE = "pgvector/pgvector:pg18"
PG_PORT = 5591
URL = f"postgresql+asyncpg://harness:harness@localhost:{PG_PORT}/harness"


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True, timeout=30
    ).returncode == 0


def _image_ok() -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True, timeout=30
        ).returncode
        == 0
    )


@pytest.fixture(scope="module")
def pg_url():
    if not (_docker_ok() and _image_ok()):
        pytest.skip("live-PG probe needs docker daemon + pgvector/pgvector:pg18 image")
    name = f"harness-m21x1-probe-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "-e", "POSTGRES_USER=harness",
            "-e", "POSTGRES_PASSWORD=harness",
            "-e", "POSTGRES_DB=harness",
            "-p", f"127.0.0.1:{PG_PORT}:5432",
            IMAGE,
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    try:
        yield URL
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)


async def _wait_ready(engine, timeout_s: float = 90.0) -> None:
    from sqlalchemy import text

    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception:
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(1.0)


async def test_full_migration_walk_then_real_vector_search(pg_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    sys.path.insert(0, str(HARNESS_ROOT / "deploy" / "runners"))
    import migrate as migrate_runner

    engine = create_async_engine(pg_url)
    try:
        await _wait_ready(engine)

        # THE LAW PROBE: apply every pending migration exactly like deploys do.
        applied = await migrate_runner.migrate(engine)
        assert "0030_pgvector.sql" in applied
        # Idempotent second boot applies nothing new.
        assert await migrate_runner.migrate(engine) == []

        from sqlalchemy import text

        async with engine.connect() as conn:
            version = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM schema_migrations WHERE version = '0030'")
                )
            ).scalar_one()
        assert int(version) == 1

        # Real vector column round-trip through OUR upsert path.
        from harness_context.chunking import chunk_text
        from harness_context.embeddings import HashingEmbedding
        from harness_context.service import ContextService
        from harness_context.store import ContextStore

        backend = HashingEmbedding(dims=256)
        chunks = chunk_text(
            "pkg/widget_store.py",
            '"""Widget storage with a hard per-team quota."""\n'
            "class WidgetStore:\n"
            "    def put(self, widget_id, payload):\n"
            "        if len(self.widgets) >= self.quota:\n"
            "            return False\n",
        )
        vectors = await backend.embed([c.content for c in chunks])
        store = ContextStore(dialect="postgresql")
        async with engine.begin() as conn:
            for chunk, vec in zip(chunks, vectors, strict=True):
                await store.upsert_chunk(conn, project_id="p-live", chunk=chunk, embedding=vec)
            rows = await store.fetch_vectors(conn, "p-live")
        assert len(rows) == len(chunks)
        assert all(len(r["vec"]) == 256 for r in rows)

        # Frozen search contract against the REAL table + ANN index.
        factory = async_sessionmaker(engine, expire_on_commit=False)
        svc = ContextService(factory, backend)
        async with factory() as session:
            hits = await svc.search(await session.connection(), "widget quota", "p-live", top_k=3)
        assert hits, "live search returned nothing"
        assert hits[0].path == "pkg/widget_store.py"
        # Same embedding space both sides -> the exact-content chunk ranks first
        # with a healthy cosine (not 1.0: the chunk carries extra vocabulary).
        assert hits[0].score > 0.4
    finally:
        await engine.dispose()


async def test_reader_during_index_does_not_rollback_writer(pg_url, mini_repo):
    """A separate Postgres session reads while an index transaction is open."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from harness_context.embeddings import HashingEmbedding
    from harness_context.service import ContextService, IndexJob

    engine = create_async_engine(pg_url)
    try:
        await _wait_ready(engine)
        sys.path.insert(0, str(HARNESS_ROOT / "deploy" / "runners"))
        import migrate as migrate_runner

        await migrate_runner.migrate(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        svc = ContextService(factory, HashingEmbedding(dims=256))
        original = svc.store.upsert_chunk
        pids = []

        async def upsert_with_reader(session, **kwargs):
            await original(session, **kwargs)
            if not pids:
                writer_pid = await session.scalar(text("SELECT pg_backend_pid()"))
                async with factory() as reader:
                    reader_pid = await reader.scalar(text("SELECT pg_backend_pid()"))
                    await reader.scalar(text("SELECT COUNT(*) FROM context_chunks"))
                pids.extend([writer_pid, reader_pid])

        svc.store.upsert_chunk = upsert_with_reader
        job = IndexJob(id="w19-pg", project_id="w19-pg", repo_path=str(mini_repo))
        await svc.reindex(job, mini_repo)
        async with factory() as reader:
            count = await reader.scalar(
                text("SELECT COUNT(*) FROM context_chunks WHERE project_id = :project"),
                {"project": job.project_id},
            )
        assert len(pids) == 2 and pids[0] != pids[1]
        assert count == job.chunks_indexed and count > 0
    finally:
        await engine.dispose()
