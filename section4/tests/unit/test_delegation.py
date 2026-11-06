"""W28 S1 — delegate verb behind the kill-switch (lane A slice).

Gate chain shared with spawn (one implementation), W8 quotas enforced, and the
default state is inert: HARNESS_SUBAGENTS=1 is required to reach the ledger.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

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

    async def _ledger():  # the orchestrator DB has cost_ledger in production; SQLite tests do not
        async with factory() as db:
            await db.execute(text("CREATE TABLE IF NOT EXISTS cost_ledger"
                                  " (run_id VARCHAR(36), cost_usd NUMERIC)"))
            await db.execute(text(
                "CREATE TABLE IF NOT EXISTS run_intent_versions"
                " (run_id VARCHAR(36), version INTEGER,"
                " team_id VARCHAR(36), goal TEXT, scope_paths TEXT, permissions TEXT,"
                " changed_fields TEXT, updated_by VARCHAR(36), updated_at TIMESTAMP,"
                " request_id VARCHAR(64), PRIMARY KEY (run_id, version))"))
            await db.commit()

    asyncio.run(_ledger())

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
    live = rows(env.f, "SELECT id FROM agent_assignments"
                       " WHERE state NOT IN ('cancelled','done','failed')")
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
    assert child["childPlanId"]  # the id a human needs to approve the child's plan
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


# --------------------------------------------------------------------------- S1b-2
# The synthesis is a read-only view for a human: who owns what, what each child returned, and
# where siblings' paths overlap. It merges nothing.


def _synthesis(env, run="r1", user="eng"):
    return env.as_(user).get(f"{P}/{run}/synthesis")


def test_synthesis_lists_children_with_owner_state_and_result(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    kids = deleg(env, agent, count=2, requestId="s2-1").json()["children"]
    r = _synthesis(env)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reviewRequired"] is True and body["autoMerge"] is False
    by_id = {c["assignmentId"]: c for c in body["children"]}
    assert set(by_id) == {k["id"] for k in kids}
    one = by_id[kids[0]["id"]]
    assert one["childRunId"] == kids[0]["childRunId"]
    assert one["agentName"] and one["delegatedBy"] and one["scope"]["paths"]
    assert one["runStatus"] == "not_started" and one["result"] is None  # nothing ran yet


def test_synthesis_reports_a_childs_final_response(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    kid = deleg(env, agent, requestId="s2-2").json()["children"][0]

    async def done(run_id):
        return {"status": "completed", "finalResponse": "wrote src/a.txt"}

    env.mp.setattr(env.svc, "get_engine_result", done)
    child = _synthesis(env).json()["children"][0]
    assert child["assignmentId"] == kid["id"]
    assert child["runStatus"] == "completed" and child["result"] == "wrote src/a.txt"


def test_synthesis_flags_overlapping_sibling_paths(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    kids = deleg(env, agent, count=2, budgetUsd="0.2", requestId="s2-3").json()["children"]
    body = _synthesis(env).json()  # both children hold the same paths from default_scope()
    assert len(body["conflicts"]) == 1
    assert set(body["conflicts"][0]["assignmentIds"]) == {k["id"] for k in kids}
    assert body["conflicts"][0]["paths"]


def test_synthesis_ignores_cancelled_children_for_conflicts(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    kids = deleg(env, agent, count=2, budgetUsd="0.2", requestId="s2-4").json()["children"]
    assert env.as_("eng").post(f"{P}/r1/agents/{kids[0]['id']}/cancel").status_code == 200
    body = _synthesis(env).json()
    assert body["conflicts"] == []
    assert {c["assignmentId"]: c["state"] for c in body["children"]}[kids[0]["id"]] == "cancelled"


def test_synthesis_is_empty_for_a_run_without_delegations(env):
    assert _synthesis(env).json()["children"] == []


def test_synthesis_spend_is_the_runs_ledger_cost(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    kids = deleg(env, agent, count=2, budgetUsd="0.2", requestId="s2-6").json()["children"]

    async def spend():
        async with env.f() as db:
            for cost in ("0.000500", "0.000562"):
                await db.execute(text("INSERT INTO cost_ledger (run_id, cost_usd) VALUES (:r, :c)"),
                                 {"r": kids[0]["childRunId"], "c": cost})
            await db.commit()

    asyncio.run(spend())
    by_id = {c["assignmentId"]: c for c in _synthesis(env).json()["children"]}
    assert by_id[kids[0]["id"]]["spentUsd"] == "0.001062"  # summed over the run's ledger rows
    assert by_id[kids[1]["id"]]["spentUsd"] == "0"  # no ledger rows yet


def test_a_stop_envelope_carries_the_replay_tombstone(env):
    """Any stop reason is tombstoned (`stopped`), else replay reads the run as resumable PAUSED."""
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    kid = deleg(env, agent, requestId="s2-7").json()["children"][0]
    seen = []
    env.svc.on_event = lambda etype, payload: seen.append((etype.value, payload))
    assert env.as_("eng").post(f"{P}/r1/agents/{kid['id']}/cancel").status_code == 200
    stops = [p for t, p in seen if t == "paused" and p.get("runId") == kid["childRunId"]]
    assert len(stops) == 1 and stops[0]["reason"] == "assignment_cancelled"
    assert stops[0]["stopped"] is True


# --------------------------------------------------------------------------- scope binding
# The engine confines a run to its intent's scopePaths (collab.intent_scope_paths), and a run with
# no intent paths is unconfined. So a delegated child's paths must become its run's intent.


def test_child_run_intent_is_bound_to_the_assignment_scope(env):
    from harness_orchestrator import collab

    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    kid = deleg(env, agent, requestId="s2-8").json()["children"][0]
    bound = asyncio.run(collab.intent_scope_paths(env.f, kid["childRunId"]))
    assert bound == kid["scope"]["paths"] and bound  # what the engine's shell gate will enforce


def test_scope_without_paths_is_refused_because_it_would_be_unconfined(env):
    env.mp.setenv("HARNESS_SUBAGENTS", "1")
    agent = env.add_def()
    scope = {**default_scope(), "paths": []}
    r = env.as_("eng").post(f"{P}/r1/delegations", json={
        "agentId": agent, "title": "x", "scope": scope, "requestId": "s2-9"})
    err(r, 422, "SCOPE_UNBINDABLE")
    assert rows(env.f, "SELECT count(*) as c FROM agent_assignments")[0]["c"] == 0


# ------------------------------------------------------------------- A3 characterisation
# _overlaps (delegation.py:166) feeds /synthesis' conflicts. Pin its edges so the first
# change to _covered (agent_tree.py:204) that breaks a sibling-scope check is loud.
# Test-only: no production function is changed here.


@pytest.mark.parametrize(("a", "b", "expected"), [
    pytest.param(["src"], ["src/a.py"], ["src/a.py"], id="parent-and-child-file"),
    pytest.param(["src"], ["src2"], [], id="string-prefix-is-not-a-path-parent"),
    pytest.param(["src/"], ["src"], ["src", "src/"], id="trailing-slash"),
    pytest.param(["src/a"], ["src/b"], [], id="disjoint-siblings"),
    pytest.param(["src"], ["src"], ["src"], id="identical"),
    pytest.param([], [], [], id="both-empty"),
    pytest.param(["src"], [], [], id="one-empty"),
    pytest.param(["src", "src"], ["src"], ["src"], id="duplicates"),
    pytest.param(["src"], ["src/a/b/c.txt"], ["src/a/b/c.txt"], id="nested-three-deep"),
    pytest.param(["src with space"], ["src with space/file.txt"],
                 ["src with space/file.txt"], id="path-with-a-space"),
])
def test_overlaps_characterisation(a, b, expected):
    assert delegation._overlaps(a, b) == expected


def test_overlaps_dot_slash_is_the_same_directory():
    assert delegation._overlaps(["./src"], ["src"]) != []


@pytest.mark.parametrize(("a", "b", "expected"), [
    pytest.param(["src//"], ["src"], ["src", "src//"], id="doubled-slash"),
    pytest.param(["src2"], ["src"], [], id="prefix-lookalike-is-not-an-overlap"),
    pytest.param(["/src"], ["src"], [], id="absolute-does-not-overlap-relative"),
    pytest.param(["../src"], ["src"], [], id="dotdot-is-not-normalised"),
    pytest.param([], ["src"], [], id="empty-side"),
    pytest.param([""], ["src"], [], id="empty-string-side"),
])
def test_overlaps_noise_and_non_noise_edges(a, b, expected):
    assert delegation._overlaps(a, b) == expected


# D1 step 2: _overlaps returns paths from EITHER side that fall under a path of
# the other side. A .-style scope is the whole workspace, so a src path is
# covered by it and must appear in the union.
@pytest.mark.parametrize(("a", "b", "expected"), [
    pytest.param(["."], ["src"], ["src"], id="dot-ceiling-vs-src"),
    pytest.param(["src"], ["."], ["src"], id="src-vs-dot-ceiling"),
    pytest.param(["./"], ["src/x"], ["src/x"], id="dot-slash-ceiling-vs-nested"),
])
def test_overlaps_workspace_root_scope(a, b, expected):
    assert delegation._overlaps(a, b) == expected
