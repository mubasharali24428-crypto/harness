"""A1-S5 — workspace lifecycle, trial-to-paid, billing bypass, org overview, evidence, residency."""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.billing import BillingPlan
from harness_identity.billing import router as billing_router
from harness_identity.config import Settings
from harness_identity.governance_api import router as gov_router
from harness_identity.lifecycle_api import expire_due_trials, router
from harness_identity.model_policy_api import router as mp_router
from harness_identity.models import (
    ApiKey,
    AuditLog,
    AuthSession,
    Base,
    Team,
    TeamMember,
    TeamTrial,
    User,
)
from harness_identity.stripe_adapter import process_event

P = "/api/v1/identity"


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.delenv("HARNESS_BILLING", raising=False)
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    for r in (router, gov_router, mp_router, billing_router):
        app.include_router(r)
    app.state.session_factory = factory
    app.state.settings = Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield client, factory
    await engine.dispose()


async def _member(factory, team_id=None, role="admin", team_name="Acme"):
    async with factory() as db:
        if team_id is None:
            team = Team(name=team_name)
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


async def _plan(factory, team_id):
    async with factory() as db:
        return (
            await db.execute(select(BillingPlan.plan).where(BillingPlan.team_id == team_id))
        ).scalar_one_or_none()


# ------------------------------------------------------------------ billing bypass


async def test_admin_cannot_self_upgrade_when_provider_owns_billing(env, monkeypatch):
    client, factory = env
    a = await _member(factory)
    monkeypatch.setenv("HARNESS_BILLING", "local")
    assert (
        await client.put("/api/v1/billing/plan", headers=a["h"], json={"plan": "pro"})
    ).status_code == 200
    monkeypatch.setenv("HARNESS_BILLING", "stripe")
    r = await client.put("/api/v1/billing/plan", headers=a["h"], json={"plan": "team"})
    assert (
        r.status_code == 409
        and r.json()["detail"]["error"]["code"] == "BILLING_MANAGED_BY_PROVIDER"
    )
    assert await _plan(factory, a["team"]) == "pro"  # unchanged
    eng = await _member(factory, a["team"], "engineer")
    monkeypatch.setenv("HARNESS_BILLING", "local")
    assert (
        await client.put("/api/v1/billing/plan", headers=eng["h"], json={"plan": "team"})
    ).status_code == 403


# ------------------------------------------------------------------ workspaces


async def test_workspace_create_rename_close_lifecycle(env):
    client, factory = env
    a = await _member(factory)
    k = ApiKey(team_id=a["team"], provider="openai", label="k", key_encrypted="x", key_hint="…abcd")
    async with factory() as db:
        db.add(k)
        await db.commit()
    new = await client.post(f"{P}/workspaces", headers=a["h"], json={"name": "Second"})
    assert new.status_code == 201 and new.json()["role"] == "admin"
    async with factory() as db:  # caller is admin of the new team
        m = (
            await db.execute(select(TeamMember).where(TeamMember.team_id == new.json()["teamId"]))
        ).scalar_one()
        assert m.user_id == a["id"]
    eng = await _member(factory, a["team"], "engineer")
    assert (
        await client.patch(f"{P}/workspace", headers=eng["h"], json={"name": "X"})
    ).status_code == 403
    assert (await client.patch(f"{P}/workspace", headers=a["h"], json={"name": "Renamed"})).json()[
        "name"
    ] == "Renamed"
    assert (
        await client.post(f"{P}/workspace/close", headers=a["h"], json={"confirmName": "Acme"})
    ).status_code == 422
    assert (
        await client.post(f"{P}/workspace/close", headers=eng["h"], json={"confirmName": "Renamed"})
    ).status_code == 403
    ok = await client.post(f"{P}/workspace/close", headers=a["h"], json={"confirmName": "Renamed"})
    assert ok.json() == {"teamId": a["team"], "closed": True}
    assert (await client.get(f"{P}/trial", headers=a["h"])).status_code == 401  # sessions dead
    async with factory() as db:
        assert (await db.get(ApiKey, k.id)).revoked_at is not None
        assert (await db.get(Team, a["team"])).closed_at is not None


async def test_workspace_limit_and_paid_plan_blocks_close(env):
    client, factory = env
    a = await _member(factory)
    for i in range(4):
        assert (
            await client.post(f"{P}/workspaces", headers=a["h"], json={"name": f"w{i}"})
        ).status_code == 201
    assert (
        await client.post(f"{P}/workspaces", headers=a["h"], json={"name": "w5"})
    ).status_code == 409
    b = await _member(factory)
    async with factory() as db:
        from decimal import Decimal

        db.add(BillingPlan(team_id=b["team"], plan="pro", monthly_budget_usd=Decimal("0")))
        await db.commit()
    r = await client.post(f"{P}/workspace/close", headers=b["h"], json={"confirmName": "Acme"})
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "PAID_PLAN_ACTIVE"


# ------------------------------------------------------------------ trial -> paid


async def test_trial_once_expires_to_free_and_is_honest(env):
    client, factory = env
    a = await _member(factory)
    eng = await _member(factory, a["team"], "engineer")
    assert (await client.post(f"{P}/trial/start", headers=eng["h"])).status_code == 403
    s = await client.post(f"{P}/trial/start", headers=a["h"])
    assert s.status_code == 201 and s.json()["status"] == "active" and s.json()["daysLeft"] >= 13
    assert await _plan(factory, a["team"]) == "pro"
    assert (await client.post(f"{P}/trial/start", headers=a["h"])).json()["detail"]["error"][
        "code"
    ] == "TRIAL_USED"
    async with factory() as db:
        t = await db.get(TeamTrial, a["team"])
        t.ends_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
    st = (await client.get(f"{P}/trial", headers=a["h"])).json()
    assert st["status"] == "expired" and st["currentPlan"] == "free"
    assert await _plan(factory, a["team"]) == "free"
    assert (
        await client.post(f"{P}/trial/start", headers=a["h"])
    ).status_code == 409  # no second trial


async def test_trial_conversion_by_signed_payment_event_survives_expiry(env):
    client, factory = env
    a = await _member(factory)
    await client.post(f"{P}/trial/start", headers=a["h"])
    event = {
        "id": "evt_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": a["team"],
                "metadata": {"harness_team_id": a["team"], "tier": "team"},
            }
        },
    }
    result = await process_event(factory, event)
    assert result.get("applied") is True, result
    async with factory() as db:
        t = await db.get(TeamTrial, a["team"])
        assert t.converted_at is not None
        t.ends_at = datetime.now(UTC) - timedelta(days=1)
        await db.commit()
    assert (await client.get(f"{P}/trial", headers=a["h"])).json()["status"] == "converted"
    assert await _plan(factory, a["team"]) == "team"  # paid plan not reverted by expiry


async def test_sweep_expires_only_due_unconverted_trials(env):
    client, factory = env
    due, fresh = await _member(factory), await _member(factory)
    for m in (due, fresh):
        await client.post(f"{P}/trial/start", headers=m["h"])
    async with factory() as db:
        (await db.get(TeamTrial, due["team"])).ends_at = datetime.now(UTC) - timedelta(hours=1)
        await db.commit()
    assert await expire_due_trials(factory) == 1
    assert (
        await _plan(factory, due["team"]) == "free" and await _plan(factory, fresh["team"]) == "pro"
    )
    assert await expire_due_trials(factory) == 0


# ------------------------------------------------------------------ overview / evidence / residency


async def test_org_overview_admin_only_and_tenant_scoped(env):
    client, factory = env
    a, b = await _member(factory), await _member(factory)
    eng = await _member(factory, a["team"], "engineer")
    assert (await client.get(f"{P}/org/overview", headers=eng["h"])).status_code == 403
    o = (await client.get(f"{P}/org/overview", headers=a["h"])).json()
    assert o["teamId"] == a["team"] and o["members"]["byRole"] == {"admin": 1, "engineer": 1}
    assert o["billing"]["plan"] == "free" and o["models"]["allowlistEnforced"] is False
    assert (await client.get(f"{P}/org/overview", headers=b["h"])).json()["members"]["byRole"] == {
        "admin": 1
    }


async def test_evidence_redacted_verifiable_and_tamper_evident(env):
    client, factory = env
    a, b = await _member(factory), await _member(factory)
    async with factory() as db:
        from harness_identity.audit import write_audit

        mid = (
            await db.execute(select(TeamMember.id).where(TeamMember.user_id == a["id"]))
        ).scalar_one()
        await write_audit(db, "plan.approved", actor_member_id=mid, detail={"teamId": a["team"]})
        db.add(
            ApiKey(
                team_id=a["team"],
                provider="openai",
                label="k",
                key_encrypted="CIPHERTEXTSECRET",
                key_hint="…1234",
            )
        )
        await db.commit()
    eng = await _member(factory, a["team"], "engineer")
    assert (await client.get(f"{P}/compliance/evidence", headers=eng["h"])).status_code == 403
    ev = (await client.get(f"{P}/compliance/evidence", headers=a["h"])).json()
    raw = str(ev)
    assert "CIPHERTEXTSECRET" not in raw and "@x.io" not in raw and "1234" not in raw
    audit = next(c for c in ev["controls"] if c["id"] == "audit_trail")["evidence"]
    assert audit["byAction"]["plan.approved"] == 1 and len(audit["chainSha256"]) == 64
    gate = next(c for c in ev["controls"] if c["id"] == "plan_approval_gate")["evidence"]
    assert gate["approvals"] == 1
    assert (
        await client.post(f"{P}/compliance/evidence/verify", headers=a["h"], json={"document": ev})
    ).json()["valid"]
    ev["controls"][0]["evidence"]["byRole"] = {"admin": 99}
    bad = (
        await client.post(f"{P}/compliance/evidence/verify", headers=a["h"], json={"document": ev})
    ).json()
    assert bad == {"valid": False, "reason": "evidence.tampered"}
    ev2 = (await client.get(f"{P}/compliance/evidence", headers=a["h"])).json()
    cross = (
        await client.post(f"{P}/compliance/evidence/verify", headers=b["h"], json={"document": ev2})
    ).json()
    assert cross["reason"] == "evidence.wrong_team"
    async with factory() as db:
        assert "compliance.evidence_exported" in [
            r.action for r in (await db.execute(select(AuditLog))).scalars()
        ]


async def test_residency_policy_roundtrip_and_simulated_denial(env):
    client, factory = env
    a = await _member(factory)
    doc = {
        "residency": {
            "enforced": True,
            "allowedRegions": ["eu"],
            "providerRegions": {"anthropic": ["eu"], "openai": ["us"]},
        }
    }
    assert (
        await client.put(
            f"{P}/model-policy", headers=a["h"], json={"residency": {"enforced": True}}
        )
    ).status_code == 422
    saved = (await client.put(f"{P}/model-policy", headers=a["h"], json=doc)).json()
    assert saved["residency"]["allowedRegions"] == ["eu"]
    from harness_identity.governance_api import router as _g  # noqa: F401

    out = {}
    for m in ("anthropic/claude-3-5-haiku", "openai/gpt-4o", "mistral/codestral"):
        r = await client.post(f"{P}/policy/simulate", headers=a["h"], json={"model": m})
        out[m] = r.json()["checks"][0]["reason"]
    assert out == {
        "anthropic/claude-3-5-haiku": "model.allowed",
        "openai/gpt-4o": "model.residency_denied",
        "mistral/codestral": "model.residency_unknown",
    }
    ov = (await client.get(f"{P}/org/overview", headers=a["h"])).json()
    assert ov["residency"] == {"enforced": True, "allowedRegions": ["eu"]}
