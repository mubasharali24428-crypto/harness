"""Task 10: external agent adapter protocol v1 — observer lease, no authority, no oracle."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import adapters
from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal
from harness_orchestrator.service import OrchestratorService

DDL = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)",
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), state TEXT)",
    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36),"
    " user_id VARCHAR(36), role VARCHAR(16))",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, actor_member_id TEXT,"
    " actor_kind TEXT, action TEXT, target_type TEXT, target_id TEXT, detail TEXT, ts TIMESTAMP)",
    "CREATE TABLE run_adapter_leases (id VARCHAR(36) PRIMARY KEY, run_id TEXT, team_id TEXT,"
    " agent_id TEXT, protocol TEXT, capabilities TEXT, max_scope TEXT, pubkey_sha256 TEXT,"
    " digest TEXT, mode TEXT CHECK (mode = 'observer'), registered_by TEXT, created_at TIMESTAMP,"
    " UNIQUE (run_id, agent_id))",
)
USERS = {"lead": ("team-a", Role.ENGINEER), "view": ("team-a", Role.VIEWER),
         "evil": ("team-b", Role.ADMIN)}
REG = {"agentId": "ext-coder", "protocolVersion": "1", "capabilities": ["read_events"],
       "maxScope": ["src/api"], "pubkey": "k" * 44}
P = "/api/v1/orchestrator/runs"


class Bus:
    async def publish(self, *a, **k):
        return None

    async def ensure_run(self, *a, **k):
        return None


@pytest.fixture()
def as_(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/x.db")

    async def setup():
        async with engine.begin() as c:
            for d in DDL:
                await c.execute(text(d))
            await c.execute(text("INSERT INTO projects VALUES ('p', 'team-a')"))
            await c.execute(text("INSERT INTO agent_runs VALUES ('r1', 'p', 'running')"))
            for u, (t, r) in USERS.items():
                await c.execute(text("INSERT INTO team_members VALUES (:i, :t, :u, :r)"),
                                {"i": f"m-{u}", "t": t, "u": u, "r": r.value})

    asyncio.run(setup())
    monkeypatch.setattr(orch_api, "_service", OrchestratorService(
        session_factory=async_sessionmaker(engine, expire_on_commit=False), bus=Bus()))
    app = FastAPI()
    app.include_router(adapters.router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1])
    client = TestClient(app)

    def as_user(u):
        who["u"] = u
        return client

    yield as_user
    asyncio.run(engine.dispose())


def test_registration_yields_observer_lease_with_no_authority(as_):
    r = as_("lead").post(f"{P}/r1/adapters/register", json=REG)
    assert r.status_code == 201, r.text
    lease = r.json()
    assert lease["mode"] == "observer" and lease["authority"] == []
    assert lease["requestedScope"] == ["src/api"] and len(lease["digest"]) == 64
    assert "k" * 44 not in r.text  # only the key fingerprint is stored
    listed = as_("view").get(f"{P}/r1/adapters").json()["adapters"]
    assert [a["leaseId"] for a in listed] == [lease["leaseId"]]


@pytest.mark.parametrize("cap", ["approve", "grant_scope", "merge", "execute"])
def test_adapter_cannot_carry_authority_capabilities(as_, cap):
    r = as_("lead").post(f"{P}/r1/adapters/register", json={**REG, "capabilities": [cap]})
    assert r.status_code == 422


def test_scope_request_uses_traversal_rules_and_unknown_protocol_refused(as_):
    c = as_("lead")
    assert c.post(f"{P}/r1/adapters/register",
                  json={**REG, "maxScope": ["../etc"]}).status_code == 422
    assert c.post(f"{P}/r1/adapters/register",
                  json={**REG, "protocolVersion": "2"}).status_code == 422


def test_foreign_run_is_not_an_oracle_and_viewer_cannot_register(as_):
    evil = as_("evil")
    hit = evil.post(f"{P}/r1/adapters/register", json=REG)
    miss = evil.post(f"{P}/nope/adapters/register", json=REG)
    assert hit.status_code == miss.status_code == 404 and hit.json() == miss.json()
    assert as_("view").post(f"{P}/r1/adapters/register", json=REG).status_code == 403


def test_re_registration_cannot_silently_widen_scope(as_):
    c = as_("lead")
    assert c.post(f"{P}/r1/adapters/register", json=REG).status_code == 201
    wider = c.post(f"{P}/r1/adapters/register", json={**REG, "maxScope": ["src", "deploy"]})
    assert wider.status_code == 409
    assert c.get(f"{P}/r1/adapters").json()["adapters"][0]["requestedScope"] == ["src/api"]
