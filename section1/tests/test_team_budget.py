"""W15 slice 2 — per-team budgets on the spend surfaces.

Per-IP limits cannot distinguish one team's scripted traffic from its NAT
neighbours, and the limiter middleware runs before authentication so it cannot
know the team. The team budget is therefore consumed where the team is known,
against the same ``rate_buckets`` store, under a namespaced key.
"""

import pytest
from harness_contracts.ratelimit import (
    MODEL_CALL_TEMPLATE,
    TEAM_BUCKET_PREFIX,
    consume_team_budget,
    team_bucket_key,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

RATE_BUCKETS_DDL = (
    "CREATE TABLE rate_buckets (bucket_key VARCHAR(200) PRIMARY KEY, "
    "window_start TIMESTAMPTZ NOT NULL, count INTEGER NOT NULL DEFAULT 0)"
)


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(text(RATE_BUCKETS_DDL))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_team_key_is_namespaced_away_from_ip_buckets():
    key = team_bucket_key("team-a", MODEL_CALL_TEMPLATE)
    assert key.startswith(TEAM_BUCKET_PREFIX)
    assert "team-a" in key
    # An IP bucket for the same route must never collide with a team bucket.
    assert not key.startswith("10.0.0.1|")


@pytest.mark.asyncio
async def test_budget_allows_up_to_the_limit_then_blocks(factory):
    outcomes = [
        await consume_team_budget(factory, "team-a", MODEL_CALL_TEMPLATE, limit=3)
        for _ in range(4)
    ]
    assert [allowed for allowed, _ in outcomes] == [True, True, True, False]
    assert 1 <= outcomes[-1][1] <= 60


@pytest.mark.asyncio
async def test_each_team_has_its_own_budget(factory):
    for _ in range(2):
        assert (await consume_team_budget(factory, "team-a", MODEL_CALL_TEMPLATE, limit=2))[0]
    blocked, _ = await consume_team_budget(factory, "team-a", MODEL_CALL_TEMPLATE, limit=2)
    allowed_b, _ = await consume_team_budget(factory, "team-b", MODEL_CALL_TEMPLATE, limit=2)
    assert blocked is False
    assert allowed_b is True


@pytest.mark.asyncio
async def test_store_failure_fails_open():
    class Broken:
        def __call__(self):
            raise RuntimeError("store down")

    allowed, retry_after = await consume_team_budget(
        Broken(), "team-a", MODEL_CALL_TEMPLATE, limit=1
    )
    assert (allowed, retry_after) == (True, 0)
