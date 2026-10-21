"""Part 38 (session 12): org policy binds the realtime sockets at upgrade and on every recheck,
the org routes are engine-gated, and the policy reads of an org-linked request are cached until
a write clears them. Scenarios are shared with test_policy_sockets_pg.py (live PostgreSQL)."""

import re

import pytest
from harness_contracts import Principal, policy_enforce
from harness_contracts.policy_enforce import EXEMPT, IDENTITY_RULES, classify
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from ws_driver import MiniWs, WsRejected

from harness_identity import policy
from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.eventbus import CLOSE_ACCESS_REVOKED
from harness_identity.models import Base, Team

ORG = "org-acme"
SETTINGS = dict(
    master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False
)


async def login(c, email):
    r = await c.post("/api/v1/auth/login", json={"email": email})
    assert r.status_code == 200, r.text
    j = r.json()
    return j["token"], j["principal"]


async def link_org(c, principal):
    """Alice's team becomes an org-linked team she owns (raw rows; the org API is untested here)."""
    async with c.db() as s:
        await s.execute(
            text("UPDATE teams SET org_id = :o WHERE id = :t"),
            {"o": ORG, "t": principal["teamId"]},
        )
        await s.execute(
            text("INSERT INTO org_members (org_id, user_id, role, created_at)"
                 " VALUES (:o, :u, 'owner', CURRENT_TIMESTAMP)"),
            {"o": ORG, "u": principal["userId"]},
        )
        await s.execute(
            text("INSERT INTO projects (id, team_id, name) VALUES ('proj-acme', :t, 'demo')"),
            {"t": principal["teamId"]},
        )
        await s.commit()
    return ORG


async def put_policy(c, token, org, rules):
    """Write the org policy through the policy API (step-up first, as the owner's UI does)."""
    headers = {"Authorization": f"Bearer {token}"}
    r = await c.post("/api/v1/stepup/verify", json={"code": "000000"}, headers=headers)
    assert r.status_code == 200, r.text
    url = f"/api/v1/policy/orgs/{org}/documents/org/{org}"
    r = await c.put(url, json={"doc": {"rules": rules}}, headers=headers)
    assert r.status_code == 200, r.text


def deny_read(principal, overridable):
    return [{"id": "no-read", "effect": "deny", "permissions": ["read"],
             "subjects": [f"user:{principal['userId']}"], "overridable": overridable}]


async def closed_code(ws, timeout=5):
    """Read until the server closes the open socket; return the close code."""
    while True:
        try:
            await ws.recv_json(timeout=timeout)
        except WsRejected as e:
            return e.code


@pytest.fixture
async def env(tmp_path):
    # A file database: an in-memory pool can lose its tables when a socket task is cancelled.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sockets.db'}")
    db = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = make_app(db, Settings(**SETTINGS))
    async with db() as s:
        connection = await s.connection()
        await connection.run_sync(policy._md.create_all)
        for ddl in (
            "CREATE TABLE IF NOT EXISTS organizations "
            "(id TEXT PRIMARY KEY, name TEXT, slug TEXT, status TEXT, created_at TEXT, "
            "require_sso BOOLEAN DEFAULT FALSE, require_mfa BOOLEAN DEFAULT FALSE, "
            "require_dual_approval BOOLEAN DEFAULT FALSE, "
            "event_retention_days INTEGER, audit_retention_days INTEGER, "
            "allowed_domains TEXT DEFAULT '[]', execution_disabled BOOLEAN NOT NULL DEFAULT FALSE)",
            "CREATE TABLE IF NOT EXISTS org_members (org_id TEXT, user_id TEXT, role TEXT, "
            "expires_at TEXT, created_at TEXT, removed_at TEXT)",
            "ALTER TABLE teams ADD COLUMN org_id VARCHAR(36)",
        ):
            await s.execute(text(ddl))
        await s.execute(
            text("INSERT INTO organizations (id, name, slug, status, created_at) "
                 "VALUES (:o, 'Acme', 'acme', 'active', '2026-01-01 00:00:00')"),
            {"o": ORG},
        )
        await s.commit()

    async def verifier(request, db_session, principal, code):
        return code == "000000"

    app.state.stepup_verifier = verifier
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.app_, c.db, c.engine = app, db, engine
        yield c
    await engine.dispose()


def test_realtime_sockets_are_classified_and_org_routes_are_not_exempt():
    assert classify(IDENTITY_RULES, "GET", "/api/v1/ws/p1") == "read"
    assert classify(IDENTITY_RULES, "GET", "/api/v1/ws/p1/runs/r1/collab") == "read"
    paths = make_app(None, Settings(**SETTINGS)).openapi()["paths"]
    org_ops = [
        (m.upper(), re.sub(r"\{[^}]+\}", "x1", p))
        for p, ops in paths.items() if p.startswith("/api/v1/orgs")
        for m in ops if m in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(org_ops) >= 20  # a vacuous walk would pass the rest
    unclassified = [
        f"{m} {p}" for m, p in org_ops if classify(IDENTITY_RULES, m, p) in (EXEMPT, None)
    ]
    assert unclassified == []


async def test_upgrade_is_refused_on_a_non_overridable_deny(env):
    token, principal = await login(env, "alice@example.com")
    org = await link_org(env, principal)
    await put_policy(env, token, org, deny_read(principal, overridable=False))
    ws = await MiniWs(env.app_, f"/api/v1/ws/proj-acme?token={token}").__aenter__()
    try:
        assert not ws.accepted
        assert ws.close_code == 4403
    finally:
        await ws.__aexit__(None, None, None)


async def test_open_socket_closes_on_the_recheck_after_a_deny_lands(env, monkeypatch):
    monkeypatch.setenv("WS_REVALIDATE_SECONDS", "0.05")
    token, principal = await login(env, "alice@example.com")
    org = await link_org(env, principal)
    async with MiniWs(env.app_, f"/api/v1/ws/proj-acme?token={token}") as ws:
        assert ws.accepted
        await put_policy(env, token, org, deny_read(principal, overridable=False))
        assert await closed_code(ws) == CLOSE_ACCESS_REVOKED


async def test_foreign_project_is_refused_before_any_policy_is_read(env):
    token_a, principal_a = await login(env, "alice@example.com")
    await link_org(env, principal_a)
    token_b, _ = await login(env, "bob@example.com")  # bob's team owns no proj-acme
    async with MiniWs(env.app_, f"/api/v1/ws/proj-acme?token={token_b}") as ws:
        assert not ws.accepted
        assert ws.close_code == 4404  # the same code an unknown project gets: no oracle


async def test_a_policy_write_clears_the_cache_before_the_ttl(env, monkeypatch):
    monkeypatch.setattr(policy_enforce, "POLICY_CACHE_TTL", 60.0)  # expiry cannot explain it
    token, principal = await login(env, "alice@example.com")
    org = await link_org(env, principal)
    async with MiniWs(env.app_, f"/api/v1/ws/proj-acme?token={token}") as ws:
        assert ws.accepted  # warms the cache
    assert policy_enforce._cache, "the upgrade should have cached its policy reads"
    await put_policy(env, token, org, deny_read(principal, overridable=False))
    async with MiniWs(env.app_, f"/api/v1/ws/proj-acme?token={token}") as ws:
        assert not ws.accepted
        assert ws.close_code == 4403


async def test_an_orm_write_to_a_policy_table_clears_the_cache_too(env, monkeypatch):
    monkeypatch.setattr(policy_enforce, "POLICY_CACHE_TTL", 60.0)
    token, principal = await login(env, "alice@example.com")
    await link_org(env, principal)
    async with MiniWs(env.app_, f"/api/v1/ws/proj-acme?token={token}") as ws:
        assert ws.accepted  # warms the cache
    assert policy_enforce._cache
    async with env.db() as s:  # a unit-of-work flush: it never passes through Session.execute()
        team = await s.get(Team, principal["teamId"])
        team.name = "renamed"
        await s.commit()
    assert not policy_enforce._cache


async def test_repeat_decisions_within_the_ttl_do_not_reread_policy(env, monkeypatch):
    monkeypatch.setattr(policy_enforce, "POLICY_CACHE_TTL", 60.0)
    token, principal = await login(env, "alice@example.com")
    await link_org(env, principal)
    reads = []

    def count(conn, cursor, statement, params, context, many):
        if "policy_documents" in statement and statement.lstrip().upper().startswith("SELECT"):
            reads.append(statement)

    engine = env.engine.sync_engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        async with env.db() as s:
            await policy_enforce.socket_decision(s, _principal(principal), "/api/v1/ws/proj-acme")
            first = len(reads)
            await policy_enforce.socket_decision(s, _principal(principal), "/api/v1/ws/proj-acme")
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert first == 1 and len(reads) == 1


def _principal(p):
    return Principal.model_validate(p)
