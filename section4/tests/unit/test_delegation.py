"""W28 S1 — delegate verb behind the kill-switch (lane A slice).

Gate chain shared with spawn (one implementation), W8 quotas enforced, and the
default state is inert: HARNESS_SUBAGENTS=1 is required to reach the ledger.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from harness_orchestrator import delegation
from tests.unit.agent_tree_support import (
    P,
    add_def_async,
    build,
    default_scope,
    rows,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_SUBAGENTS", raising=False)
    engine, factory, app, who, svc = asyncio.run(build(tmp_path, monkeypatch))
    app2 = app  # test_agent_tree mounts agent_tree.router; we add delegation's
    app2.include_router(delegation.router)

    def as_(user):
        who["u"] = user
        return TestClient(app2)

    def add_def(name="coder", **over):
        return asyncio.run(add_def_async(factory, name, **over))

    yield SimpleNamespace(
        as_=as_, f=factory, svc=svc, add_def=add_def, mp=monkeypatch, who=who
    )
    asyncio.run(engine.dispose())


def deleg(env, agent, run="r1", user="eng", **kw):
    body = {"agentId": agent, "title": "do a thing", "scope": default_scope(), **kw}
    return env.as_(user).post(f"{P}/{run}/delegations", json=body)


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["detail"]["error"]["code"] == code, r.text


def test_default_is_inert(env):
    agent = env.add_def()
    r = deleg(env, agent, requestId="x1")
    err(r, 403, "SUBAGENTS_DISABLED")
    assert rows(env.f, "SELECT count(*) as c FROM agent_assignments")[0]["c"] == 0


def test_flag_mints_one_child(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    r = deleg(env, agent, requestId="x2")
    assert r.status_code == 201, r.text
    out = r.json()
    assert len(out["children"]) == 1
    assignment = out["children"][0]
    assert assignment["duplicate"] is False
    assert rows(env.f, "SELECT run_id, parent_id FROM agent_assignments")[0]["run_id"] == "r1"
    # idempotent re-POST with the same request id
    r2 = deleg(env, agent, requestId="x2")
    assert r2.status_code == 201, r2.text
    assert r2.json()["children"][0]["duplicate"] is True
    assert r2.json()["minted"] == 0


def test_count_mints_bounded_children(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    r = deleg(env, agent, count=2, requestId="x3")
    assert r.status_code == 201, r.text
    titles = sorted(child["title"] for child in r.json()["children"])
    assert titles == ["do a thing (1/2)", "do a thing (2/2)"]
    assert rows(env.f, "SELECT count(*) as c FROM agent_assignments")[0]["c"] == 2


def test_count_above_cap_refused(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    r = deleg(env, agent, count=5, requestId="x4")
    assert r.status_code == 422


def test_unknown_parent_assignment_is_not_found(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    r = deleg(env, agent, parentAssignmentId="nope", requestId="x5")
    err(r, 404, "NOT_FOUND")


def test_denial_leaves_no_rows(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    r = deleg(env, agent, budgetUsd="1000", requestId="x6")
    assert r.status_code == 422
    assert rows(env.f, "SELECT count(*) as c FROM agent_assignments")[0]["c"] == 0


def test_viewer_cannot_delegate(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    r = deleg(env, agent, count=1, requestId="x7", user="view")
    err(r, 403, "ROLE_DENIED")


def test_two_agent_task_one_cancelled_no_orphan(env):
    """W28 exit: two delegated children, one cancelled, then the parent; nothing stays live."""
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    root = deleg(env, agent, requestId="t-root").json()["children"][0]
    kids = deleg(env, agent, count=2, budgetUsd="0.2", requestId="t-kids",
                 parentAssignmentId=root["id"]).json()["children"]
    assert len(kids) == 2
    c = env.as_("eng")
    assert c.post(f"{P}/r1/agents/{kids[0]['id']}/cancel").json()["state"] == "cancelled"
    assert c.post(f"{P}/r1/agents/{root['id']}/cancel").json()["state"] == "cancelled"
    live = rows(env.f, "SELECT id FROM agent_assignments WHERE state NOT IN ('cancelled','done','failed')")
    assert live == []
    assert c.get(f"{P}/r1/agents").json()["pool"]["reservedUsd"] == "0"


# --------------------------------------------------------------------------- S1b-1
# A delegated child is a real run: it goes through the normal plan-approval path, so it
# waits for a human approval (option 1, owner decision 2026-11-05), has its own run id
# (and so its own engine workspace), and is stopped when its assignment is cancelled.


def _child_run(env, assignment_id):
    rows_ = rows(env.f, f"SELECT child_run_id FROM agent_assignments WHERE id = '{assignment_id}'")
    return rows_[0]["child_run_id"]


def _state(env, run_id):
    return env.svc.runs[run_id]["state_machine"].state.value


def test_delegated_child_gets_its_own_run_awaiting_approval(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    child = deleg(env, agent, requestId="s1b-1").json()["children"][0]
    run_id = _child_run(env, child["id"])
    assert run_id and run_id != "r1"
    assert child["childRunId"] == run_id
    assert _state(env, run_id) == "awaiting_plan_approval"  # never auto-approved


def test_each_child_gets_a_distinct_run(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    kids = deleg(env, agent, count=2, requestId="s1b-2").json()["children"]
    runs_ = {_child_run(env, k["id"]) for k in kids}
    assert len(runs_) == 2 and None not in runs_


def test_repost_reuses_the_child_run(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    first = deleg(env, agent, requestId="s1b-3").json()["children"][0]
    again = deleg(env, agent, requestId="s1b-3").json()["children"][0]
    assert again["duplicate"] is True and again["childRunId"] == first["childRunId"]
    assert set(env.svc.runs) - {"r1"} == {first["childRunId"]}  # no second child run


def test_cancelling_the_assignment_stops_the_child_run(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    root = deleg(env, agent, requestId="s1b-4a").json()["children"][0]
    kid = deleg(env, agent, requestId="s1b-4b", budgetUsd="0.2",
                parentAssignmentId=root["id"]).json()["children"][0]
    root_run, kid_run = root["childRunId"], kid["childRunId"]
    assert env.as_("eng").post(f"{P}/r1/agents/{root['id']}/cancel").status_code == 200
    assert _state(env, root_run) == "done" and _state(env, kid_run) == "done"  # no orphan runs


def test_planning_failure_leaves_no_live_assignment(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()

    async def boom(*a, **k):
        raise RuntimeError("planner down")

    env.mp.setattr(env.svc, "propose_plan", boom)
    r = deleg(env, agent, requestId="s1b-5")
    err(r, 502, "CHILD_RUN_FAILED")
    live = rows(env.f, "SELECT id FROM agent_assignments"
                       " WHERE state NOT IN ('cancelled','done','failed')")
    assert live == []


def test_delegation_router_is_mounted_by_the_deployed_app():
    """S1a shipped the router but never mounted it, so the verb 404'd on a real stack."""
    from pathlib import Path

    services = Path(__file__).resolve().parents[3] / "deploy" / "runners" / "services.py"
    assert "delegation.router" in services.read_text()
