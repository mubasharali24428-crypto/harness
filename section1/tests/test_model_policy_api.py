"""A1-S3 — org model allowlist approval workflow + model policy API (offline)."""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.model_policy_api import load_policy, router
from harness_identity.models import (
    AuditLog,
    AuthSession,
    Base,
    Team,
    TeamMember,
    TeamSetting,
    User,
)

P = "/api/v1/identity"


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield client, factory
    await engine.dispose()


async def _member(factory, team_id=None, role="admin"):
    async with factory() as db:
        if team_id is None:
            team = Team(name="t")
            db.add(team)
            await db.flush()
            team_id = team.id
        n = secrets.token_hex(3)
        user = User(email=f"{n}@x.io", name=n)
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team_id, user_id=user.id, display_name=n, role=role))
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
        return {"team": team_id, "id": user.id, "h": {"Authorization": f"Bearer {token}"}}


async def test_request_then_admin_approves_and_policy_reflects(env):
    client, factory = env
    admin = await _member(factory)
    eng = await _member(factory, admin["team"], "engineer")
    r = await client.post(f"{P}/model-allowlist", headers=eng["h"], json={"model": "openai/gpt-4o"})
    assert r.status_code == 201 and r.json()["status"] == "pending"
    again = await client.post(
        f"{P}/model-allowlist", headers=eng["h"], json={"model": "openai/gpt-4o"}
    )
    assert again.json()["id"] == r.json()["id"]  # idempotent
    eid = r.json()["id"]
    assert (
        await client.post(f"{P}/model-allowlist/{eid}/approve", headers=eng["h"])
    ).status_code == 403
    ok = await client.post(f"{P}/model-allowlist/{eid}/approve", headers=admin["h"])
    assert ok.json()["status"] == "approved" and ok.json()["decidedBy"] == admin["id"]
    assert (
        await client.post(f"{P}/model-allowlist/{eid}/approve", headers=admin["h"])
    ).status_code == 409
    pol = await client.get(f"{P}/model-policy", headers=eng["h"])
    assert (
        pol.json()["approvedModels"] == ["openai/gpt-4o"]
        and pol.json()["allowlistEnforced"] is False
    )


async def test_deny_revoke_rerequest_cycle_and_viewer_cannot_request(env):
    client, factory = env
    admin = await _member(factory)
    viewer = await _member(factory, admin["team"], "viewer")
    assert (
        await client.post(f"{P}/model-allowlist", headers=viewer["h"], json={"model": "a/x"})
    ).status_code == 403
    eid = (
        await client.post(f"{P}/model-allowlist", headers=admin["h"], json={"model": "a/x"})
    ).json()["id"]
    assert (
        await client.post(f"{P}/model-allowlist/{eid}/revoke", headers=admin["h"])
    ).status_code == 409
    assert (await client.post(f"{P}/model-allowlist/{eid}/deny", headers=admin["h"])).json()[
        "status"
    ] == "denied"
    again = await client.post(f"{P}/model-allowlist", headers=admin["h"], json={"model": "a/x"})
    assert again.json()["status"] == "pending" and again.json()["decidedBy"] is None
    await client.post(f"{P}/model-allowlist/{eid}/approve", headers=admin["h"])
    assert (await client.post(f"{P}/model-allowlist/{eid}/revoke", headers=admin["h"])).json()[
        "status"
    ] == "revoked"
    got = await client.get(f"{P}/model-allowlist?status=revoked", headers=viewer["h"])
    assert [i["model"] for i in got.json()["items"]] == ["a/x"]
    async with factory() as db:
        acts = [r.action for r in (await db.execute(select(AuditLog))).scalars()]
    assert {
        "model_allowlist.requested",
        "model_allowlist.denied",
        "model_allowlist.revoked",
    } <= set(acts)


async def test_cross_tenant_entries_invisible_and_undecidable(env):
    client, factory = env
    a = await _member(factory)
    b = await _member(factory)
    eid = (await client.post(f"{P}/model-allowlist", headers=a["h"], json={"model": "a/x"})).json()[
        "id"
    ]
    assert (
        await client.post(f"{P}/model-allowlist/{eid}/approve", headers=b["h"])
    ).status_code == 404
    assert (await client.get(f"{P}/model-allowlist", headers=b["h"])).json()["items"] == []
    # same model name in another team is an independent row
    other = await client.post(f"{P}/model-allowlist", headers=b["h"], json={"model": "a/x"})
    assert other.status_code == 201 and other.json()["id"] != eid


async def test_bad_model_names_rejected(env):
    client, factory = env
    a = await _member(factory)
    for bad in ("", "../etc", "a b", "x" * 121, "-x"):
        r = await client.post(f"{P}/model-allowlist", headers=a["h"], json={"model": bad})
        assert r.status_code == 422, bad


async def test_policy_put_admin_only_validated_and_corrupt_fails_closed(env):
    client, factory = env
    admin = await _member(factory)
    eng = await _member(factory, admin["team"], "engineer")
    doc = {
        "allowlistEnforced": True,
        "fallbackEnabled": True,
        "fallbackEquivalents": {"a/x": ["a/y"]},
        "cacheEnabled": True,
        "cacheTtlSeconds": 60,
        "routingRules": [{"taskType": "review", "model": "a/y"}],
    }
    assert (await client.put(f"{P}/model-policy", headers=eng["h"], json=doc)).status_code == 403
    assert (
        await client.put(f"{P}/model-policy", headers=admin["h"], json={"cacheTtlSeconds": 0})
    ).status_code == 422
    assert (await client.put(f"{P}/model-policy", headers=admin["h"], json=[1])).status_code == 422
    ok = await client.put(f"{P}/model-policy", headers=admin["h"], json=doc)
    assert ok.status_code == 200 and ok.json()["allowlistEnforced"] is True
    assert ok.json()["fallbackEquivalents"] == {"a/x": ["a/y"]}
    async with factory() as db:
        row = await db.get(TeamSetting, (admin["team"], "model_policy"))
        row.value = "{not json"
        await db.commit()
        pol = await load_policy(db, admin["team"])
    assert pol.allowlist_enforced is True and pol.approved_models == frozenset()  # fail closed
