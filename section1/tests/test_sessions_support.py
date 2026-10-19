"""Shared helpers for the session suites (Session 4 lane S). No tests live here."""

import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.models import AgentEvent, AgentRun, Base
from harness_identity.sessions import router
from tests.test_workspaces_support import (  # noqa: F401 — re-exported for the suites
    audit_actions,
    bearer,
    seed_project,
    seed_team,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
S = "/api/v1/sessions"
RUN_CLAIMS = ("CREATE TABLE run_claims (run_id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
              " state VARCHAR(20), claimed_at TEXT, heartbeat_at TEXT, worker_id VARCHAR(80))")


def migration_statements(name: str) -> list[str]:
    sql = "\n".join(ln for ln in (MIGRATIONS / name).read_text().splitlines()
                    if not ln.strip().startswith("--"))
    sql = re.sub(r"ADD COLUMN IF NOT EXISTS", "ADD COLUMN", sql)  # sqlite has no IF NOT EXISTS
    return [s.strip() for s in sql.split(";") if s.strip()]


@asynccontextmanager
async def make_env(db_file: str | None = None):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}" if db_file
                                 else "sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(RUN_CLAIMS))
        for mig in ("0040_workspaces.sql", "0041_sessions.sql"):
            for stmt in migration_statements(mig):
                await conn.execute(text(stmt))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory
    await engine.dispose()


def err(r) -> str:
    return r.json()["detail"]["error"]["code"]


async def create_session(client, actor: dict, title: str = "Release train", **kw) -> dict:
    r = await client.post(S, json={"title": title, **kw}, headers=bearer(actor))
    assert r.status_code in (200, 201), r.text
    return r.json()


async def add_member(client, actor: dict, sid: str, user: dict, role: str = "participant"):
    r = await client.post(f"{S}/{sid}/members", json={"userId": user["userId"], "role": role},
                          headers=bearer(actor))
    assert r.status_code in (200, 201), r.text
    return r.json()


async def seed_run(factory, project_id: str, events: tuple[str, ...] = ()) -> str:
    async with factory() as db:
        run = AgentRun(project_id=project_id)
        db.add(run)
        await db.flush()
        for t in events:
            db.add(AgentEvent(project_id=project_id, run_id=run.id, type=t, payload={}))
        await db.commit()
        return run.id


async def add_events(factory, project_id: str, run_id: str, *types: str) -> None:
    async with factory() as db:
        for t in types:
            db.add(AgentEvent(project_id=project_id, run_id=run_id, type=t, payload={}))
        await db.commit()


async def drop_team_membership(factory, team_id: str, user_id: str) -> None:
    async with factory() as db:
        await db.execute(text("DELETE FROM team_members WHERE team_id=:t AND user_id=:u"),
                         {"t": team_id, "u": user_id})
        await db.commit()


DONE = ("plan_proposed", "plan_approved", "execution_started", "engine_completed",
        "done_signal_received", "review_passed")
