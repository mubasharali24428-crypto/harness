"""G-D rotation drill (P4): plant under the OLD key, rotate, prove the NEW key
reads everything and no plaintext ever appeared on any read surface.

Runs the same code path as `section1/scripts/rotate_master_key.py`
(crypto.rotate_master_key) against a scratch database, so CI drills rotation
on every run instead of trusting the runbook. The PG-shaped variant is the
nightly live probe; this sqlite drill pins the semantics.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)

OLD = b"drill-old-key-0123456789abcdef0000"
NEW = b"drill-new-key-fedcba98765432100000"
CANARY = "sk-ROTATION-CANARY-ab12cd34ef56"


@pytest.fixture
async def rotation_env():
    from harness_contracts import hash_token
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import harness_identity.siem_sink  # noqa: F401 - registers siem_sinks model
    import harness_identity.sso  # noqa: F401 - registers team_sso_configs model
    from harness_identity.app import make_app
    from harness_identity.config import Settings
    from harness_identity.models import ApiKey, AuthSession, Base, Team, TeamMember, User

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = make_app(factory, Settings(master_key=OLD, cors_origins=[],
                                     require_ownership=False))
    sent: list[str] = []
    app.state.notifier = lambda email, code: sent.append(code)
    async with factory() as db:
        team = Team(name="alpha")
        db.add(team)
        await db.flush()
        user = User(email="admin@alpha.io", name="admin", is_active=True)
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=user.id, role="ADMIN",
                          display_name="admin"))
        db.add(AuthSession(token_hash=hash_token("tok-drill"), user_id=user.id,
                           team_id=team.id,
                           expires_at=None) if False else AuthSession(
            token_hash=hash_token("tok-drill"), user_id=user.id, team_id=team.id,
            expires_at=__import__("datetime").datetime.now(
                __import__("datetime").UTC) + __import__("datetime").timedelta(hours=1)))
        await db.commit()
    yield {
        "factory": factory, "app": app, "select": select, "ApiKey": ApiKey,
        "engine": engine, "user_id": user.id,
    }
    await engine.dispose()


async def test_rotation_rewraps_everything_and_new_key_reads_it(rotation_env):

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select as _select

    from harness_identity.app import make_app
    from harness_identity.config import Settings
    from harness_identity.crypto import (
        ensure_kdf_params,
        load_kdf_params,
        rotate_master_key,
    )

    env = rotation_env
    factory = env["factory"]
    # Plant a provider key under the OLD key.
    async with AsyncClient(transport=ASGITransport(app=env["app"]),
                           base_url="http://t") as client:
        r = await client.post(
            "/api/v1/keys", headers={"Authorization": "Bearer tok-drill"},
            json={"label": "canary", "provider": "openai", "key": CANARY})
        assert r.status_code == 201, r.text
    # Mint KDF params (the app does this lazily on first salted write; the
    # plant above may precede it) and rotate.
    async with factory() as db:
        await ensure_kdf_params(db, OLD)
        await db.commit()
        summary = await rotate_master_key(db, OLD, NEW, dry_run=False)
    assert summary["failed"] == 0, summary
    assert summary["rewrapped"] >= 1, summary
    # The NEW key decrypts what the OLD key wrote.
    async with factory() as db:
        row = (await db.execute(_select(env["ApiKey"]))).scalars().first()
        _, new_read, _legacy = await load_kdf_params(db, NEW)
        clear = new_read.decrypt(row.key_encrypted)
        if isinstance(clear, bytes):
            clear = clear.decode()
    assert clear.startswith("sk-ROTATION-CANARY"), clear[:20]
    # And an app booted on the NEW key serves reads (hint only — never plaintext).
    app2 = make_app(factory, Settings(master_key=NEW, cors_origins=[],
                                      require_ownership=False))
    async with AsyncClient(transport=ASGITransport(app=app2),
                           base_url="http://t") as client:
        r = await client.get("/api/v1/keys",
                             headers={"Authorization": "Bearer tok-drill"})
        assert r.status_code == 200
        assert CANARY not in r.text, "plaintext key on the list surface!"
        assert r.json()["items"][0]["keyHint"].endswith("ef56")


async def test_no_read_surface_carries_the_plaintext(rotation_env):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    env = rotation_env
    surfaces = (
        ("GET", "/api/v1/keys"), ("GET", "/api/v1/audit"),
        ("GET", "/api/v1/audit/export"), ("GET", "/api/v1/me"),
        ("GET", "/api/v1/billing/state"),
    )
    async with AsyncClient(transport=ASGITransport(app=env["app"]),
                           base_url="http://t") as client:
        r = await client.post(
            "/api/v1/keys", headers={"Authorization": "Bearer tok-drill"},
            json={"label": "canary", "provider": "openai", "key": CANARY})
        assert r.status_code == 201
        for method, url in surfaces:
            rr = await client.request(method, url,
                                      headers={"Authorization": "Bearer tok-drill"})
            assert CANARY not in rr.text, f"{url} leaked the planted key"
    async with env["factory"]() as db:
        rows = (await db.execute(
            text("SELECT detail FROM audit_log ORDER BY ts DESC LIMIT 50"))).all()
        assert not any(CANARY in str(row) for row in rows), "audit_log holds plaintext"
