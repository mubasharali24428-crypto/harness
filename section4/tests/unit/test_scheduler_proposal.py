"""Sprint 2 Task 15: W31 tier proposals on a run — flag-off default, admin-only, proposal-only."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import scheduler_proposal
from harness_orchestrator.api import get_principal
from harness_orchestrator.service import OrchestratorService

DDL = (
    "CREATE TABLE projects (id TEXT PRIMARY KEY, team_id TEXT NOT NULL)",
    "CREATE TABLE agent_runs (id TEXT PRIMARY KEY, project_id TEXT, state TEXT)",
    "CREATE TABLE team_members (id TEXT PRIMARY KEY, team_id TEXT, user_id TEXT, role TEXT)",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, actor_member_id TEXT,"
    " actor_kind TEXT, action TEXT, target_type TEXT, target_id TEXT, detail TEXT, ts TIMESTAMP)",
    "CREATE TABLE scheduler_proposals (id TEXT PRIMARY KEY, run_id TEXT, team_id TEXT,"
    " schema_tag TEXT, proposal TEXT, violations TEXT, created_by TEXT, created_at TIMESTAMP)",
)
USERS = {"boss": ("team-a", Role.ADMIN), "eng": ("team-a", Role.ENGINEER),
         "evil": ("team-b", Role.ADMIN)}
ITEMS = {"items": [{"id": "m1", "bytes": 100, "hits_per_day": 9, "billing_approved": False}]}
P = "/api/v1/orchestrator/runs"


def fake_w31(ttl: int):
    """Stands in for w31/tier_ttl.py (OR-Tools is not a section4 dependency)."""
    def solve(items):
        return {"schema": "w31-tier-ttl/1", "status": "optimal", "proposalOnly": True,
                "requiresHumanApproval": True,
                "placements": [{"id": i["id"], "tier": "hot", "ttl_hours": ttl} for i in items]}

    def validate(items, proposal):
        paid = {i["id"] for i in items if i["billing_approved"]}
        return [f"{p['id']}: 48h needs billing_approved" for p in proposal["placements"]
                if p["ttl_hours"] == 48 and p["id"] not in paid]

    return SimpleNamespace(solve=solve, validate=validate)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv(scheduler_proposal.FLAG_ENV, raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/s.db")

    async def setup():
        async with engine.begin() as c:
            for d in DDL:
                await c.execute(text(d))
            await c.execute(text("INSERT INTO projects VALUES ('p', 'team-a')"))
            await c.execute(text("INSERT INTO agent_runs VALUES ('r1', 'p', 'executing')"))
            for u, (t, r) in USERS.items():
                await c.execute(text("INSERT INTO team_members VALUES (:i, :t, :u, :r)"),
                                {"i": f"m-{u}", "t": t, "u": u, "r": r.value})

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(orch_api, "_service", OrchestratorService(session_factory=factory))
    app = FastAPI()
    app.include_router(scheduler_proposal.router)
    who = {"u": "boss"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1])
    client = TestClient(app)

    def as_(u):
        who["u"] = u
        return client

    def state():
        async def go():
            async with factory() as db:
                q = text("SELECT state FROM agent_runs WHERE id='r1'")
                return (await db.execute(q)).scalar()
        return asyncio.run(go())

    yield as_, monkeypatch, state
    asyncio.run(engine.dispose())


def test_off_by_default(env):
    as_, _, _ = env
    r = as_("boss").post(f"{P}/r1/scheduler-proposals", json=ITEMS)
    assert r.status_code == 404 and r.json()["detail"]["error"]["code"] == "FEATURE_OFF"


def test_proposal_attached_run_untouched_and_48h_without_billing_flagged(env):
    as_, mp, state = env
    mp.setenv(scheduler_proposal.FLAG_ENV, "1")
    mp.setattr(scheduler_proposal, "load_tier_ttl", lambda: fake_w31(48))
    r = as_("boss").post(f"{P}/r1/scheduler-proposals", json=ITEMS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["valid"] is False and body["violations"] == ["m1: 48h needs billing_approved"]
    assert body["proposalOnly"] and body["requiresHumanApproval"]
    assert state() == "executing"  # nothing dispatched, run untouched
    latest = as_("boss").get(f"{P}/r1/scheduler-proposals").json()["latest"]
    assert latest["schema"] == "w31-tier-ttl/1"
    assert latest["proposal"]["placements"][0]["id"] == "m1"


def test_admin_only_and_foreign_run_is_404(env):
    as_, mp, _ = env
    mp.setenv(scheduler_proposal.FLAG_ENV, "1")
    mp.setattr(scheduler_proposal, "load_tier_ttl", lambda: fake_w31(24))
    assert as_("eng").get(f"{P}/r1/scheduler-proposals").status_code == 403
    hit = as_("evil").get(f"{P}/r1/scheduler-proposals")
    miss = as_("evil").get(f"{P}/x/scheduler-proposals")
    assert hit.status_code == miss.status_code == 404 and hit.json() == miss.json()


def test_missing_solver_is_503_not_a_fallback(env, tmp_path):
    as_, mp, _ = env
    mp.setenv(scheduler_proposal.FLAG_ENV, "1")
    mp.setenv(scheduler_proposal.ROOT_ENV, str(tmp_path / "nowhere"))
    r = as_("boss").post(f"{P}/r1/scheduler-proposals", json=ITEMS)
    assert r.status_code == 503 and r.json()["detail"]["error"]["code"] == "SCHEDULER_UNAVAILABLE"
    # the real experiment module needs OR-Tools, which section4 does not install -> also 503
    mp.setenv(scheduler_proposal.ROOT_ENV,
              str(Path(__file__).resolve().parents[3] / "experiments/w31-scheduler"))
    scheduler_proposal._load.cache_clear()
    assert as_("boss").post(f"{P}/r1/scheduler-proposals", json=ITEMS).status_code == 503
