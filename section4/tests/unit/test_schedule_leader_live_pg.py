"""Parts 48/50 (session 12) on real Postgres: a maintenance tick runs in one replica at a time.

Two "replicas" (two engines, so two connection pools) start the same job at once; the job blocks
until released. Only one runs it, the other skips that tick, and once the leader finishes the lock
is free again. Opt-in (HARNESS_TEST_PG); advisory locks need no schema, so no throwaway database.
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.schedule import leader_only

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG leader election test skipped",
)


def _url() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    dsn = raw if "://" in raw else (os.environ.get("HARNESS_TEST_PG_DSN") or "postgresql://harness:harness@127.0.0.1:5499/harness")
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_only_one_replica_runs_a_tick_and_the_lock_frees_after():
    engines = [create_async_engine(_url()) for _ in range(2)]
    a, b = (async_sessionmaker(e) for e in engines)
    label = f"leader-test-{uuid.uuid4().hex[:8]}"
    release, started, ran = asyncio.Event(), asyncio.Event(), []

    async def job(name):
        ran.append(name)
        started.set()
        await release.wait()
        return name

    try:
        first = asyncio.create_task(leader_only(a, label, lambda: job("a")))
        await asyncio.wait_for(started.wait(), 5)
        assert await leader_only(b, label, lambda: job("b")) is None  # skipped, not queued
        release.set()
        assert await first == "a"
        assert ran == ["a"]
        assert await leader_only(b, label, lambda: job("b")) == "b"  # free again
        assert ran == ["a", "b"]
    finally:
        for e in engines:
            await e.dispose()


async def test_a_failing_tick_releases_the_lock():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine)
    label = f"leader-test-{uuid.uuid4().hex[:8]}"

    async def boom():
        raise RuntimeError("tick failed")

    try:
        with pytest.raises(RuntimeError):
            await leader_only(factory, label, boom)

        async def ok():
            return "ok"

        assert await leader_only(factory, label, ok) == "ok"
    finally:
        await engine.dispose()
