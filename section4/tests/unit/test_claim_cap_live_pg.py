"""Per-team running cap must hold under CONCURRENT claimers on real PG
(opt-in: HARNESS_TEST_PG). Without the advisory lock in claim_next_run the
COUNT(*) guard overshoots under READ COMMITTED (cap=4 -> 12-16 running)."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.run_queue import claim_next_run

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG claim-cap test skipped",
)


def _dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else (os.environ.get("HARNESS_TEST_PG_DSN") or "postgresql://harness:harness@127.0.0.1:5499/harness")


async def test_team_cap_holds_under_concurrent_claimers():
    import asyncpg

    dbname = f"harness_cap_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_dsn())
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    url = _dsn().replace("postgresql://", "postgresql+asyncpg://").rsplit("/", 1)[0] + f"/{dbname}"
    engine = create_async_engine(url, pool_size=20)
    try:
        async with engine.begin() as c:
            await c.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY, team_id TEXT)"))
            await c.execute(
                text(
                    "CREATE TABLE run_claims (run_id TEXT PRIMARY KEY, project_id TEXT,"
                    " state TEXT, claimed_at TEXT, heartbeat_at TEXT, worker_id TEXT)"
                )
            )
            await c.execute(text("INSERT INTO projects VALUES ('p1','t1')"))
            for i in range(20):
                await c.execute(
                    text("INSERT INTO run_claims VALUES (:r,'p1','queued',:t,:t,'enq')"),
                    {"r": f"r{i:02d}", "t": f"2026-10-18T00:00:{i:02d}+00:00"},
                )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        await asyncio.gather(*[claim_next_run(factory, f"w{i}", 4) for i in range(16)])
        async with engine.connect() as c:
            running = (
                await c.execute(text("SELECT count(*) FROM run_claims WHERE state='running'"))
            ).scalar()
        assert running == 4
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{dbname}" WITH (FORCE)')
        await admin.close()


async def test_fair_ordering_holds_on_postgres():
    """W15 slice 1: a long queue in one team must not take the whole pool."""
    import asyncpg

    dbname = f"harness_fair_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_dsn())
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    url = _dsn().replace("postgresql://", "postgresql+asyncpg://").rsplit("/", 1)[0] + f"/{dbname}"
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            await c.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY, team_id TEXT)"))
            await c.execute(
                text(
                    "CREATE TABLE run_claims (run_id TEXT PRIMARY KEY, project_id TEXT,"
                    " state TEXT, claimed_at TEXT, heartbeat_at TEXT, worker_id TEXT)"
                )
            )
            await c.execute(text("INSERT INTO projects VALUES ('p1','t1'), ('p2','t2')"))
            for i in range(5):
                await c.execute(
                    text("INSERT INTO run_claims VALUES (:r,'p1','queued',:t,:t,'enq')"),
                    {"r": f"a{i}", "t": f"2026-10-29T00:00:0{i}+00:00"},
                )
            await c.execute(
                text("INSERT INTO run_claims VALUES ('b1','p2','queued',:t,:t,'enq')"),
                {"t": "2026-10-29T00:00:09+00:00"},
            )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        claimed = [
            (await claim_next_run(factory, "w", max_per_team=4))["run_id"] for _ in range(4)
        ]
        assert claimed == ["a0", "b1", "a1", "a2"], claimed
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{dbname}" WITH (FORCE)')
        await admin.close()
