"""Shared helpers for the workspace suites (lane W). No tests live here."""

import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.models import AuditLog, AuthSession, Base, Project, Team, TeamMember, User
from harness_identity.workspaces import router

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0040_workspaces.sql"


def migration_statements() -> list[str]:
    sql = "\n".join(ln for ln in MIGRATION.read_text().splitlines()
                    if not ln.strip().startswith("--"))
    # SQLite has no ALTER ... ADD COLUMN IF NOT EXISTS; the table is fresh in tests.
    sql = re.sub(r"ADD COLUMN IF NOT EXISTS", "ADD COLUMN", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


@asynccontextmanager
async def make_env(max_per_team: int | None = None):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in migration_statements():
            await conn.execute(text(stmt))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    if max_per_team is not None:
        app.state.workspace_max_per_team = max_per_team
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory
    await engine.dispose()


def bearer(actor: dict) -> dict:
    return {"Authorization": f"Bearer {actor['token']}"}


async def seed_team(factory, *members: tuple[str, str], name: str = "t") -> dict:
    """New team; returns {email: {teamId,userId,role,token}} plus 'teamId'."""
    out: dict = {}
    async with factory() as db:
        team = Team(name=name)
        db.add(team)
        await db.flush()
        out["teamId"] = team.id
        for email, role in members:
            user = User(email=email, name=email.split("@")[0])
            db.add(user)
            await db.flush()
            db.add(TeamMember(team_id=team.id, user_id=user.id, display_name=user.name, role=role))
            token = secrets.token_urlsafe(32)
            db.add(AuthSession(token_hash=hash_token(token), user_id=user.id, team_id=team.id,
                               expires_at=datetime.now(UTC) + timedelta(days=1)))
            out[email] = {"teamId": team.id, "userId": user.id, "role": role, "token": token}
        await db.commit()
    return out


async def seed_project(factory, team_id: str, name: str = "proj") -> str:
    async with factory() as db:
        pr = Project(team_id=team_id, name=name, repo_full_name="acme/repo")
        db.add(pr)
        await db.commit()
        return pr.id


async def audit_actions(factory, action: str) -> list[AuditLog]:
    async with factory() as db:
        return list((await db.execute(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.seq))).scalars())


async def create_ws(client, actor: dict, name: str = "Alpha", **kw) -> dict:
    body = {"name": name, "dataResidency": "eu", **kw}
    r = await client.post("/api/v1/workspaces", json=body, headers=bearer(actor))
    assert r.status_code == 201, r.text
    return r.json()
