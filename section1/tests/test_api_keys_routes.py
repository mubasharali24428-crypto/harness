"""Mission 10 node A1 — BYO-keys API tests (contract M10-C1 server side).

Self-contained by design (conftest.py is frozen): fixtures are local and the
router under test is mounted on a SCRATCH FastAPI app exactly like the wave9
invite suite does — app.py registration is ATLAS's merge step and we must not
touch that file. Sessions are seeded straight into the store with contract-
shaped token hashes so no login route is needed. Everything runs offline on
aiosqlite. The raw key must never appear in any response body.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.api_keys import router
from harness_identity.config import Settings
from harness_identity.crypto import KeyVault
from harness_identity.models import (
    ApiKey,
    AuthSession,
    Base,
    Team,
    TeamMember,
    User,
)

RAW_KEY = "sk-proj-abcdef1234567890SECRET"  # gitleaks:allow

# --------------------------------------------------------------- fixtures


@pytest.fixture
async def keys_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def keys_client(keys_factory):
    """Scratch app exposing ONLY this node's router; state mirrors what
    make_app() sets that our handlers read: session_factory + vault."""
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = keys_factory
    app.state.vault = KeyVault(
        Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[]).master_key
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_user(
    factory, email: str, *, role: str = "admin", team_id: str | None = None
) -> dict:
    """Create team/user/membership/session rows directly; return ids + bearer."""
    import secrets

    async with factory() as db:
        if team_id is None:
            team = Team(name=f"team-{email}")
            db.add(team)
            await db.flush()
            team_id = team.id
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team_id, user_id=user.id, display_name=user.name, role=role))
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team_id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    return {"teamId": team_id, "userId": user.id, "token": token}


async def _mint(keys_client, actor: dict, **overrides) -> dict:
    payload = {
        "label": "prod key",
        "provider": "openai",
        "key": RAW_KEY,
        **overrides,
    }
    r = await keys_client.post("/api/v1/keys", json=payload, headers=_bearer(actor["token"]))
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------ happy path


async def test_mint_list_revoke_lifecycle_hint_never_leaks_key(
    keys_factory, keys_client
):
    admin = await _seed_user(keys_factory, "boss@x.com", role="admin")

    # --- mint: 201, camelCase shape per M10-C1, no raw key anywhere
    created = await _mint(keys_client, admin, label="Prod OpenAI")
    assert set(created) == {"id", "label", "provider", "keyHint", "createdAt"}
    assert created["label"] == "Prod OpenAI"
    assert created["provider"] == "openai"
    assert RAW_KEY not in str(created)

    # --- list shows hint, not key; revokedAt starts null
    r = await keys_client.get("/api/v1/keys", headers=_bearer(admin["token"]))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [created["id"]]
    item = items[0]
    assert set(item) == {"id", "label", "provider", "keyHint", "createdAt", "revokedAt"}
    assert item["keyHint"] == "…" + RAW_KEY[-4:]  # existing KeyVault convention
    assert RAW_KEY not in r.text  # hint never leaks the full secret
    assert all("keyEncrypted" not in i for i in items)
    assert item["revokedAt"] is None

    # --- revoke: 204 soft-delete
    r = await keys_client.delete(
        f"/api/v1/keys/{created['id']}", headers=_bearer(admin["token"])
    )
    assert r.status_code == 204, r.text

    # --- revoked stays visible in the roster with revokedAt stamped
    r = await keys_client.get("/api/v1/keys", headers=_bearer(admin["token"]))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [created["id"]]  # still listed
    assert items[0]["revokedAt"] is not None
    stamped = datetime.fromisoformat(items[0]["revokedAt"])
    if stamped.tzinfo is None:  # sqlite stores naive; postgres would be aware
        stamped = stamped.replace(tzinfo=UTC)
    assert stamped <= datetime.now(UTC)


async def test_double_revoke_is_idempotent_204(keys_factory, keys_client):
    admin = await _seed_user(keys_factory, "dbl-admin@x.com", role="admin")
    created = await _mint(keys_client, admin)

    first = await keys_client.delete(
        f"/api/v1/keys/{created['id']}", headers=_bearer(admin["token"])
    )
    second = await keys_client.delete(
        f"/api/v1/keys/{created['id']}", headers=_bearer(admin["token"])
    )
    assert first.status_code == 204, first.text
    assert second.status_code == 204, second.text

    # ...and the stamp did not move on the second revoke
    from sqlalchemy import select

    async with keys_factory() as db:
        fresh = (
            (await db.execute(select(ApiKey).where(ApiKey.id == created["id"])))
            .scalars()
            .one()
        )
        assert fresh.revoked_at is not None
        stamp_after_first = fresh.revoked_at
    third = await keys_client.delete(
        f"/api/v1/keys/{created['id']}", headers=_bearer(admin["token"])
    )
    assert third.status_code == 204
    async with keys_factory() as db:
        fresh = await db.get(ApiKey, created["id"])
        assert fresh.revoked_at == stamp_after_first  # unchanged by re-revoke


async def test_encrypted_at_rest_stored_bytes_differ_from_plaintext(
    keys_factory, keys_client
):
    admin = await _seed_user(keys_factory, "enc-admin@x.com", role="admin")
    created = await _mint(keys_client, admin)

    async with keys_factory() as db:
        row = await db.get(ApiKey, created["id"])
        assert row.key_encrypted != RAW_KEY          # not plaintext at rest
        assert RAW_KEY not in row.key_encrypted      # Fernet never embeds it
        assert len(row.key_encrypted) >= len(RAW_KEY)
        assert row.key_hint == "…" + RAW_KEY[-4:]
        assert RAW_KEY not in row.key_hint

    # round-trips through the SAME vault (existing crypto pattern intact)
    vault = KeyVault(b"test-master-key-0123456789abcdef")
    assert vault.decrypt(row.key_encrypted) == RAW_KEY


async def test_viewer_read_ok_but_write_rejected(keys_factory, keys_client):
    admin = await _seed_user(keys_factory, "vw-admin@x.com", role="admin")
    viewer = await _seed_user(
        keys_factory, "vw-view@x.com", role="viewer", team_id=admin["teamId"]
    )
    created = await _mint(keys_client, admin)

    # viewer CAN read the roster
    r = await keys_client.get("/api/v1/keys", headers=_bearer(viewer["token"]))
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["items"]] == [created["id"]]

    # viewer CANNOT mint... (roles.can gate)
    r = await keys_client.post(
        "/api/v1/keys",
        json={"label": "nope", "provider": "openai", "key": RAW_KEY},
        headers=_bearer(viewer["token"]),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"]["code"] == "FORBIDDEN"  # contract envelope

    # ...nor revoke
    r = await keys_client.delete(
        f"/api/v1/keys/{created['id']}", headers=_bearer(viewer["token"])
    )
    assert r.status_code == 403, r.text
    err = r.json()["detail"]
    assert err["error"]["code"] == "FORBIDDEN"

    # unauthenticated callers are 401 before any of that matters
    r = await keys_client.get("/api/v1/keys")
    assert r.status_code == 401


async def test_foreign_team_revoke_and_list_are_not_cross_tenant(
    keys_factory, keys_client
):
    admin_a = await _seed_user(keys_factory, "a@x.com", role="admin")
    admin_b = await _seed_user(keys_factory, "b@y.com", role="admin")
    created = await _mint(keys_client, admin_a)

    # B cannot see A's key in their roster
    r = await keys_client.get("/api/v1/keys", headers=_bearer(admin_b["token"]))
    assert r.json()["items"] == []

    # explicit ?teamId= pointing outside membership answers the same 404 an
    # unknown team would produce (M13-G5a no-oracle law; was 403)
    r = await keys_client.get(
        f"/api/v1/keys?teamId={admin_a['teamId']}", headers=_bearer(admin_b["token"])
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"

    # DELETE of A's id by B: identical answer to an unknown id — 404, no oracle
    r = await keys_client.delete(
        f"/api/v1/keys/{created['id']}", headers=_bearer(admin_b["token"])
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"

    # unknown id behaves identically
    r = await keys_client.delete(
        "/api/v1/keys/00000000-dead-beef-0000-000000000000",
        headers=_bearer(admin_b["token"]),
    )
    assert r.status_code == 404

    # A's key was NOT touched by any of B's attempts
    r = await keys_client.get("/api/v1/keys", headers=_bearer(admin_a["token"]))
    assert r.json()["items"][0]["revokedAt"] is None


async def test_engineer_can_write_and_mint_scopes_to_own_team(
    keys_factory, keys_client
):
    admin = await _seed_user(keys_factory, "eng-admin@x.com", role="admin")
    engineer = await _seed_user(
        keys_factory, "eng-eng@x.com", role="engineer", team_id=admin["teamId"]
    )
    created = await _mint(keys_client, engineer, label="eng key")

    # scoped to the shared team, visible to both members
    for member in (admin, engineer):
        r = await keys_client.get("/api/v1/keys", headers=_bearer(member["token"]))
        assert r.status_code == 200
        ids = [i["id"] for i in r.json()["items"]]
        assert ids == [created["id"]]


async def test_mint_validation_rejects_bad_payloads(keys_factory, keys_client):
    admin = await _seed_user(keys_factory, "val-admin@x.com", role="admin")
    hdrs = _bearer(admin["token"])

    for payload, why in (
        ({"label": "", "provider": "openai", "key": RAW_KEY}, "empty label"),
        ({"label": "x" * 121, "provider": "openai", "key": RAW_KEY}, "label too long"),
        ({"label": "ok", "provider": "bad provider!", "key": RAW_KEY}, "bad provider"),
        ({"label": "ok", "provider": "openai"}, "missing key"),
        ({"label": "ok", "provider": "openai", "key": "abc"}, "key too short"),
    ):
        r = await keys_client.post("/api/v1/keys", json=payload, headers=hdrs)
        assert r.status_code in (403, 422), (why, r.text)


async def test_key_hint_fits_column_and_short_keys_get_ellipsis():
    # column guard: api_keys.key_hint is VARCHAR(8); hint convention fits
    assert len(KeyVault.hint(RAW_KEY)) <= 8
    assert KeyVault.hint("abc") == "…"


# ------------------------------------------------- schema/migration parity


def test_api_keys_model_matches_frozen_migration_shape():
    cols = ApiKey.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "team_id",
        "provider",
        "label",
        "key_encrypted",
        "key_hint",
        "revoked_at",
        "owner_user_id",  # A1-S2, migration 0035 (NULL = team key)
        "created_at",
    }
    assert cols["id"].primary_key
    assert cols["provider"].type.length == 40  # type: ignore[attr-defined]
    assert cols["key_hint"].type.length == 8  # type: ignore[attr-defined]
