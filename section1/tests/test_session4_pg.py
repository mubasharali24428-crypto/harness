"""Session 4 (parts 40, 45) on real Postgres: migrations 0041-0044 plus the sessions and agent
registry routers through the real app, then the audit hash chain over what they wrote. SQLite
cannot catch dialect faults in their raw SQL (jsonb, ON CONFLICT, TIMESTAMPTZ binds).
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
S, A = "/api/v1/sessions", "/api/v1/agents"

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG session 4 tests skipped",
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

    name = f"harness_s4id_{uuid.uuid4().hex[:10]}"
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


async def test_sessions_and_agents_on_real_postgres(env, monkeypatch):
    c = env
    monkeypatch.setenv("HARNESS_AGENT_ALLOW_SELF_APPROVAL", "1")
    ha, alice = await login(c, "alice@example.com")
    hb, _ = await login(c, "bob@example.com")  # another team

    # --- part 40: session, template (versioned), idempotent create, run link, resume
    r = await c.post(S, json={"title": "Pg session", "clientRequestId": "k1"}, headers=ha)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    again = await c.post(S, json={"title": "Pg session", "clientRequestId": "k1"}, headers=ha)
    assert again.status_code == 200 and again.json()["id"] == sid
    tpl = {"name": "triage", "defaults": {"description": "weekly", "retentionDays": 14}}
    for version in (1, 2):
        r = await c.post(f"{S}/templates", json=tpl, headers=ha)
        assert r.status_code == 201 and r.json()["version"] == version, r.text
    r = await c.post(f"{S}/{sid}/apply-template", json={"name": "triage"}, headers=ha)
    assert r.status_code == 200, r.text
    assert (await c.get(S, headers=ha)).status_code == 200
    assert (await c.get(f"{S}/{sid}", headers=hb)).status_code == 404  # no existence oracle

    async with c.engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO projects (id, team_id, name, repo_full_name)"
            " VALUES ('p', :t, 'p', 'l/p')"), {"t": alice["teamId"]})
        await conn.execute(text(
            "INSERT INTO agent_runs (id, project_id, state) VALUES ('run1', 'p', 'idle')"))
    r = await c.post(f"{S}/{sid}/runs", json={"runId": "run1"}, headers=ha)
    assert r.status_code in (200, 201), r.text
    r = await c.get(f"{S}/{sid}/resume", headers=ha)
    assert r.status_code == 200, r.text
    assert (await c.delete(f"{S}/{sid}/runs/run1", headers=ha)).status_code in (200, 204)
    for step in ("pause", "archive"):
        assert (await c.post(f"{S}/{sid}/{step}", headers=ha)).status_code == 200, step
    r = await c.post(f"{S}/{sid}/delete", json={"reason": "cleanup"}, headers=ha)
    assert r.status_code == 200 and r.json()["state"] == "deleted", r.text
    async with c.engine.begin() as conn:  # retention elapsed: purge is allowed
        await conn.execute(text(
            "UPDATE collab_sessions SET purge_after = now() - interval '1 day' WHERE id = :i"),
            {"i": sid})
    assert (await c.post(f"{S}/{sid}/purge", headers=ha)).status_code == 200

    # --- part 45: register (version bump), approve, select, foreign team sees nothing
    body = {"name": "Coder", "description": "writes code", "capabilities": ["code.python"],
            "toolPermissions": ["read_file"], "permissions": ["read", "comment"],
            "allowedPaths": ["src"], "networkAllow": [], "credentialRefs": [],
            "model": "m1", "fallbackModel": "m2", "toolFallbacks": {}, "maxDepth": 2,
            "maxChildren": 3, "maxBudgetUsd": "2.5"}
    r = await c.post(A, json=body, headers=ha)
    assert r.status_code == 201, r.text
    agent = r.json()["id"]
    r = await c.put(f"{A}/{agent}", json={**body, "description": "v2"}, headers=ha)
    assert r.status_code == 200, r.text
    r = await c.post(f"{A}/{agent}/approve", headers=ha)
    assert r.status_code == 200 and r.json()["state"] == "approved", r.text
    r = await c.post(f"{A}/select", json={}, headers=ha)
    assert r.status_code == 200 and r.json()["items"], r.text
    assert (await c.post(f"{A}/select", json={}, headers=hb)).json()["items"] == []
    assert (await c.get(f"{A}/{agent}", headers=hb)).status_code == 404

    # --- every audit row these routers wrote chains correctly on Postgres
    async with c.engine.connect() as conn:
        rep = await verify_chain(conn)
        actions = {a for (a,) in (await conn.execute(text("SELECT action FROM audit_log"))).all()}
    assert rep.ok, rep.problems
    assert {"session.created", "session.template_saved", "session.template_applied",
            "session.run_linked", "session.resumed", "session.deleted", "session.purged",
            "agent.registered", "agent.approved"} <= actions
