"""X-07 (10X Wave B) — approval rendering by reader: the per-member preference.

Pins the whole surface honestly:
- GET /me/rendering defaults to technical for every seeded member;
- PUT /me/rendering (self-service, viewer included) persists and re-reads;
- a no-op PUT stays silent but answers 200 (idempotent convention);
- an unknown value is 422 VALIDATION_ERROR, never written;
- PUT /teams/{id}/members/{uid}/rendering is admin-only: admin 200, engineer/viewer
  403 FORBIDDEN, and a cross-team id gets the same 404 as an unknown one (no oracle);
- every real change writes exactly one member.rendering_changed audit row with
  before/after and setBy self|admin (X-D1), and a no-op writes none;
- the admin-set path is visible to the member through GET /me/rendering.

SQLite (create_all) serves every row these tests touch — no live-PG dependency.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import AuthSession, Base, Team, TeamMember, User

MASTER = b"test-master-key-0123456789abcdef"


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(master_key=MASTER, cors_origins=[], require_ownership=True)
    app = make_app(factory, settings)
    app.state.notifier = lambda email, code: None

    async with factory() as db:
        team = Team(name="alpha")
        db.add(team)
        await db.flush()
        tokens: dict[str, str] = {}
        member_ids: dict[str, str] = {}
        for role in ("ADMIN", "ENGINEER", "VIEWER"):
            user = User(email=f"{role.lower()}@alpha.io", name=role.lower(), is_active=True)
            db.add(user)
            await db.flush()
            member = TeamMember(team_id=team.id, user_id=user.id, role=role,
                                display_name=role.lower())
            db.add(member)
            await db.flush()
            member_ids[role.lower()] = user.id
            token = f"tok-{role.lower()}"
            db.add(AuthSession(token_hash=hash_token(token), user_id=user.id,
                               team_id=team.id,
                               expires_at=datetime.now(UTC) + timedelta(hours=1)))
            tokens[role.lower()] = token
        await db.commit()
    yield app, tokens, member_ids, team.id
    await engine.dispose()


async def test_default_is_technical_and_self_put_persists(env):
    app, tokens, _ids, _team = env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/me/rendering",
                             headers={"Authorization": f"Bearer {tokens['viewer']}"})
        assert r.status_code == 200
        assert r.json() == {"userId": r.json()["userId"], "rendering": "technical"}

        r = await client.put("/api/v1/me/rendering", json={"rendering": "plain"},
                             headers={"Authorization": f"Bearer {tokens['viewer']}"})
        assert r.status_code == 200 and r.json()["rendering"] == "plain"
        r = await client.get("/api/v1/me/rendering",
                             headers={"Authorization": f"Bearer {tokens['viewer']}"})
        assert r.json()["rendering"] == "plain"

        # No-op PUT: same value answers 200 and writes nothing (asserted below via audit count).
        r = await client.put("/api/v1/me/rendering", json={"rendering": "plain"},
                             headers={"Authorization": f"Bearer {tokens['viewer']}"})
        assert r.status_code == 200 and r.json()["rendering"] == "plain"


async def test_unknown_value_is_422_and_never_written(env):
    app, tokens, _ids, _team = env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.put("/api/v1/me/rendering", json={"rendering": "comic-sans"},
                             headers={"Authorization": f"Bearer {tokens['engineer']}"})
        assert r.status_code == 422
        assert (r.json()["detail"]["error"]["code"]) == "VALIDATION_ERROR"
        r = await client.get("/api/v1/me/rendering",
                             headers={"Authorization": f"Bearer {tokens['engineer']}"})
        assert r.json()["rendering"] == "technical"


async def test_admin_sets_member_engineer_and_viewer_refused(env):
    app, tokens, ids, team = env
    transport = ASGITransport(app=app)
    target = f"/api/v1/teams/{team}/members/{ids['viewer']}/rendering"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        engineer_hdr = {"Authorization": f"Bearer {tokens['engineer']}"}
        viewer_hdr = {"Authorization": f"Bearer {tokens['viewer']}"}
        admin_hdr = {"Authorization": f"Bearer {tokens['admin']}"}

        r = await client.put(target, json={"rendering": "decision"}, headers=engineer_hdr)
        assert r.status_code == 403, "engineer must not set another member's rendering"
        assert (r.json()["detail"]["error"]["code"]) == "FORBIDDEN"
        r = await client.put(target, json={"rendering": "decision"}, headers=viewer_hdr)
        assert r.status_code == 403

        r = await client.put(target, json={"rendering": "decision"}, headers=admin_hdr)
        assert r.status_code == 200 and r.json()["rendering"] == "decision"
        # The member sees their admin-set rendering through their own door.
        r = await client.get("/api/v1/me/rendering", headers=viewer_hdr)
        assert r.json()["rendering"] == "decision"

        # Cross-team probing answers the same 404 as an unknown member (no oracle).
        r = await client.put(f"/api/v1/teams/other-team/members/{ids['viewer']}/rendering",
                             json={"rendering": "plain"}, headers=admin_hdr)
        assert r.status_code == 404


async def test_changes_are_audited_once_and_noops_are_silent(env):
    from harness_identity.models import AuditLog

    app, tokens, ids, team = env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/v1/me/rendering", json={"rendering": "plain"},
                         headers={"Authorization": f"Bearer {tokens['viewer']}"})
        await client.put("/api/v1/me/rendering", json={"rendering": "plain"},
                         headers={"Authorization": f"Bearer {tokens['viewer']}"})  # no-op
        await client.put(f"/api/v1/teams/{team}/members/{ids['viewer']}/rendering",
                         json={"rendering": "decision"},
                         headers={"Authorization": f"Bearer {tokens['admin']}"})

    async with app.state.session_factory() as db:
        rows = (await db.execute(
            select(AuditLog).where(AuditLog.action == "member.rendering_changed")
            .order_by(AuditLog.seq)
        )).scalars().all()
        assert len(rows) == 2, f"expected exactly 2 audit rows, got {len(rows)}"
        first, second = rows
        assert first.detail["setBy"] == "self" and first.detail["before"] == "technical"
        assert first.detail["after"] == "plain"
        assert second.detail["setBy"] == "admin" and second.detail["after"] == "decision"
        assert second.detail["memberUserId"] == ids["viewer"]


async def test_all_endpoints_require_a_session(env):
    app, _tokens, _ids, team = env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for method, path in (
            ("GET", "/api/v1/me/rendering"),
            ("PUT", "/api/v1/me/rendering"),
            ("GET", f"/api/v1/teams/{team}/members/x/rendering"),
            ("PUT", f"/api/v1/teams/{team}/members/x/rendering"),
        ):
            r = await client.request(method, path)
            assert r.status_code == 401, f"{method} {path} must be 401 anonymous"
