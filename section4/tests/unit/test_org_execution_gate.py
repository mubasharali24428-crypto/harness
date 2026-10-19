"""Organization execution stop is checked against the durable team/org link."""

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.org_execution_gate import (
    CODE,
    enforce_organization_execution,
    organization_execution_state,
)


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for ddl in (
            "CREATE TABLE organizations (id TEXT PRIMARY KEY, execution_disabled BOOLEAN NOT NULL)",
            "CREATE TABLE teams (id TEXT PRIMARY KEY, org_id TEXT)",
            "CREATE TABLE projects (id TEXT PRIMARY KEY, team_id TEXT)",
            "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, actor_member_id TEXT, "
            "actor_kind TEXT, action TEXT, target_type TEXT, target_id TEXT, detail TEXT, ts TEXT)",
        ):
            await conn.execute(text(ddl))
        await conn.execute(text("INSERT INTO organizations VALUES ('o1', TRUE)"))
        await conn.execute(text("INSERT INTO organizations VALUES ('o2', FALSE)"))
        await conn.execute(text("INSERT INTO teams VALUES ('t1', 'o1')"))
        await conn.execute(text("INSERT INTO teams VALUES ('t2', 'o2')"))
        await conn.execute(text("INSERT INTO teams VALUES ('t3', NULL)"))
        await conn.execute(text("INSERT INTO projects VALUES ('p1', 't1')"))
        await conn.execute(text("INSERT INTO projects VALUES ('p2', 't2')"))
        await conn.execute(text("INSERT INTO projects VALUES ('p3', 't3')"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_disabled_org_blocks_and_unlinked_or_enabled_org_passes(factory):
    assert await organization_execution_state(factory, "p1") == ("o1", True)
    assert await organization_execution_state(factory, "p2") is None
    assert await organization_execution_state(factory, "p3") is None
    with pytest.raises(HTTPException) as caught:
        await enforce_organization_execution(
            type("Service", (), {"session_factory": factory})(), "r1", "p1"
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["error"]["code"] == CODE


@pytest.mark.asyncio
async def test_linked_org_missing_control_fails_closed(factory):
    async with factory() as db:
        await db.execute(text("ALTER TABLE organizations DROP COLUMN execution_disabled"))
        await db.commit()
    with pytest.raises(HTTPException) as caught:
        await organization_execution_state(factory, "p1")
    assert caught.value.status_code == 503
    assert caught.value.detail["error"]["code"] == "ORGANIZATION_CONTROL_UNAVAILABLE"
