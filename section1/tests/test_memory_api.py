"""Marathon 2.0 W12 — project memory API tests (Section 1 side).

Self-contained by design (conftest.py is frozen): scratch FastAPI app, seeded
auth sessions, offline aiosqlite — the rules-suite convention.

Coverage: draft create -> admin review -> delete lifecycle with read-back,
authz matrix (engineer authors/deletes; only admins review; viewer/guest
read-only; unauth 401), cross-project 404 no-oracle, the 16KB byte cap with
the exact 413 MEMORY_TOO_LARGE policy code, idempotent delete, and audit rows
for memory.created / memory.reviewed / memory.deleted. The API can only ever
write source=user.
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.memory_api import MAX_MEMORY_BYTES, router
from harness_identity.models import (
    AuditLog,
    AuthSession,
    Base,
    Project,
    Team,
    TeamMember,
    User,
)

# --------------------------------------------------------------- fixtures


@pytest.fixture
async def memory_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def memory_client(memory_factory):
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = memory_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_teams_and_projects(factory) -> dict:
    async with factory() as db:
        team_a = Team(name="memory-team-a")
        team_b = Team(name="memory-team-b")
        db.add_all([team_a, team_b])
        await db.flush()
        project_a = Project(team_id=team_a.id, name="memory-repo-a")
        project_b = Project(team_id=team_b.id, name="memory-repo-b")
        db.add_all([project_a, project_b])
        await db.flush()
        memberships = [
            ("a-admin@x.com", "admin", team_a),
            ("a-eng@x.com", "engineer", team_a),
            ("a-view@x.com", "viewer", team_a),
            ("a-guest@x.com", "guest", team_a),
            ("b-admin@x.com", "admin", team_b),
        ]
        users: dict[str, User] = {}
        tokens: dict[str, str] = {}
        for email, _role, _team in memberships:
            user = User(email=email, name=email.split("@")[0])
            db.add(user)
            users[email] = user
        await db.flush()
        for email, role, team in memberships:
            db.add(
                TeamMember(
                    team_id=team.id,
                    user_id=users[email].id,
                    display_name=users[email].name,
                    role=role,
                )
            )
        for email, _role, team in memberships:
            token = secrets.token_urlsafe(32)
            db.add(
                AuthSession(
                    token_hash=hash_token(token),
                    user_id=users[email].id,
                    team_id=team.id,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            tokens[email] = token
        await db.commit()

    def _actor(email: str) -> dict:
        return {"userId": users[email].id, "token": tokens[email]}

    return {
        "projectA": project_a.id,
        "projectB": project_b.id,
        "admin": _actor("a-admin@x.com"),
        "engineer": _actor("a-eng@x.com"),
        "viewer": _actor("a-view@x.com"),
        "guest": _actor("a-guest@x.com"),
        "foreignAdmin": _actor("b-admin@x.com"),
    }


async def _audit_actions(factory, action: str) -> list[AuditLog]:
    async with factory() as db:
        return list(
            (await db.execute(select(AuditLog).where(AuditLog.action == action)))
            .scalars()
            .all()
        )


# ------------------------------------------------------------------- tests


async def test_lifecycle_draft_review_delete(memory_client, memory_factory):
    env = await _seed_teams_and_projects(memory_factory)
    admin = _bearer(env["admin"]["token"])
    eng = _bearer(env["engineer"]["token"])
    url = f"/api/v1/projects/{env['projectA']}/memory"

    # draft create: source is always user, status always draft
    r = await memory_client.post(
        url, json={"memoryText": "The API prefers camelCase projections."}, headers=eng
    )
    assert r.status_code == 201, r.text
    created = r.json()["entry"]
    assert created["source"] == "user" and created["status"] == "draft"
    assert created["createdBy"] == env["engineer"]["userId"]

    # listed to the team, draft included
    r = await memory_client.get(url, headers=eng)
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert [e["memoryText"] for e in entries] == ["The API prefers camelCase projections."]

    entry_id = created["id"]

    # only an admin reviews
    r = await memory_client.post(f"{url}/{entry_id}/review", headers=eng)
    assert r.status_code == 403
    r = await memory_client.post(f"{url}/{entry_id}/review", headers=admin)
    assert r.status_code == 200, r.text
    reviewed = r.json()["entry"]
    assert reviewed["status"] == "reviewed"
    assert reviewed["reviewedBy"] == env["admin"]["userId"]
    assert reviewed["memoryText"] == "The API prefers camelCase projections."

    # deletion: write roles; idempotent
    r = await memory_client.delete(f"{url}/{entry_id}", headers=eng)
    assert r.status_code == 204
    r = await memory_client.delete(f"{url}/{entry_id}", headers=eng)
    assert r.status_code == 204  # idempotent
    r = await memory_client.get(url, headers=eng)
    assert r.json()["entries"] == []

    assert len(await _audit_actions(memory_factory, "memory.created")) == 1
    assert len(await _audit_actions(memory_factory, "memory.reviewed")) == 1
    assert len(await _audit_actions(memory_factory, "memory.deleted")) == 1


async def test_authz_matrix_and_cross_project_no_oracle(memory_client, memory_factory):
    env = await _seed_teams_and_projects(memory_factory)
    url_a = f"/api/v1/projects/{env['projectA']}/memory"

    # foreign admin probing project A gets the identical 404 as an unknown id
    for path in (url_a, "/api/v1/projects/does-not-exist/memory"):
        r = await memory_client.get(path, headers=_bearer(env["foreignAdmin"]["token"]))
        assert r.status_code == 404
    r = await memory_client.post(url_a, json={"memoryText": "nope"},
                                 headers=_bearer(env["foreignAdmin"]["token"]))
    assert r.status_code == 404

    # viewer/guest read their own project but cannot mutate
    for who in ("viewer", "guest"):
        hdrs = _bearer(env[who]["token"])
        assert (await memory_client.get(url_a, headers=hdrs)).status_code == 200
        assert (await memory_client.post(url_a, json={"memoryText": "nope"},
                                         headers=hdrs)).status_code == 403

    # unauthenticated never reads
    assert (await memory_client.get(url_a)).status_code == 401

    # project B stays empty (no cross-project bleed through the API)
    url_b = f"/api/v1/projects/{env['projectB']}/memory"
    r = await memory_client.get(url_b, headers=_bearer(env["foreignAdmin"]["token"]))
    assert r.status_code == 200
    assert r.json()["entries"] == []


async def test_over_cap_rejected_413_and_blank_refused(memory_client, memory_factory):
    env = await _seed_teams_and_projects(memory_factory)
    hdrs = _bearer(env["admin"]["token"])
    url = f"/api/v1/projects/{env['projectA']}/memory"

    exactly_cap = "a" * MAX_MEMORY_BYTES  # ASCII: chars == bytes == cap: allowed
    r = await memory_client.post(url, json={"memoryText": exactly_cap}, headers=hdrs)
    assert r.status_code == 201, r.text

    r = await memory_client.post(url, json={"memoryText": "a" * (MAX_MEMORY_BYTES + 1)},
                                 headers=hdrs)
    assert r.status_code == 413, r.text[:200]
    assert r.json()["detail"]["error"]["code"] == "MEMORY_TOO_LARGE"

    # byte-count authority: ~5.5k CJK chars ≈ 16KB+ utf-8 bytes
    cjk = "技" * (MAX_MEMORY_BYTES // 3 + 2)
    r = await memory_client.post(url, json={"memoryText": cjk}, headers=hdrs)
    assert r.status_code == 413

    r = await memory_client.post(url, json={"memoryText": "   "}, headers=hdrs)
    assert r.status_code == 422


async def test_expiry_round_trips(memory_client, memory_factory):
    env = await _seed_teams_and_projects(memory_factory)
    admin = _bearer(env["admin"]["token"])
    url = f"/api/v1/projects/{env['projectA']}/memory"
    expiry = (datetime.now(UTC) + timedelta(days=7)).isoformat()

    r = await memory_client.post(
        url, json={"memoryText": "Expires next week.", "expiresAt": expiry}, headers=admin
    )
    assert r.status_code == 201, r.text
    assert r.json()["entry"]["expiresAt"] is not None
    entry = r.json()["entry"]
    await memory_client.post(f"{url}/{entry['id']}/review", headers=admin)
    listed = (await memory_client.get(url, headers=admin)).json()["entries"]
    assert listed[0]["expiresAt"] is not None
