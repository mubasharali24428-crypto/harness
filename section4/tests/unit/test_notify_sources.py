"""Part 47: the call sites that put rows in an inbox (Session 5 lane N): approval requested,
handoff offered/decided in collab.py, run failed/blocked/review-failed in service.py.

Drives the real routes on sqlite with the real migration 0046. A notification problem must never
break the approval, the handoff or the run."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.events import EventType
from harness_contracts.roles import Principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.service import OrchestratorService
from tests.unit.test_collab_backend import DDL, PROPOSAL, USERS, Bus
from tests.unit.test_notify import migration_statements

P = "/api/v1/orchestrator/runs"
EXTRA = (
    "CREATE TABLE workspaces (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)",
    "CREATE TABLE collab_sessions (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " workspace_id VARCHAR(36))",
    "CREATE TABLE session_runs (run_id VARCHAR(36) PRIMARY KEY, session_id VARCHAR(36) NOT NULL)",
)
# the shared DDL's projects table has no workspace_id yet (migration 0040 adds it)
SCHEMA = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " name VARCHAR(200) NOT NULL DEFAULT 'x', workspace_id VARCHAR(36))",
    *DDL[1:], *EXTRA,
)


def run(coro):
    return asyncio.run(coro)


async def seed(engine):
    async with engine.begin() as conn:
        for stmt in (*SCHEMA, *migration_statements()):
            await conn.execute(text(stmt))
        await conn.execute(text("INSERT INTO projects (id, team_id) VALUES ('p', 'team-a')"))
        await conn.execute(text("INSERT INTO agent_runs (id, project_id) VALUES ('r1', 'p')"))
        for u, (t, role) in USERS.items():
            await conn.execute(
                text("INSERT INTO team_members (id, team_id, user_id, role) VALUES (:i,:t,:u,:r)"),
                {"i": f"m-{u}", "t": t, "u": u, "r": role.value})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    monkeypatch.delenv("HARNESS_ALLOW_SELF_APPROVAL", raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/n.db")
    run(seed(engine))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=Bus())
    monkeypatch.setattr(orch_api, "_service", service)
    app = FastAPI()
    app.include_router(orch_router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1])
    client = TestClient(app)

    def as_(user):
        who["u"] = user
        return client

    yield as_, factory
    run(engine.dispose())


def inbox(factory, **where) -> list:
    clause = " AND ".join(f"{k}=:{k}" for k in where) or "1=1"

    async def go():
        async with factory() as db:
            return (await db.execute(
                text(f"SELECT * FROM notification_inbox WHERE {clause} ORDER BY created_at, id"),
                where)).mappings().all()

    return run(go())


def lead_run(as_):
    assert as_("lead").post(f"{P}/r1/leader").status_code == 200
    as_("eng2").post(f"{P}/r1/join")
    as_("view").post(f"{P}/r1/join")


# ------------------------------------------------------------------ approval requested


def test_approval_request_tells_the_leader_only_and_never_carries_the_proposal(env):
    as_, f = env
    lead_run(as_)
    r = as_("eng2").post(f"{P}/r1/approvals", json=PROPOSAL)
    assert r.status_code in (200, 201), r.text
    aid = r.json()["id"]
    (row,) = inbox(f)
    assert (row["user_id"], row["kind"], row["summary_code"]) == (
        "lead", "approval", "approval_requested")
    assert (row["source_kind"], row["source_id"], row["run_id"]) == ("approval", aid, "r1")
    assert row["urgent"] and row["dedupe_key"] == f"approval:{aid}" and row["team_id"] == "team-a"
    blob = json.dumps({k: str(v) for k, v in dict(row).items()})
    assert PROPOSAL["summary"] not in blob
    for path in PROPOSAL["paths"]:
        assert path not in blob
    # nobody else is told: not the requester, the observer or another team's admin
    assert inbox(f, user_id="eng2") == [] and inbox(f, user_id="view") == []
    assert inbox(f, user_id="evil") == []


def test_a_leader_requesting_their_own_approval_is_not_notified(env):
    as_, f = env
    lead_run(as_)
    assert as_("lead").post(f"{P}/r1/approvals", json=PROPOSAL).status_code in (200, 201)
    assert inbox(f) == []


def test_approval_burst_groups_into_one_row(env):
    as_, f = env
    lead_run(as_)
    for _ in range(3):
        assert as_("eng2").post(f"{P}/r1/approvals", json=PROPOSAL).status_code in (200, 201)
    (row,) = inbox(f)
    assert row["count"] == 3


# ------------------------------------------------------------------ handoff


def test_handoff_offer_and_decision_notify_the_other_party(env):
    as_, f = env
    lead_run(as_)
    h = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"}).json()
    (offer,) = inbox(f, user_id="eng2")
    assert (offer["kind"], offer["summary_code"], offer["source_id"]) == (
        "handoff", "handoff_offered", h["handoffId"])
    assert inbox(f, user_id="lead") == []
    ok = as_("eng2").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": True})
    assert ok.status_code == 200
    (done,) = inbox(f, user_id="lead")
    assert (done["kind"], done["summary_code"]) == ("handoff", "handoff_accepted")
    assert len(inbox(f)) == 2


def test_handoff_rejection_is_reported_as_rejected(env):
    as_, f = env
    lead_run(as_)
    h = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"}).json()
    as_("eng2").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": False})
    (done,) = inbox(f, user_id="lead")
    assert done["summary_code"] == "handoff_rejected"


# ------------------------------------------------------------------ never breaks the caller


def test_a_broken_inbox_never_breaks_approvals_or_handoffs(env):
    as_, f = env
    lead_run(as_)

    async def drop():
        async with f() as db:
            await db.execute(text("DROP TABLE notification_inbox"))
            await db.commit()

    run(drop())
    r = as_("eng2").post(f"{P}/r1/approvals", json=PROPOSAL)
    assert r.status_code in (200, 201), r.text
    assert len(as_("lead").get(f"{P}/r1/approvals").json()["approvals"]) == 1
    h = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"})
    assert h.status_code in (200, 201), h.text
    ok = as_("eng2").post(f"{P}/r1/handoff/{h.json()['handoffId']}", json={"accept": True})
    assert ok.status_code == 200 and ok.json()["leader"] == "eng2"


# ------------------------------------------------------------------ run failed / blocked (service)


async def _service_env():
    engine = create_async_engine("sqlite+aiosqlite://")
    await seed(engine)
    async with engine.begin() as conn:
        for u, role in (("lead", "leader"), ("eng2", "participant"), ("view", "observer")):
            await conn.execute(text("INSERT INTO run_participants (run_id, team_id, user_id, role)"
                                    " VALUES ('r1', 'team-a', :u, :r)"), {"u": u, "r": role})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=Bus())
    service.runs["r1"] = {"projectId": "p"}
    return engine, factory, service


@pytest.mark.parametrize("event,kind,code", [
    (EventType.ENGINE_FAILED, "failure", "run_failed"),
    (EventType.REVIEW_FAILED, "failure", "review_failed"),
    (EventType.BLOCKED_AWAITING_CLARIFICATION, "blocked", "run_blocked"),
])
async def test_run_problem_events_notify_leader_and_participants_not_observers(event, kind, code):
    engine, factory, service = await _service_env()
    service._emit(event, {"runId": "r1", "reason": "secret detail that must not travel"})
    await asyncio.gather(*list(service._bg_tasks))
    async with factory() as db:
        got = (await db.execute(text("SELECT * FROM notification_inbox ORDER BY user_id"))
               ).mappings().all()
    assert [(r["user_id"], r["kind"], r["summary_code"]) for r in got] == [
        ("eng2", kind, code), ("lead", kind, code)]
    assert all(r["source_kind"] == "run" and r["source_id"] == "r1" for r in got)
    assert not any(r["urgent"] for r in got)
    assert "secret detail" not in json.dumps({k: str(v) for r in got for k, v in dict(r).items()})
    await engine.dispose()


async def test_other_run_events_and_runs_without_participants_notify_nobody():
    engine, factory, service = await _service_env()
    service._emit(EventType.ENGINE_COMPLETED, {"runId": "r1"})
    service.runs["r2"] = {"projectId": "p"}
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO agent_runs (id, project_id) VALUES ('r2', 'p')"))
    service._emit(EventType.ENGINE_FAILED, {"runId": "r2", "reason": "x"})
    await asyncio.gather(*list(service._bg_tasks))
    async with factory() as db:
        assert (await db.execute(text("SELECT COUNT(*) FROM notification_inbox"))).scalar() == 0
    await engine.dispose()


async def test_a_broken_inbox_never_breaks_the_run_event():
    engine, factory, service = await _service_env()
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE notification_inbox"))
    service._emit(EventType.ENGINE_FAILED, {"runId": "r1", "reason": "x"})
    results = await asyncio.gather(*list(service._bg_tasks), return_exceptions=True)
    assert not [r for r in results if isinstance(r, BaseException)]
    await engine.dispose()
