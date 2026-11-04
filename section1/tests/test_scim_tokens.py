"""W16 slice 3 — SCIM bearer tokens (issue, list, revoke) and the door they open.

A SCIM token is a MACHINE credential: minted by a team admin, shown once, stored
only as a SHA-256 hash, revocable, and it authorises the `/scim/v2` surface and
nothing else. It is never a user session token, and a user session token never
opens the SCIM door.

Runner conventions are reused deliberately (migration 0068 / `runner_api.py`):
hash at rest, hint for the UI, revocation is a row update — dead on the next
request, no grace window.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.crypto import KeyVault
from harness_identity.models import AuditLog, AuthSession, Base, Team, TeamMember, User
from harness_identity.scim import MAX_TOKENS_PER_TEAM, TOKEN_PREFIX, ScimToken, scim_router
from harness_identity.scim import router as api_router

MASTER_KEY = b"test-master-key-0123456789abcdef"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(factory):
    app = FastAPI()
    app.include_router(api_router)
    app.include_router(scim_router)
    app.state.session_factory = factory
    app.state.vault = KeyVault(MASTER_KEY)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        c.app = app
        yield c


async def _seed(factory, *, plan: str | None = "team", role: str = "admin") -> dict:
    async with factory() as db:
        team = Team(name="scim-team")
        db.add(team)
        await db.flush()
        user = User(email=f"scim-{secrets.token_hex(4)}@x.com", name="Scim Admin")
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=user.id, display_name="Scim", role=role))
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team.id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        if plan is not None:
            from decimal import Decimal

            from harness_identity.billing import BillingPlan

            db.add(BillingPlan(team_id=team.id, plan=plan, monthly_budget_usd=Decimal("0")))
        await db.commit()
    return {"teamId": team.id, "userId": user.id, "token": token}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _error_code(r) -> str:
    body = r.json()
    err = body.get("error") or (body.get("detail") or {}).get("error")
    if isinstance(err, dict):
        return err.get("code", "<missing>")
    return "<missing>"


async def _mint(client, admin, label: str = "bridge"):
    return await client.post(
        f"/api/v1/teams/{admin['teamId']}/scim/tokens",
        json={"label": label},
        headers=_bearer(admin["token"]),
    )


async def test_admin_mints_a_scim_token_shown_once(client, factory):
    admin = await _seed(factory)
    r = await _mint(client, admin, "okta bridge")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"].startswith(TOKEN_PREFIX)
    assert body["hint"] == body["token"][-4:]
    assert body["label"] == "okta bridge"

    # The list carries no secret of any kind, and the stored row is a hash.
    listed = await client.get(
        f"/api/v1/teams/{admin['teamId']}/scim/tokens", headers=_bearer(admin["token"])
    )
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["id"] == body["id"]
    assert "token" not in item
    assert item["hint"] == body["hint"]
    async with factory() as db:
        row = (
            await db.execute(select(ScimToken).where(ScimToken.id == body["id"]))
        ).scalar_one()
        assert row.token_sha256 == hashlib.sha256(body["token"].encode()).hexdigest()


async def test_minted_token_opens_the_scim_door_and_revocation_closes_it(client, factory):
    admin = await _seed(factory)
    created = await _mint(client, admin)
    token = created.json()["token"]

    res = await client.get("/scim/v2/Users", headers=_bearer(token))
    assert res.status_code == 200, res.text
    assert res.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]

    revoke = await client.delete(
        f"/api/v1/teams/{admin['teamId']}/scim/tokens/{created.json()['id']}",
        headers=_bearer(admin["token"]),
    )
    assert revoke.status_code == 204

    after = await client.get("/scim/v2/Users", headers=_bearer(token))
    assert after.status_code == 401
    # Revoked and unknown answer identically: no oracle on which tokens exist.
    unknown = await client.get("/scim/v2/Users", headers=_bearer(f"{TOKEN_PREFIX}nope"))
    assert unknown.status_code == 401
    assert unknown.json() == after.json()


async def test_a_user_session_token_does_not_open_the_scim_door(client, factory):
    admin = await _seed(factory)
    res = await client.get("/scim/v2/Users", headers=_bearer(admin["token"]))
    assert res.status_code == 401


async def test_only_admin_and_team_tier_may_mint(client, factory):
    engineer = await _seed(factory, role="engineer")
    r = await _mint(client, engineer)
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"

    free = await _seed(factory, plan=None)
    r = await _mint(client, free)
    assert r.status_code == 403
    assert _error_code(r) == "PLAN_REQUIRED"


async def test_foreign_team_is_answered_like_an_unknown_one(client, factory):
    admin = await _seed(factory)
    other = await _seed(factory)
    r = await client.post(
        f"/api/v1/teams/{other['teamId']}/scim/tokens",
        json={"label": "peek"},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 404
    assert _error_code(r) == "NOT_FOUND"


@pytest.mark.parametrize("label", ["", "   ", "x" * 121])
async def test_label_is_bounded(client, factory, label):
    admin = await _seed(factory)
    r = await _mint(client, admin, label=label)
    assert r.status_code == 422, r.text
    assert _error_code(r) == "VALIDATION_ERROR"


async def test_a_team_cannot_accumulate_scim_tokens(client, factory):
    admin = await _seed(factory)
    for i in range(MAX_TOKENS_PER_TEAM):
        r = await _mint(client, admin, f"bridge {i}")
        assert r.status_code == 201, r.text
    r = await _mint(client, admin, "one too many")
    assert r.status_code == 409, r.text
    assert _error_code(r) == "LIMIT_EXCEEDED"


async def test_revocation_is_idempotent_and_records_audit_rows(client, factory):
    admin = await _seed(factory)
    created = await _mint(client, admin)
    token_id = created.json()["id"]

    async def rows(action):
        async with factory() as db:
            return (
                (await db.execute(select(AuditLog).where(AuditLog.action == action)))
                .scalars()
                .all()
            )

    assert len(await rows("scim.token_created")) == 1
    assert "token" not in str((await rows("scim.token_created"))[0].detail)

    r = await client.delete(
        f"/api/v1/teams/{admin['teamId']}/scim/tokens/{token_id}",
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 204
    again = await client.delete(
        f"/api/v1/teams/{admin['teamId']}/scim/tokens/{token_id}",
        headers=_bearer(admin["token"]),
    )
    assert again.status_code == 204

    revoked = await rows("scim.token_revoked")
    assert len(revoked) == 1  # the second DELETE changed nothing, so it says nothing
    assert revoked[0].detail["refId"] == token_id


def test_migration_0070_matches_model_shape():
    """The deploy lane runs the SQL file, the app lane runs the models; a
    divergence means production boots a schema the code cannot write to."""
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        .joinpath("migrations", "0070_scim_provisioning.sql")
        .read_text()
    )
    assert "CREATE TABLE IF NOT EXISTS scim_tokens" in sql
    assert "CREATE TABLE IF NOT EXISTS scim_user_links" in sql
    assert "REFERENCES users (id) ON DELETE CASCADE" in sql
    from harness_identity.scim import ScimUserLink

    assert {c.name for c in ScimToken.__table__.columns} == {
        "id",
        "team_id",
        "label",
        "token_sha256",
        "token_hint",
        "created_by",
        "revoked_by",
        "revoked_at",
        "last_used_at",
        "created_at",
    }
    assert {c.name for c in ScimUserLink.__table__.columns} == {
        "team_id",
        "user_id",
        "external_id",
        "updated_at",
    }
