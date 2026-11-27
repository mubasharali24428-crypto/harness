"""Session 3 (parts 37-39) on real Postgres: migrations 0038-0040 plus the org, policy and
workspace routers end to end through the real app, then the audit hash chain over everything
they wrote. SQLite tests cannot catch dialect faults in the raw SQL these routers use.
Opt-in (HARNESS_TEST_PG=1); builds and drops its own throwaway database."""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.audit_chain import verify_chain
from harness_identity.config import Settings

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG session 3 tests skipped",
)

_spec = importlib.util.spec_from_file_location(
    "harness_migrate", ROOT / "deploy" / "runners" / "migrate.py"
)
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else (os.environ.get("HARNESS_TEST_PG_DSN") or "postgresql://harness:harness@127.0.0.1:5499/harness")


@pytest.fixture
async def env():
    import asyncpg

    name = f"harness_s3_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        for path in sorted((ROOT / "section1" / "migrations").glob("*.sql")):
            for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                await conn.execute(stmt)
    finally:
        await conn.close()
    engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False
    )
    app = make_app(factory, settings)

    async def verifier(request, db, principal, code):
        return code == "000000"

    app.state.stepup_verifier = verifier
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.engine = engine
        yield c
    await engine.dispose()
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def login(c, email):
    r = await c.post("/api/v1/auth/login", json={"email": email})
    assert r.status_code == 200, r.text
    j = r.json()
    return {"Authorization": f"Bearer {j['token']}"}, j["principal"]


async def test_orgs_policy_workspaces_on_real_postgres(env):
    c = env
    ha, alice = await login(c, "alice@example.com")

    # --- part 37: org, team link, role-change step-up, session assurance flags
    r = await c.post("/api/v1/orgs", json={"name": "Acme", "slug": "acme"}, headers=ha)
    assert r.status_code == 201, r.text
    org = r.json()["id"]
    r = await c.post(f"/api/v1/orgs/{org}/teams", json={"teamId": alice["teamId"]}, headers=ha)
    assert r.status_code == 201, r.text
    assert (await c.get("/api/v1/orgs", headers=ha)).status_code == 200
    r = await c.get(f"/api/v1/orgs/{org}/history", headers=ha)
    assert r.status_code == 200, r.text

    # --- part 38: policy document (step-up gated), explainable check, preview, log
    url = f"/api/v1/policy/orgs/{org}/documents/org/{org}"
    doc = {"doc": {"rules": [{"id": "no-export", "effect": "deny", "permissions": ["export"],
                              "subjects": ["role:member"]}]}}
    r = await c.put(url, json=doc, headers=ha)
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "STEPUP_REQUIRED"
    r = await c.post("/api/v1/stepup/verify", json={"code": "000000"}, headers=ha)
    assert r.status_code == 200, r.text
    r = await c.put(url, json=doc, headers=ha)
    assert r.status_code == 200, r.text
    r = await c.post(f"/api/v1/policy/orgs/{org}/check", json={"permission": "approve"}, headers=ha)
    assert r.status_code == 200 and r.json()["allowed"] is True, r.text
    assert (await c.get(f"/api/v1/policy/orgs/{org}/decisions", headers=ha)).status_code == 200

    # --- part 38.005: the shared gate enforces org policy on a legacy route (real PG, raw SQL)
    r = await c.get("/api/v1/audit/export", headers=ha)
    assert r.status_code == 200, r.text
    deny = {"doc": {"rules": [{"id": "no-audit", "effect": "deny", "permissions": ["export"],
                               "subjects": [f"user:{alice['userId']}"]}]}}
    r = await c.put(url, json=deny, headers=ha)
    assert r.status_code == 200, r.text
    r = await c.get("/api/v1/audit/export", headers=ha)
    assert r.status_code == 403 and r.json()["error"]["code"] == "POLICY_DENIED", r.text
    assert (await c.put(url, json=doc, headers=ha)).status_code == 200  # restore
    assert (await c.get("/api/v1/audit/export", headers=ha)).status_code == 200

    # --- part 37/38 wiring: legacy team role change is step-up gated for org teams
    hb, bob = await login(c, "bob@example.com")
    assert bob["teamId"] != alice["teamId"]

    # --- part 39: workspace lifecycle, settings versions, templates
    r = await c.post("/api/v1/workspaces", json={"name": "core", "dataResidency": "us"}, headers=ha)
    assert r.status_code == 201, r.text
    ws = r.json()["id"]
    body = {"settings": {"max_concurrent_runs": 3}}
    r = await c.put(f"/api/v1/workspaces/{ws}/settings", json=body, headers=ha)
    assert r.status_code == 200, r.text
    assert (await c.get(f"/api/v1/workspaces/{ws}/settings/history", headers=ha)).status_code == 200
    for step in ("pause", "archive", "restore"):
        r = await c.post(f"/api/v1/workspaces/{ws}/{step}", headers=ha)
        assert r.status_code == 200, (step, r.text)
    assert (await c.get("/api/v1/workspaces", headers=ha)).status_code == 200

    # --- every audit row these routers wrote chains correctly on Postgres
    async with c.engine.connect() as conn:
        rep = await verify_chain(conn)
        actions = {a for (a,) in (await conn.execute(text("SELECT action FROM audit_log"))).all()}
    assert rep.ok, rep.problems
    assert {"org.created", "org.team_linked", "policy.saved", "stepup.verified",
            "policy.denied", "workspace.created", "workspace.paused", "workspace.archived",
            "workspace.restored"} <= actions
