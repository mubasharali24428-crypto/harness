"""A1-S1 — /api/v1/policy HAP/1 evaluator + capability issuance (scratch app, offline)."""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.config import Settings
from harness_identity.models import (
    AuditLog,
    AuthSession,
    Base,
    Project,
    Team,
    TeamMember,
    User,
)
from harness_identity.policy_api import router

PEM = "-----BEGIN PRIVATE KEY-----\n"
ACT_ISSUED, ACT_DENIED = "policy.capability_issued", "policy.denied"
SECRET = "sk-ant-api03-" + "Z" * 40  # gitleaks:allow


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    app.state.settings = Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield client, factory
    await engine.dispose()


async def _seed(factory, email, role="admin"):
    async with factory() as db:
        team = Team(name=f"t-{email}")
        db.add(team)
        await db.flush()
        user = User(email=email, name=email)
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=user.id, display_name=email, role=role))
        project = Project(team_id=team.id, name="p")
        db.add(project)
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
        return {"h": {"Authorization": f"Bearer {token}"}, "team": team.id, "project": project.id}


def _body(a, op, args, **kw):
    return {
        "schemaVersion": "hap/1",
        "requestId": "r1",
        "projectId": a["project"],
        "runId": "run1",
        "operation": op,
        "arguments": args,
        **kw,
    }


async def test_requires_auth(env):
    client, _ = env
    assert (await client.post("/api/v1/policy/evaluate", json={})).status_code == 401


async def test_secret_prompt_denied_and_audited_without_content(env):
    client, factory = env
    a = await _seed(factory, "a@x.io")
    r = await client.post(
        "/api/v1/policy/evaluate",
        headers=a["h"],
        json=_body(a, "policy.scan_outbound", {"text": PEM + SECRET}),
    )
    assert r.status_code == 200 and r.json()["status"] == "denied"
    assert SECRET not in r.text
    async with factory() as db:
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.action == ACT_DENIED)))
            .scalars()
            .all()
        )
    assert len(rows) == 1 and SECRET not in str(rows[0].detail)
    assert rows[0].detail["teamId"] == a["team"]


async def test_redaction_never_echoes_secret(env):
    client, factory = env
    a = await _seed(factory, "a@x.io")
    r = await client.post(
        "/api/v1/policy/evaluate",
        headers=a["h"],
        json=_body(a, "policy.scan_outbound", {"text": f"use {SECRET} now"}),
    )
    assert r.json()["status"] == "succeeded" and SECRET not in r.text


async def test_foreign_project_is_404(env):
    client, factory = env
    a = await _seed(factory, "a@x.io")
    b = await _seed(factory, "b@x.io")
    forged = _body(b, "policy.scan_outbound", {"text": "hi"})  # b's project, a's token
    r = await client.post("/api/v1/policy/evaluate", headers=a["h"], json=forged)
    assert r.status_code == 404


async def test_body_team_id_ignored_capability_bound_to_principal(env):
    client, factory = env
    a = await _seed(factory, "a@x.io")
    b = await _seed(factory, "b@x.io")
    grant = await client.post(
        "/api/v1/policy/capabilities",
        headers=a["h"],
        json={"projectId": a["project"], "runId": "run1", "operation": "tool.x", "audience": "s5"},
    )
    assert grant.status_code == 201
    tok = grant.json()["capability"]
    ok = {"token": tok, "requiredOperation": "tool.x", "audience": "s5"}
    r = await client.post(
        "/api/v1/policy/evaluate", headers=a["h"], json=_body(a, "policy.capability_verify", ok)
    )
    assert r.json()["status"] == "succeeded"
    # other tenant presenting the same bearer capability against its own project
    r = await client.post(
        "/api/v1/policy/evaluate",
        headers=b["h"],
        json=_body(b, "policy.capability_verify", {**ok, "teamId": a["team"]}),
    )
    assert r.json()["status"] == "denied"
    # wrong audience / operation
    for bad in ({"audience": "other"}, {"requiredOperation": "tool.y"}):
        r = await client.post(
            "/api/v1/policy/evaluate",
            headers=a["h"],
            json=_body(a, "policy.capability_verify", {**ok, **bad}),
        )
        assert r.json()["status"] == "denied"
    # wrong run
    wrong_run = {**_body(a, "policy.capability_verify", ok), "runId": "run2"}
    r = await client.post("/api/v1/policy/evaluate", headers=a["h"], json=wrong_run)
    assert r.json()["status"] == "denied"


async def test_viewer_cannot_issue_grant(env):
    client, factory = env
    v = await _seed(factory, "v@x.io", role="viewer")
    r = await client.post(
        "/api/v1/policy/capabilities",
        headers=v["h"],
        json={"projectId": v["project"], "runId": "r", "operation": "o", "audience": "a"},
    )
    assert r.status_code == 403


async def test_grant_audit_has_no_token(env):
    client, factory = env
    a = await _seed(factory, "a@x.io")
    r = await client.post(
        "/api/v1/policy/capabilities",
        headers=a["h"],
        json={"projectId": a["project"], "runId": "r", "operation": "o", "audience": "a"},
    )
    tok = r.json()["capability"]
    async with factory() as db:
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.action == ACT_ISSUED)))
            .scalars()
            .all()
        )
    assert len(rows) == 1 and tok not in str(rows[0].detail) and tok not in rows[0].target_id


async def test_egress_private_url_denied_and_bad_schema_422(env):
    client, factory = env
    a = await _seed(factory, "a@x.io")
    r = await client.post(
        "/api/v1/policy/evaluate",
        headers=a["h"],
        json=_body(
            a, "policy.egress", {"url": "http://169.254.169.254/x", "allowedHosts": ["example.com"]}
        ),
    )
    assert r.json()["status"] == "denied"
    bad = _body(a, "policy.scan_outbound", {"text": "x"})
    bad["schemaVersion"] = "hap/9"
    r = await client.post("/api/v1/policy/evaluate", headers=a["h"], json=bad)
    assert r.status_code == 422
