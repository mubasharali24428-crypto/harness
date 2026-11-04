"""Session 6 PostgreSQL parity probes; opt in with HARNESS_TEST_PG=1.

Creates a disposable database and applies the real migration chain. These exercise the
PostgreSQL unique-index race, artifact store, live search reauthorization, export serialization,
and retry request-id lookup. The local PG runner owns the only invocation of this module.
"""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from harness_contracts.roles import Principal, Role
from sqlalchemy import text

from harness_orchestrator import artifacts, execution, exports, plans, search
from harness_orchestrator.service import OrchestratorService

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; Session 6 PG probe skipped",
)
_spec = importlib.util.spec_from_file_location(
    "harness_migrate_s6", ROOT / "deploy/runners/migrate.py"
)
assert _spec is not None and _spec.loader is not None
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


def server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


class Bus:
    async def publish(self, *args, **kwargs):
        return None

    async def ensure_run(self, *args, **kwargs):
        return None


@pytest.fixture
async def pg(tmp_path, monkeypatch):
    import asyncpg

    name = f"harness_s6_{uuid.uuid4().hex[:10]}"
    base = server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        for path in sorted((ROOT / "section1/migrations").glob("*.sql")):
            for stmt in migrate.split_statements(path.read_text(encoding="utf-8")):
                if stmt.strip():
                    await conn.execute(stmt)
        # migrate() rolls the partition window forward; this hand-split loop
        # does not, so replicate that post-migration step or current-month
        # agent_events inserts fail.
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        _engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
        try:
            await migrate.ensure_forward_partitions(
                async_sessionmaker(_engine, expire_on_commit=False)
            )
        finally:
            await _engine.dispose()
        await conn.execute("INSERT INTO teams (id, name) VALUES ('t1', 's6')")
        await conn.execute(
            "INSERT INTO users (id, email, name, is_active) "
            "VALUES ('u1', 's6@example.test', 'S6', true)"
        )
        await conn.execute(
            "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
            " VALUES ('m1', 't1', 'u1', 'S6', 'human', 'admin')"
        )
        await conn.execute("INSERT INTO projects (id, team_id, name) VALUES ('p1', 't1', 's6')")
        await conn.execute(
            "INSERT INTO agent_runs (id, project_id, state) VALUES ('r1', 'p1', 'idle')"
        )
        await conn.execute(
            "INSERT INTO agent_events (project_id, run_id, type, payload, ts)"
            " VALUES ('p1', 'r1', 'execution_started', '{}', now())"
        )
        await conn.execute(
            "INSERT INTO comments (id, scope_kind, scope_id, team_id, author_user_id,"
            " author_name_snapshot, body, state, created_at)"
            " VALUES ('c1', 'run', 'r1', 't1', 'u1', 'S6', 'needle phrase in a comment',"
            " 'visible', now())"
        )
        await conn.execute(
            "INSERT INTO run_attempts (id, run_id, team_id, attempt_no, kind, state, request_id,"
            " requested_by, requested_at) VALUES ('a1', 'r1', 't1', 1, 'retry', 'start_failed',"
            " 'retry-idempotent-1', 'u1', now())"
        )
    finally:
        await conn.close()
    engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("HARNESS_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("HARNESS_EXPORT_DIR", str(tmp_path / "exports"))
    yield factory
    await engine.dispose()
    admin = await asyncpg.connect(server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def test_session6_postgres_races_artifacts_search_export_and_retry(pg):
    p = Principal(userId="u1", teamId="t1", role=Role.ADMIN)
    body = plans.VersionBody(content={"goal": "first proposal"}, expectedVersion=0)

    async def create_version():
        async with pg() as db:
            return await plans.create_version(db, p, "r1", body)

    results = await asyncio.gather(
        create_version(),
        create_version(),
        return_exceptions=True,
    )
    # The request is not idempotent; exactly one writer owns version 1.
    assert sum(not isinstance(r, Exception) for r in results) == 1, results
    assert sum(isinstance(r, plans.PlansError) and r.status == 409 for r in results) == 1, results

    stored, replayed = await artifacts.create_artifact(
        pg,
        p,
        "r1",
        name="notes.txt",
        kind="upload",
        data=b"reviewable note",
        declared="text/plain",
        request_id="upload-session6-1",
    )
    same, replay = await artifacts.create_artifact(
        pg,
        p,
        "r1",
        name="notes.txt",
        kind="upload",
        data=b"reviewable note",
        declared="text/plain",
        request_id="upload-session6-1",
    )
    assert stored["id"] == same["id"] and not replayed and replay

    await search.sync_index(pg, "t1")
    before = await search.search(pg, p, q="needle phrase")
    assert any(hit["kind"] == "comment" for hit in before["results"]), before
    async with pg() as db:
        await db.execute(text("UPDATE comments SET state = 'hidden' WHERE id = 'c1'"))
        await db.commit()
    after = await search.search(pg, p, q="needle phrase")
    assert not any(hit.get("sourceId") == "c1" for hit in after["results"]), after

    report, created = await exports.create_export(
        pg,
        p,
        exports.ExportBody(
            kind="run_history", scopeId="r1", format="ndjson", requestId="export-session6-1"
        ),
    )
    assert created and report["state"] == "completed" and report["format"] == "ndjson", report

    service = OrchestratorService(session_factory=pg, bus=Bus())
    # retry() returns (body, created): a repeated request id replays the stored attempt (HTTP 200)
    retry, created = await execution.retry(
        pg, service, p, "r1", execution.RetryBody(requestId="retry-idempotent-1")
    )
    assert not created and retry["duplicate"] is True and retry["attempt"]["attemptNo"] == 1, retry
