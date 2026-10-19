"""Session 4 (parts 41, 42, 45) on real Postgres: the full 0001->latest chain plus the presence,
participation, control and agent-tree routers. SQLite cannot show the ON CONFLICT upserts, the
per-run advisory lock in capacity_ok, partial-index races or compare-and-set UPDATEs under real
concurrent connections. Opt-in (HARNESS_TEST_PG); builds and drops its own throwaway database."""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from harness_contracts.roles import Principal, Role
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import agent_tree, control, participation, presence
from harness_orchestrator import api as orch_api
from harness_orchestrator.service import OrchestratorService

_ROOT = Path(__file__).resolve().parents[3]
P = "/api/v1/orchestrator"

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG session 4 tests skipped",
)

_spec = importlib.util.spec_from_file_location(
    "harness_migrate", _ROOT / "deploy" / "runners" / "migrate.py"
)
assert _spec is not None and _spec.loader is not None  # noqa: S101
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

USERS = {f"u{i}": ("t1", Role.ENGINEER) for i in range(8)} | {"boss": ("t1", Role.ADMIN)}


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


class Bus:
    async def publish(self, *a, **k):
        return None

    async def ensure_run(self, *a, **k):
        return None


@pytest.fixture
async def env(monkeypatch):
    import asyncpg

    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)

    async def allow(*a, **k):
        return SimpleNamespace(allowed=True, reason="", degraded=False)

    monkeypatch.setattr(agent_tree, "check_budget", allow)
    dbname = f"harness_s4_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(f"{base}/{dbname}")
    try:
        for path in sorted((_ROOT / "section1" / "migrations").glob("*.sql")):
            for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                if stmt.strip():
                    await conn.execute(stmt)
        await conn.execute("INSERT INTO teams (id, name) VALUES ('t1', 's4')")
        await conn.execute(
            "INSERT INTO projects (id, team_id, name, repo_full_name)"
            " VALUES ('p', 't1', 'p', 'l/p')"
        )
        await conn.execute(
            "INSERT INTO agent_runs (id, project_id, state) VALUES ('r1', 'p', 'idle')"
        )
        for u, (t, role) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, email, name, is_active) VALUES ($1, $2, $3, true)",
                u, f"{u}@example.com", f"Name {u}",
            )
            await conn.execute(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                " VALUES ($1, $2, $3, $4, 'human', $5)", f"m-{u}", t, u, f"Name {u}", role.value,
            )
    finally:
        await conn.close()
    engine = create_async_engine(f"{base}/{dbname}".replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=Bus())
    monkeypatch.setattr(orch_api, "_service", service)
    for mod, fn in ((presence, "configure_presence"), (participation, "configure_participation"),
                    (control, "configure_control"), (agent_tree, "configure_agent_tree")):
        getattr(mod, fn)(service)
    app = FastAPI()
    for r in (orch_api.router, presence.router, participation.router, control.router,
              agent_tree.router):
        app.include_router(r)
    def principal(request: Request) -> Principal:
        user = request.headers["x-user"]  # per-request identity so concurrent calls stay distinct
        return Principal(userId=user, teamId=USERS[user][0], role=USERS[user][1])

    app.dependency_overrides[orch_api.get_principal] = principal
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.factory = factory
        yield client
    await engine.dispose()
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    finally:
        await admin.close()


def h(user):
    return {"x-user": user}


async def scalar(c, sql):
    async with c.factory() as db:
        return (await db.execute(text(sql))).scalar_one()


async def test_presence_upsert_and_participant_cap_on_real_postgres(env):
    c = env
    # ON CONFLICT upsert: the same device twice leaves one row holding the newer state
    assert (await c.post(f"{P}/runs/r1/join", headers=h("u0"))).status_code == 200
    for state in ("online", "idle"):
        r = await c.post(f"{P}/runs/r1/presence/heartbeat", headers=h("u0"),
                         json={"deviceId": "d1", "state": state})
        assert r.status_code == 200, r.text
    assert await scalar(c, "SELECT state FROM presence_records WHERE user_id='u0'") == "idle"

    # cap of 3 with u0 already seated, 6 simultaneous joins: the advisory lock admits exactly 2 more
    r = await c.put(f"{P}/presence-settings", headers=h("boss"),
                    json={"mode": "full", "maxParticipants": 3})
    assert r.status_code == 200, r.text
    res = await asyncio.gather(
        *(c.post(f"{P}/runs/r1/join", headers=h(f"u{i}")) for i in range(1, 7))
    )
    codes = sorted(x.status_code for x in res)
    assert codes == [200, 200, 409, 409, 409, 409], [x.text for x in res]
    refused = {x.json()["detail"]["error"]["code"] for x in res if x.status_code == 409}
    assert refused == {"SESSION_FULL"}
    assert await scalar(c, "SELECT count(*) FROM run_participants WHERE run_id='r1'") == 3


async def test_lease_and_intent_races_on_real_postgres(env):
    c = env
    for i in range(4):
        assert (await c.post(f"{P}/runs/r1/join", headers=h(f"u{i}"))).status_code == 200
    res = await asyncio.gather(*(c.post(f"{P}/runs/r1/control/acquire", headers=h(f"u{i}"))
                                 for i in range(4)))
    codes = sorted(x.status_code for x in res)
    assert codes.count(200) == 1 and all(k in (200, 409) for k in codes), [x.text for x in res]
    assert await scalar(c, "SELECT count(*) FROM control_leases WHERE run_id='r1'") == 1
    leaders = "SELECT count(*) FROM run_participants WHERE run_id='r1' AND role='leader'"
    assert await scalar(c, leaders) == 1
    holder = await scalar(c, "SELECT holder_user_id FROM control_leases WHERE run_id='r1'")

    # simultaneous intent edits from the same base version: one winner, the rest 409
    res = await asyncio.gather(*(c.put(f"{P}/runs/r1/intent", headers=h(holder),
                                       json={"expectedVersion": 0, "goal": f"goal {i}"})
                                 for i in range(4)))
    codes = sorted(x.status_code for x in res)
    assert codes.count(200) == 1 and codes.count(409) == 3, [x.text for x in res]
    assert await scalar(c, "SELECT count(*) FROM run_intent_versions WHERE run_id='r1'") == 1


async def test_agent_spawn_limits_under_real_concurrency(env):
    c = env
    did = str(uuid.uuid4())
    async with c.factory() as db:
        await db.execute(text(
            "INSERT INTO agent_definitions (id, team_id, name, name_key, version, state,"
            " capabilities,"
            " tool_permissions, permissions, allowed_paths, network_allow, credential_refs, model,"
            " fallback_model, tool_fallbacks, max_depth, max_children, max_budget_microusd,"
            " created_by, created_at, updated_at)"
            " VALUES (:id,'t1','coder','coder',1,'approved','[]',"
            " CAST(:tp AS jsonb), CAST(:pm AS jsonb), CAST(:ap AS jsonb), '[]'::jsonb, '[]'::jsonb,"
            " 'm1', 'm2', '{}'::jsonb, 2, 3, 2000000, 'seed', now(), now())"),
            {"id": did, "tp": '["read_file"]', "pm": '["read","comment","steer"]', "ap": '["src"]'})
        await db.commit()
    body = {"agentId": did, "title": "t", "budgetUsd": "1.0",
            "scope": {"permissions": ["read"], "tools": ["read_file"], "paths": ["src/a"],
                      "network": [], "credentialRefs": []}}
    root = await c.post(f"{P}/runs/r1/agents/spawn", headers=h("u0"), json=body)
    assert root.status_code == 201, root.text
    child = {**body, "budgetUsd": "0.05", "parentAssignmentId": root.json()["id"]}
    res = await asyncio.gather(*(c.post(f"{P}/runs/r1/agents/spawn", headers=h("u0"), json=child)
                                 for _ in range(8)))
    codes = sorted(x.status_code for x in res)
    assert codes == [201] * 3 + [409] * 5, [x.text for x in res]
    root_children = "SELECT children_count FROM agent_assignments WHERE parent_id IS NULL"
    assert await scalar(c, root_children) == 3
