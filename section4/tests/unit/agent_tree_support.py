"""Shared helpers for the agent_tree suites (lane A, part 45). No tests live here.

The agent tables come from the REAL migration 0044 (section1), applied to sqlite with its
comments stripped. The surrounding tables (projects, agent_runs, teams, team_members,
audit_log and the 0039 policy tables) are minimal sqlite mirrors, like the other unit suites.
"""

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import agent_tree
from harness_orchestrator import api as orch_api
from harness_orchestrator.service import OrchestratorService

MIGRATIONS = Path(__file__).resolve().parents[3] / "section1" / "migrations"
MIGRATION = MIGRATIONS / "0044_agents.sql"
# 0075 adds agent_assignments.child_run_id (W28 S1b); applied after 0044.
MIGRATION_FILES = (MIGRATION, MIGRATIONS / "0075_assignment_child_run.sql")


def migration_statements() -> list[str]:
    sql = "\n".join(ln for f in MIGRATION_FILES for ln in f.read_text().splitlines()
                    if not ln.strip().startswith("--"))
    return [s.strip() for s in sql.split(";") if s.strip()]


BASE_DDL = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " name VARCHAR(200) NOT NULL DEFAULT 'x')",
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
    " state VARCHAR(40))",
    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36), display_name VARCHAR(200) NOT NULL DEFAULT 'x',"
    " member_type VARCHAR(16) NOT NULL DEFAULT 'human',"
    " role VARCHAR(16) NOT NULL DEFAULT 'engineer')",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " actor_member_id VARCHAR(36), actor_kind VARCHAR(16) NOT NULL,"
    " action VARCHAR(80) NOT NULL, target_type VARCHAR(60) NOT NULL,"
    " target_id VARCHAR(80) NOT NULL, detail TEXT NOT NULL DEFAULT '{}',"
    " ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
)
ORG_TEAMS = "CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, org_id VARCHAR(36))"
PLAIN_TEAMS = "CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY)"
POLICY_DDL = (
    "CREATE TABLE policy_documents (org_id VARCHAR(36), level VARCHAR(16),"
    " scope_id VARCHAR(64), doc TEXT)",
    "CREATE TABLE policy_custom_roles (org_id VARCHAR(36), name VARCHAR(40), permissions TEXT)",
    "CREATE TABLE org_members (org_id VARCHAR(36), user_id VARCHAR(36), role VARCHAR(32),"
    " expires_at TIMESTAMP, removed_at TIMESTAMP)",
    "CREATE TABLE policy_assignments (org_id VARCHAR(36), user_id VARCHAR(36),"
    " level VARCHAR(16), scope_id VARCHAR(64), role VARCHAR(32), expires_at TIMESTAMP)",
    "CREATE TABLE policy_emergency_grants (id VARCHAR(36), org_id VARCHAR(36),"
    " user_id VARCHAR(36), level VARCHAR(16), scope_id VARCHAR(64), permissions TEXT,"
    " starts_at TIMESTAMP, expires_at TIMESTAMP, status VARCHAR(16))",
)

USERS = {  # user -> (team, role)
    "eng": ("team-a", Role.ENGINEER),
    "eng2": ("team-a", Role.ENGINEER),
    "boss": ("team-a", Role.ADMIN),
    "view": ("team-a", Role.VIEWER),
    "evil": ("team-b", Role.ADMIN),
}
P = "/api/v1/orchestrator/runs"


class Bus:
    async def publish(self, *a, **k):
        return None

    async def ensure_run(self, *a, **k):
        return None


def allow_budget(monkeypatch, allowed: bool = True, reason: str = "") -> None:
    async def fake(*a, **k):
        return SimpleNamespace(allowed=allowed, reason=reason, degraded=False)

    monkeypatch.setattr(agent_tree, "check_budget", fake)


async def build(path, monkeypatch, *, org: str | None = None, org_column: bool = True):
    """(engine, factory, app, who, service) with two runs (r1, r2) of team-a."""
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    allow_budget(monkeypatch)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}/agents.db")
    async with engine.begin() as conn:
        for d in (*BASE_DDL, ORG_TEAMS if org_column else PLAIN_TEAMS, *POLICY_DDL,
                  *migration_statements()):
            await conn.execute(text(d))
        await conn.execute(text("INSERT INTO projects (id, team_id) VALUES ('p', 'team-a')"))
        await conn.execute(text("INSERT INTO agent_runs (id, project_id)"
                                " VALUES ('r1', 'p'), ('r2', 'p')"))
        if org_column:
            await conn.execute(text("INSERT INTO teams (id, org_id) VALUES ('team-a', :o),"
                                    " ('team-b', NULL)"), {"o": org})
        for u, (t, role) in USERS.items():
            await conn.execute(
                text("INSERT INTO team_members (id, team_id, user_id, role) VALUES (:i,:t,:u,:r)"),
                {"i": f"m-{u}", "t": t, "u": u, "r": role.value})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=Bus())
    monkeypatch.setattr(orch_api, "_service", service)
    agent_tree.configure_agent_tree(service)
    app = FastAPI()
    app.include_router(agent_tree.router)
    who = {"u": "eng"}
    app.dependency_overrides[orch_api.get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1])
    return engine, factory, app, who, service


DEF_DEFAULTS = {
    "permissions": ["read", "comment", "steer"],
    "tool_permissions": ["read_file", "write_file", "search", "run_tests"],
    "allowed_paths": ["src", "tests"],
    "network_allow": ["api.example.com"],
    "credential_refs": ["GITHUB_READ"],
    "model": "m-primary", "fallback_model": "m-fallback",
    "tool_fallbacks": {"search": "read_file"},
    "max_depth": 2, "max_children": 3, "max_budget_microusd": 2_000_000,
    "state": "approved", "team_id": "team-a", "version": 1,
}
_JSON = ("permissions", "tool_permissions", "allowed_paths", "network_allow",
         "credential_refs", "tool_fallbacks")


async def add_def_async(factory, name: str = "coder", **over) -> str:
    v = {**DEF_DEFAULTS, **over}
    did = str(uuid.uuid4())
    params = {k: (json.dumps(x) if k in _JSON else x) for k, x in v.items()}
    async with factory() as db:
        await db.execute(
            text("INSERT INTO agent_definitions (id, team_id, name, name_key, version, state,"
                 " capabilities, tool_permissions, permissions, allowed_paths, network_allow,"
                 " credential_refs, model, fallback_model, tool_fallbacks, max_depth,"
                 " max_children, max_budget_microusd, created_by, created_at, updated_at)"
                 " VALUES (:id,:team_id,:n,:k,:version,:state,'[]',:tool_permissions,"
                 ":permissions,:allowed_paths,:network_allow,:credential_refs,:model,"
                 ":fallback_model,:tool_fallbacks,:max_depth,:max_children,"
                 ":max_budget_microusd,'seed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
            {**params, "id": did, "n": name, "k": name.lower()})
        await db.commit()
    return did


def default_scope(**over) -> dict:
    return {"permissions": ["read"], "tools": ["read_file"], "paths": ["src/a"],
            "network": [], "credentialRefs": [], **over}


def spawn_body(agent_id: str, parent: str | None = None, budget: str = "0.5", **over) -> dict:
    scope = over.pop("scope", default_scope())
    return {"agentId": agent_id, "title": "do a thing", "scope": scope, "budgetUsd": budget,
            **({"parentAssignmentId": parent} if parent else {}), **over}


def rows(factory, sql: str, **kw):
    async def go():
        async with factory() as db:
            return (await db.execute(text(sql), kw)).mappings().all()

    return asyncio.run(go())


def audit_actions(factory) -> list[str]:
    return [r["action"] for r in rows(factory, "SELECT action FROM audit_log ORDER BY seq")]
