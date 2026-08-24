"""M16-X5 — shared fixtures for the edge-case corpus (raw-18 seeds, wave A).

conftest.py is FROZEN this mission, so the corpus carries its own scratch
fixtures here. Every builder of an S1 edge-case suite mounts the router under
test on a bare FastAPI app exactly like the wave-9 invite pattern: sessions
are seeded straight into the store with contract-shaped token hashes so no
login route is needed; everything runs offline on aiosqlite.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.config import Settings

MASTER = b"test-master-key-0123456789abcdef"


def make_settings(**overrides: Any) -> Settings:
    return Settings(master_key=MASTER, cors_origins=[], **overrides)


def make_scratch_app(router) -> FastAPI:
    """Bare app exposing ONLY ``router`` (app.py registration is ATLAS's)."""
    app = FastAPI()
    app.include_router(router)
    return app


async def seed_user(
    factory: async_sessionmaker,
    email: str,
    *,
    role: str = "admin",
    team_id: str | None = None,
    team_name: str | None = None,
) -> dict:
    """Create team/user/membership/session rows directly; return ids+bearer."""
    import secrets

    async with factory() as db:
        if team_id is None:
            team = Team(name=team_name or f"team-{email}")
            db.add(team)
            await db.flush()
            team_id = team.id
        else:
            team = await db.get(Team, team_id)
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=user.id, display_name=user.name, role=role))
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team.id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    return {"teamId": team.id, "userId": user.id, "token": token}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def x5_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    from harness_identity.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def make_client(app: FastAPI):
    class _Ctx:
        async def __aenter__(self):
            transport = ASGITransport(app=app)
            self._client = AsyncClient(transport=transport, base_url="http://test")
            await self._client.__aenter__()
            return self._client

        async def __aexit__(self, *exc):
            return await self._client.__aexit__(*exc)

    return _Ctx()


# Late imports kept at the bottom so collection order never matters.
from harness_identity.models import AuthSession, Team, TeamMember, User  # noqa: E402

__all__ = [
    "MASTER",
    "bearer",
    "make_client",
    "make_scratch_app",
    "make_settings",
    "seed_user",
    "x5_factory",
]
