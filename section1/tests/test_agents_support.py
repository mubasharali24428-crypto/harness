"""Shared helpers for the agent registry suite (lane A). No tests live here."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.agents import router
from harness_identity.models import Base
from tests.test_workspaces_support import audit_actions, bearer, seed_team

__all__ = ["audit_actions", "bearer", "seed_team", "make_env", "body", "register"]

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0044_agents.sql"
A = "/api/v1/agents"


def migration_statements() -> list[str]:
    sql = "\n".join(ln for ln in MIGRATION.read_text().splitlines()
                    if not ln.strip().startswith("--"))
    return [s.strip() for s in sql.split(";") if s.strip()]


@asynccontextmanager
async def make_env():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in migration_statements():
            await conn.execute(text(stmt))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory
    await engine.dispose()


def body(**over) -> dict:
    return {"name": "Coder", "description": "writes code", "capabilities": ["code.python"],
            "toolPermissions": ["read_file", "write_file", "search"],
            "permissions": ["read", "comment"], "allowedPaths": ["src"],
            "networkAllow": ["api.example.com"], "credentialRefs": ["GITHUB_READ"],
            "model": "deepseek-chat", "fallbackModel": "deepseek-lite",
            "toolFallbacks": {"search": "read_file"}, "maxDepth": 2, "maxChildren": 3,
            "maxBudgetUsd": "2.5", **over}


async def register(client, actor: dict, **over) -> dict:
    r = await client.post(A, json=body(**over), headers=bearer(actor))
    assert r.status_code == 201, r.text
    return r.json()
