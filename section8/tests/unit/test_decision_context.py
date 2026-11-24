"""X-07 blind-approval denominator: the rendering a reviewer worked from, fail-open to unknown."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_review.decision_context import reviewer_rendering


async def _factory(tmp_path, ddl: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d.db")
    async with engine.begin() as c:
        await c.execute(text(ddl))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_reads_member_preference(tmp_path):
    engine, f = await _factory(tmp_path, "CREATE TABLE team_members (team_id TEXT,"
                               " user_id TEXT, approval_rendering TEXT)")
    async with f() as db:
        await db.execute(text("INSERT INTO team_members VALUES ('t', 'u', 'plain'),"
                              " ('t', 'odd', 'shouty')"))
        await db.commit()
        assert await reviewer_rendering(db, "t", "u") == "plain"
        assert await reviewer_rendering(db, "t", "odd") == "unknown"  # out-of-set value
        assert await reviewer_rendering(db, "t", "nobody") == "unknown"
        assert await reviewer_rendering(db, None, "u") == "unknown"
    await engine.dispose()


@pytest.mark.asyncio
async def test_pre_0077_schema_is_unknown_not_500(tmp_path):
    engine, f = await _factory(tmp_path, "CREATE TABLE team_members (team_id TEXT, user_id TEXT)")
    async with f() as db:
        assert await reviewer_rendering(db, "t", "u") == "unknown"
    await engine.dispose()
