"""Session 8: an operator run-retention hold excludes all Section 4 purge work."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import retention


def test_held_run_is_excluded_from_section4_retention_purge(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/holds.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    workspace, sessions = tmp_path / "workspace", tmp_path / "sessions"
    now = datetime(2026, 10, 19, tzinfo=UTC)
    old = now - timedelta(days=45)
    run_id = "held-run"
    for path in (
        workspace / ".baseline" / run_id,
        workspace / ".staged" / run_id,
        workspace / run_id,
        sessions / run_id,
    ):
        path.mkdir(parents=True)
        (path / "evidence.txt").write_text("retain", encoding="utf-8")

    async def seed():
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
                " state VARCHAR(40) NOT NULL, updated_at TIMESTAMP)"
            ))
            await conn.execute(text(
                "CREATE TABLE run_retention_holds (run_id VARCHAR(36) PRIMARY KEY,"
                " held_by VARCHAR(36), reason VARCHAR(500), created_at TIMESTAMP)"
            ))
            await conn.execute(text(
                "CREATE TABLE high_impact_approvals (id VARCHAR(36) PRIMARY KEY,"
                " run_id VARCHAR(36), state VARCHAR(16), diff TEXT, created_at TIMESTAMP)"
            ))
            await conn.execute(text(
                "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " run_id VARCHAR(36), kind VARCHAR(40), ts TIMESTAMP)"
            ))
            await conn.execute(text(
                "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT,"
                " target_id TEXT, ts TIMESTAMP)"
            ))
            await conn.execute(text(
                "CREATE TABLE agent_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " run_id TEXT, type TEXT, ts TIMESTAMP)"
            ))
            await conn.execute(text(
                "INSERT INTO agent_runs (id, state, updated_at) VALUES (:r, 'idle', :t)"
            ), {"r": run_id, "t": old})
            await conn.execute(text(
                "INSERT INTO run_retention_holds (run_id, held_by, reason, created_at)"
                " VALUES (:r, 'admin-1', 'preserve for investigation', :t)"
            ), {"r": run_id, "t": old})
            await conn.execute(text(
                "INSERT INTO agent_events (run_id, type, ts)"
                " VALUES (:r, 'engine_completed', :t)"
            ), {"r": run_id, "t": old})

    async def purge():
        try:
            await seed()
            return await retention.purge(
                factory, str(workspace), str(sessions), days=30,
                apply=True, include_workspace=True, now=now,
            )
        finally:
            await engine.dispose()

    result = asyncio.run(purge())

    assert result["runs"] == []
    assert all((workspace / p / run_id).is_dir() for p in (".baseline", ".staged", "."))
    assert (sessions / run_id).is_dir()
