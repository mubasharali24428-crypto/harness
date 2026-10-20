"""Part 44.002/003 — approving a plan VERSION binds what the engine runs to the reviewed content.

A natively created or edited version has no legacy ``agent_plans`` row, so before this route it
could never be approved or executed. These tests use the real service, real migrations 0041, 0047
and 0048 on sqlite, and the real plans + legacy routers."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from harness_contracts.roles import Principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import collab, execution, plans
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.plan_gate import router as plan_gate_router
from harness_orchestrator.service import OrchestratorService
from tests.unit import test_execution_support as sup
from tests.unit.test_execution_support import (
    DDL,
    MIGRATIONS,
    T0,
    USERS,
    Bus,
    Engine,
    Env,
    err,
    statements,
)

CONTENT = {
    "goal": "Add a retry to the uploader",
    "steps": ["Read uploader.py", "Add retry loop", "Run the tests"],
    "tests": ["pytest tests/test_uploader.py"],
    "risks": ["Retry storms"],
    "filesLikelyToChange": ["src/uploader.py"],
    "forbiddenPaths": ["secrets/"],
    "expectedResult": "Uploads survive a flaky network",
    "estimatedCostUsd": 0.25,
}
ADD_STEP = [{"op": "add", "path": "/steps/-", "value": "Update the docs"}]


@pytest.fixture()
async def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    clock = {"t": T0}
    monkeypatch.setattr(collab, "db_now", lambda: clock["t"])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/x.db")
    migrations = [
        s
        for name in ("0041_sessions.sql", "0047_plans.sql", "0048_execution.sql")
        for s in statements((MIGRATIONS / name).read_text())
    ]
    async with engine.begin() as conn:
        for d in (*statements(DDL), *migrations):
            await conn.execute(text(d))
        for t in ("team-a", "team-b"):
            await conn.execute(text("INSERT INTO teams (id) VALUES (:t)"), {"t": t})
        for u, (t, role) in USERS.items():
            await conn.execute(
                text(
                    "INSERT INTO team_members (id, team_id, user_id, display_name, role)"
                    " VALUES (:i, :t, :u, :u, :r)"
                ),
                {"i": f"m-{u}", "t": t, "u": u, "r": role.value},
            )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    stub = Engine()
    svc = OrchestratorService(engine=stub, session_factory=factory, bus=Bus(factory, clock))
    monkeypatch.setattr(orch_api, "_service", svc)
    monkeypatch.setattr(execution, "_service", svc)
    monkeypatch.setattr(plans, "_service", svc)
    from harness_orchestrator import plan_gate

    plan_gate.configure_plan_gate(svc)
    app = FastAPI()
    for r in (orch_router, plans.router, plan_gate_router, execution.router):
        app.include_router(r)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        yield Env(client, who, factory, clock, svc, stub)
    await asyncio.gather(
        *[r["task"] for r in svc.runs.values() if r.get("task") is not None], return_exceptions=True
    )
    await asyncio.gather(*list(svc._bg_tasks), return_exceptions=True)
    svc.shutdown()
    await engine.dispose()


async def bare_run(env, rid="r1", team="team-a"):
    """A run with a project but no legacy plan: the only way to a plan is a native version."""
    await env.x("INSERT INTO projects (id, team_id) VALUES (:p, :t)", p=f"proj-{rid}", t=team)
    await env.x("INSERT INTO agent_runs (id, project_id) VALUES (:r, :p)", r=rid, p=f"proj-{rid}")
    return rid


async def native_version(env, rid="r1", user="lead", content=None, expected=0):
    body = {"content": content or CONTENT, "expectedVersion": expected}
    r = await env.post(user, f"/runs/{rid}/plan/versions", body)
    assert r.status_code == 201, r.text
    return r.json()


async def approve(env, user, n, rid="r1"):
    return await env.post(user, f"/runs/{rid}/plan/versions/{n}/approve")


async def test_a_native_version_is_approved_and_is_exactly_what_the_engine_runs(env):
    await bare_run(env)
    content = dict(CONTENT, forbiddenPaths=[])
    v = await native_version(env, content=content)
    assert v["version"] == 1 and v["source"] == "native"
    r = await approve(env, "boss", 1)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "approved" and body["source"] == "legacy" and body["legacyPlanId"]
    assert body["approvedSha256"] == body["contentSha256"] == v["contentSha256"]
    plan = await env.svc.get_plan("r1")  # the plan the engine takes
    assert plan.goal == content["goal"] and plan.steps == content["steps"]
    assert plan.estimatedCostUsd == 0.25
    assert env.svc.runs["r1"]["state_machine"].plan_approved is True
    await env.start("r1")
    await env.work("r1")
    assert env.engine.calls == ["r1"]  # it really ran
    (row,) = await env.audit("plan.approved")
    d = json.loads(row.detail)
    assert d["version"] == 1 and d["planId"] == body["legacyPlanId"]
    assert d["after"] == {"state": "approved"}
    assert "Add a retry" not in row.detail  # ids and digests only, never plan text


async def test_the_engine_gate_sees_the_approved_versions_forbidden_paths(env):
    await bare_run(env)
    await native_version(env)  # CONTENT declares forbiddenPaths=["secrets/"]
    assert (await approve(env, "boss", 1)).status_code == 200
    await env.start("r1")
    await env.work("r1")
    assert env.engine.calls == []  # this runtime cannot enforce forbidden paths: fail closed
    failed = await env.events("r1", "engine_failed")
    assert "forbidden paths" in json.loads(failed[-1].payload)["reason"]


async def test_an_accepted_edit_runs_the_edited_plan_not_the_original(env):
    await bare_run(env)
    await native_version(env)
    e = await env.post("lead", "/runs/r1/plan/versions/1/edits", {"patch": ADD_STEP})
    assert e.status_code == 201, e.text
    d = await env.post("eng2", f"/runs/r1/plan/versions/1/edits/{e.json()['id']}/decide",
                       {"decision": "accept"})
    assert d.status_code == 200, d.text
    err(await approve(env, "boss", 1), 409, "STALE_VERSION")  # v1 was superseded by the edit
    r = await approve(env, "boss", 2)
    assert r.status_code == 200, r.text
    plan = await env.svc.get_plan("r1")
    assert plan.steps == [*CONTENT["steps"], "Update the docs"]
    vs = (await env.get("boss", "/runs/r1/plan/versions")).json()
    states = [(v["version"], v["state"]) for v in vs["versions"]]
    assert states == [(1, "superseded"), (2, "approved")]
    assert vs["approvedVersion"] == 2


async def test_the_approved_edited_plan_survives_a_restart(env):
    await bare_run(env)
    await native_version(env)
    e = await env.post("lead", "/runs/r1/plan/versions/1/edits", {"patch": ADD_STEP})
    await env.post("eng2", f"/runs/r1/plan/versions/1/edits/{e.json()['id']}/decide",
                   {"decision": "accept"})
    assert (await approve(env, "boss", 2)).status_code == 200
    await env.flush()
    fresh = OrchestratorService(
        engine=Engine(), session_factory=env.factory, bus=Bus(env.factory, env.clock)
    )
    try:
        plan = await fresh.get_plan("r1")  # rehydrated from the event log and agent_plans
        assert plan.steps[-1] == "Update the docs"
        assert fresh.runs["r1"]["state_machine"].plan_approved is True
    finally:
        fresh.shutdown()


async def test_approving_twice_is_idempotent_and_audited_once(env):
    await bare_run(env)
    await native_version(env)
    first = await approve(env, "boss", 1)
    again = await approve(env, "boss", 1)
    assert first.status_code == again.status_code == 200
    assert again.json()["legacyPlanId"] == first.json()["legacyPlanId"]
    assert len(await env.audit("plan.approved")) == 1


async def test_two_approvers_at_once_make_one_decision(env):
    await bare_run(env)
    await native_version(env)
    a, b = await asyncio.gather(approve(env, "boss", 1), approve(env, "lead", 1))
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json()["legacyPlanId"] == b.json()["legacyPlanId"]
    assert len(await env.audit("plan.approved")) == 1
    rows = await env.q("SELECT id, status FROM agent_plans WHERE run_id = 'r1'")
    assert [r.status for r in rows] == ["approved"]  # no orphan plan row was left proposed


async def test_a_version_mirrored_from_the_legacy_plan_uses_that_plan(env):
    await env.new_run("r1", approved=False)
    await env.svc.propose_plan("r1", goal="legacy goal", project_id="proj-r1")
    legacy_id = env.svc.runs["r1"]["plan_id"]
    vs = (await env.get("lead", "/runs/r1/plan/versions")).json()
    assert vs["versions"][0]["legacyPlanId"] == legacy_id
    r = await approve(env, "boss", 1)
    assert r.status_code == 200 and r.json()["legacyPlanId"] == legacy_id
    assert env.svc.planner.is_approved(legacy_id)


async def test_the_legacy_plan_replaced_by_an_adopted_version_can_no_longer_be_approved(env):
    await env.new_run("r1", approved=False)
    await env.svc.propose_plan("r1", goal="legacy goal", project_id="proj-r1")
    old = env.svc.runs["r1"]["plan_id"]
    await env.get("lead", "/runs/r1/plan/versions")  # mirror it as v1
    await native_version(env, expected=1)  # v2 supersedes v1
    assert (await approve(env, "boss", 2)).status_code == 200
    with pytest.raises(KeyError, match="does not belong"):
        await env.svc.approve_plan("r1", old, "boss")  # what the legacy route calls
    assert env.svc.planner.is_approved(old) is False
    (row,) = await env.q("SELECT status FROM agent_plans WHERE id = :i", i=old)
    assert row.status == "rejected"  # and the mirror says so after a restart


async def test_roles_and_scope_are_enforced(env):
    await bare_run(env)
    await native_version(env)
    err(await approve(env, "view", 1), 403, "ROLE_DENIED")
    err(await approve(env, "guest", 1), 403, "ROLE_DENIED")
    r = await approve(env, "evil", 1)  # another team's admin: same 404 as an unknown run
    assert r.status_code == 404, r.text
    err(await approve(env, "boss", 9), 404, "VERSION_NOT_FOUND")
    assert env.svc.runs.get("r1", {}).get("plan_id") is None  # nothing was adopted by the refusals


async def test_an_approved_plan_locks_further_versions_and_cannot_be_adopted_over(env):
    await bare_run(env)
    await native_version(env)
    assert (await approve(env, "boss", 1)).status_code == 200
    r = await env.post("lead", "/runs/r1/plan/versions", {"content": CONTENT, "expectedVersion": 1})
    err(r, 409, "PLAN_LOCKED")
    other = dict(CONTENT, goal="a different goal")
    # even bypassing the lock check, the service refuses to replace a decided plan
    with pytest.raises(Exception, match="plan can no longer change"):
        await env.svc.adopt_plan_version("r1", other)
    assert (await env.svc.get_plan("r1")).goal == CONTENT["goal"]


async def test_the_new_route_is_classified_by_the_policy_gate():
    from harness_contracts.policy_enforce import ORCHESTRATOR_RULES, classify

    path = "/api/v1/orchestrator/runs/{runId}/plan/versions/{n}/approve"
    assert classify(ORCHESTRATOR_RULES, "POST", path) == "approve"


def test_support_module_is_the_one_the_migrations_come_from():
    assert sup.MIGRATIONS.name == "migrations" and (sup.MIGRATIONS / "0047_plans.sql").exists()
