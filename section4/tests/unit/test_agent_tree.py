"""Part 45 (lane A): spawn limits, scope/delegation bounds, lifecycle, fallback, tool summaries.

Drives the real routes of ``agent_tree.py`` on sqlite with the real migration 0044 SQL.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role

from harness_orchestrator import agent_tree
from tests.unit.agent_tree_support import (
    P,
    add_def_async,
    allow_budget,
    audit_actions,
    build,
    default_scope,
    rows,
    spawn_body,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k in ("HARNESS_AGENT_UNAVAILABLE_MODELS", "HARNESS_AGENT_RUN_MAX_AGENTS",
              "HARNESS_AGENT_RUN_BUDGET_USD", "HARNESS_AGENT_MAX_DEPTH",
              "HARNESS_AGENT_MAX_CHILDREN"):
        monkeypatch.delenv(k, raising=False)
    engine, factory, app, who, svc = asyncio.run(build(tmp_path, monkeypatch))
    client = TestClient(app)

    def as_(user):
        who["u"] = user
        return client

    def add_def(name="coder", **over):
        return asyncio.run(add_def_async(factory, name, **over))

    yield SimpleNamespace(as_=as_, f=factory, svc=svc, add_def=add_def, mp=monkeypatch)
    asyncio.run(engine.dispose())


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["detail"]["error"]["code"] == code, r.text


def spawn(env, agent, run="r1", user="eng", **kw):
    return env.as_(user).post(f"{P}/{run}/agents/spawn", json=spawn_body(agent, **kw))


def ok_spawn(env, agent, **kw):
    r = spawn(env, agent, **kw)
    assert r.status_code == 201, r.text
    return r.json()


def call(env, aid, user="eng", run="r1", tool="read_file", **kw):
    body = {"tool": tool, "summary": "did it", "outcome": "ok", **kw}
    return env.as_(user).post(f"{P}/{run}/agents/{aid}/tool-calls", json=body)


# ------------------------------------------------------------------ spawn basics


def test_spawn_root_records_scope_budget_and_audit(env):
    d = env.add_def()
    a = ok_spawn(env, d)
    assert a["state"] == "active" and a["depth"] == 0 and a["parentId"] is None
    assert a["scope"]["tools"] == ["read_file"] and a["delegatedBy"] == "eng"
    assert a["budget"]["allocatedUsd"] == "0.5" and a["model"]["used"] == "m-primary"
    assert a["limits"] == {"maxDepth": 2, "maxChildren": 3, "liveChildren": 0}
    assert audit_actions(env.f) == ["agent.spawned"]
    detail = json.loads(rows(env.f, "SELECT detail FROM audit_log")[0]["detail"])
    assert detail["userId"] == "eng" and detail["agentId"] == d and detail["depth"] == 0
    listing = env.as_("view").get(f"{P}/r1/agents").json()  # viewers can read the tree
    assert listing["summary"]["byState"] == {"active": 1}
    assert listing["pool"]["reservedUsd"] == "0.5" and listing["pool"]["totalSpawned"] == 1


def test_spawn_is_idempotent_on_request_id(env):
    d = env.add_def()
    first = ok_spawn(env, d, requestId="req-1")
    again = spawn(env, d, requestId="req-1")
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert again.json()["duplicate"] is True
    assert len(rows(env.f, "SELECT id FROM agent_assignments")) == 1
    assert audit_actions(env.f).count("agent.spawned") == 1


def test_only_approved_agents_of_own_team_and_roles(env):
    proposed = env.add_def("p1", state="proposed")
    retired = env.add_def("p2", state="retired")
    foreign = env.add_def("p3", team_id="team-b")
    err(spawn(env, proposed), 409, "AGENT_NOT_APPROVED")
    err(spawn(env, retired), 409, "AGENT_NOT_APPROVED")
    err(spawn(env, foreign), 404, "NOT_FOUND")  # same answer as an unknown id
    err(spawn(env, "nope"), 404, "NOT_FOUND")
    good = env.add_def()
    err(spawn(env, good, user="view"), 403, "ROLE_DENIED")
    err(spawn(env, good, user="evil"), 404, "NOT_FOUND")  # foreign run: no oracle
    acts = audit_actions(env.f)
    assert acts.count("agent.spawn_refused") == 2 and "agent.spawned" not in acts


# ------------------------------------------------------------------ scope / delegation


@pytest.mark.parametrize("scope", [
    default_scope(tools=["delete_everything"]),
    default_scope(paths=["secrets/prod"]),
    default_scope(network=["evil.example.com"]),
    default_scope(credentialRefs=["PROD_ADMIN"]),
])
def test_scope_beyond_definition_is_denied_and_audited(env, scope):
    d = env.add_def()
    err(spawn(env, d, scope=scope), 403, "SCOPE_EXCEEDS_AGENT")
    assert audit_actions(env.f) == ["agent.delegation_denied"]


def test_permissions_beyond_agent_ceiling_or_forbidden_names(env):
    d = env.add_def(permissions=["read"])
    err(spawn(env, d, scope=default_scope(permissions=["steer"])), 403, "SCOPE_EXCEEDS_AGENT")
    for bad in (["approve"], ["administer"], []):
        err(spawn(env, d, scope=default_scope(permissions=bad)), 422, "VALIDATION_ERROR")


def test_path_traversal_and_bad_shapes_rejected(env):
    d = env.add_def()
    for paths in (["../etc"], ["/abs"], ["src/../../x"], ["a\\b"]):
        err(spawn(env, d, scope=default_scope(paths=paths)), 422, "VALIDATION_ERROR")
    r = env.as_("eng").post(f"{P}/r1/agents/spawn",
                            json={**spawn_body(d), "unknown": 1})
    assert r.status_code == 422


def test_path_prefix_is_segment_aware(env):
    d = env.add_def(allowed_paths=["src"])
    err(spawn(env, d, scope=default_scope(paths=["srcevil/x"])), 403, "SCOPE_EXCEEDS_AGENT")
    assert spawn(env, d, scope=default_scope(paths=["src", "src/deep/file.py"])).status_code == 201


def test_child_scope_cannot_exceed_parent_scope(env):
    d = env.add_def()
    parent = ok_spawn(env, d, scope=default_scope(tools=["read_file"], paths=["src/a"]))
    err(spawn(env, d, parent=parent["id"], scope=default_scope(tools=["write_file"])),
        403, "SCOPE_EXCEEDS_PARENT")
    err(spawn(env, d, parent=parent["id"], scope=default_scope(paths=["src/b"])),
        403, "SCOPE_EXCEEDS_PARENT")
    err(spawn(env, d, parent=parent["id"], scope=default_scope(permissions=["comment"])),
        403, "SCOPE_EXCEEDS_PARENT")
    child = ok_spawn(env, d, parent=parent["id"], budget="0.1",
                     scope=default_scope(paths=["src/a/sub"]))
    assert child["depth"] == 1 and child["parentId"] == parent["id"]


def test_missing_permissions_uses_policy_engine_role_defaults(env):
    async def go():
        async with env.f() as db:
            viewer = Principal(userId="view", teamId="team-a", role=Role.VIEWER)
            eng = Principal(userId="eng", teamId="team-a", role=Role.ENGINEER)
            return (await agent_tree.missing_permissions(db, viewer, ["read", "steer"], "r1", "p"),
                    await agent_tree.missing_permissions(db, eng, ["read", "steer"], "r1", "p"))

    viewer_missing, eng_missing = asyncio.run(go())
    assert viewer_missing == ["steer"] and eng_missing == []


def test_team_without_org_column_still_evaluates(tmp_path, monkeypatch):
    engine, factory, _, _, _ = asyncio.run(build(tmp_path, monkeypatch, org_column=False))

    async def go():
        async with factory() as db:
            eng = Principal(userId="eng", teamId="team-a", role=Role.ENGINEER)
            return await agent_tree.missing_permissions(db, eng, ["steer"], "r1", "p")

    assert asyncio.run(go()) == []
    asyncio.run(engine.dispose())


def test_org_policy_deny_blocks_delegation_beyond_the_users_rights(tmp_path, monkeypatch):
    engine, factory, app, who, _ = asyncio.run(build(tmp_path, monkeypatch, org="org1"))
    doc = {"rules": [{"id": "no-steer", "effect": "deny", "permissions": ["steer"],
                      "subjects": ["role:engineer"]}]}

    async def seed():
        async with factory() as db:
            from sqlalchemy import text
            await db.execute(text("INSERT INTO policy_documents (org_id, level, scope_id, doc)"
                                  " VALUES ('org1', 'org', 'org1', :d)"), {"d": json.dumps(doc)})
            await db.commit()
        return await add_def_async(factory, "coder")

    d = asyncio.run(seed())
    client = TestClient(app)
    who["u"] = "eng"
    steer = default_scope(permissions=["read", "steer"])
    err(client.post(f"{P}/r1/agents/spawn", json=spawn_body(d, scope=steer)),
        403, "DELEGATION_DENIED")
    assert rows(factory, "SELECT action FROM audit_log")[0]["action"] == "agent.delegation_denied"
    assert client.post(f"{P}/r1/agents/spawn", json=spawn_body(d)).status_code == 201  # read ok
    who["u"] = "boss"  # the admin is not hit by the engineer-only deny
    assert client.post(f"{P}/r1/agents/spawn", json=spawn_body(d, scope=steer)).status_code == 201
    asyncio.run(engine.dispose())


# ------------------------------------------------------------------ limits


def test_depth_limit(env):
    d = env.add_def(max_depth=1)
    root = ok_spawn(env, d)
    child = ok_spawn(env, d, parent=root["id"], budget="0.1")
    err(spawn(env, d, parent=child["id"], budget="0.01"), 409, "SPAWN_DEPTH_EXCEEDED")
    assert audit_actions(env.f)[-1] == "agent.spawn_refused"


def test_global_depth_cap_wins_over_definition(env):
    env.mp.setenv("HARNESS_AGENT_MAX_DEPTH", "0")
    d = env.add_def(max_depth=5)
    root = ok_spawn(env, d)
    assert root["limits"]["maxDepth"] == 0
    err(spawn(env, d, parent=root["id"], budget="0.1"), 409, "SPAWN_DEPTH_EXCEEDED")


def test_children_limit_counts_live_children_only(env):
    d = env.add_def(max_children=2)
    root = ok_spawn(env, d, budget="1.0")
    kids = [ok_spawn(env, d, parent=root["id"], budget="0.1") for _ in range(2)]
    err(spawn(env, d, parent=root["id"], budget="0.1"), 409, "SPAWN_CHILDREN_EXCEEDED")
    assert env.as_("eng").post(f"{P}/r1/agents/{kids[0]['id']}/cancel").status_code == 200
    ok_spawn(env, d, parent=root["id"], budget="0.1")  # a slot is free again
    err(spawn(env, d, parent=root["id"], budget="0.1"), 409, "SPAWN_CHILDREN_EXCEEDED")


def test_agent_with_zero_children_may_not_spawn(env):
    d = env.add_def(max_children=0)
    root = ok_spawn(env, d)
    err(spawn(env, d, parent=root["id"], budget="0.1"), 409, "SPAWN_NOT_ALLOWED")


def test_budget_limits_parent_pool_and_definition_max(env):
    env.mp.setenv("HARNESS_AGENT_RUN_BUDGET_USD", "1.5")
    d = env.add_def()
    root = ok_spawn(env, d, budget="1.0")
    ok_spawn(env, d, parent=root["id"], budget="0.6")
    err(spawn(env, d, parent=root["id"], budget="0.6"), 409, "SPAWN_BUDGET_EXCEEDED")
    err(spawn(env, d, budget="1.0"), 409, "SPAWN_BUDGET_EXCEEDED")  # run pool is 1.5
    err(spawn(env, d, budget="2.5"), 422, "BUDGET_EXCEEDS_AGENT_MAX")  # definition max is 2.0
    pool = env.as_("eng").get(f"{P}/r1/agents").json()["pool"]
    assert pool["totalSpawned"] == 2 and pool["reservedUsd"] == "1"  # refusals changed nothing


def test_run_agent_count_limit(env):
    env.mp.setenv("HARNESS_AGENT_RUN_MAX_AGENTS", "2")
    d = env.add_def()
    ok_spawn(env, d, budget="0.1")
    ok_spawn(env, d, budget="0.1")
    err(spawn(env, d, budget="0.1"), 409, "SPAWN_RUN_LIMIT")
    ok_spawn(env, d, run="r2", budget="0.1")  # the pool is per run


def test_team_budget_gate_refuses_spawn(env):
    d = env.add_def()
    allow_budget(env.mp, allowed=False, reason="cap reached")
    err(spawn(env, d), 409, "TEAM_BUDGET_EXHAUSTED")
    assert audit_actions(env.f) == ["agent.spawn_refused"]


# ------------------------------------------------------------------ lifecycle


def tree(env):
    d = env.add_def(max_depth=3)
    root = ok_spawn(env, d, budget="1.0")
    kid = ok_spawn(env, d, parent=root["id"], budget="0.4")
    grand = ok_spawn(env, d, parent=kid["id"], budget="0.1")
    return d, root, kid, grand


def state(env, aid):
    return rows(env.f, "SELECT state, pause_reason FROM agent_assignments WHERE id=:i",
                i=aid)[0]


def test_pause_resume_cascade_and_idempotence(env):
    _, root, kid, grand = tree(env)
    c = env.as_("eng")
    r = c.post(f"{P}/r1/agents/{root['id']}/pause", json={"reason": "lunch"})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert state(env, kid["id"])["pause_reason"] == "parent_paused"
    assert state(env, grand["id"])["state"] == "paused"
    assert c.post(f"{P}/r1/agents/{root['id']}/pause").json()["changed"] is False
    err(c.post(f"{P}/r1/agents/{kid['id']}/resume"), 409, "PARENT_NOT_ACTIVE")
    assert c.post(f"{P}/r1/agents/{root['id']}/resume").json()["changed"] is True
    assert {state(env, x["id"])["state"] for x in (root, kid, grand)} == {"active"}
    acts = audit_actions(env.f)
    assert acts.count("agent.paused") == 3 and acts.count("agent.resumed") == 3


def test_cancel_cascades_rolls_budget_up_and_is_idempotent(env):
    _, root, kid, grand = tree(env)
    assert call(env, kid["id"], costUsd="0.1").status_code == 201
    c = env.as_("eng")
    r = c.post(f"{P}/r1/agents/{root['id']}/cancel", json={"reason": "wrong plan"})
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert {state(env, x["id"])["state"] for x in (root, kid, grand)} == {"cancelled"}
    pool = c.get(f"{P}/r1/agents").json()["pool"]
    assert pool["reservedUsd"] == "0" and pool["spentUsd"] == "0.1"  # spend survives the cancel
    assert c.post(f"{P}/r1/agents/{root['id']}/cancel").json()["changed"] is False
    err(c.post(f"{P}/r1/agents/{root['id']}/pause"), 409, "ILLEGAL_TRANSITION")
    err(call(env, kid["id"]), 409, "ASSIGNMENT_NOT_ACTIVE")
    assert audit_actions(env.f).count("agent.cancelled") == 3
    ok_spawn(env, env.add_def("fresh"), budget="1.0")  # reservation was released


def test_lifecycle_is_team_and_run_scoped(env):
    d = env.add_def()
    a = ok_spawn(env, d)
    for verb in ("pause", "resume", "cancel"):
        err(env.as_("evil").post(f"{P}/r1/agents/{a['id']}/{verb}"), 404, "NOT_FOUND")
        err(env.as_("eng").post(f"{P}/r2/agents/{a['id']}/{verb}"), 404, "NOT_FOUND")
    err(env.as_("view").post(f"{P}/r1/agents/{a['id']}/pause"), 403, "ROLE_DENIED")
    err(env.as_("evil").get(f"{P}/r1/agents"), 404, "NOT_FOUND")
    assert env.as_("eng").get(f"{P}/r2/agents").json()["items"] == []


def test_reassign_to_another_approved_agent(env):
    old = env.add_def("old", max_children=3)
    new = env.add_def("new", max_children=1, tool_permissions=["read_file"], model="m-new")
    narrow = env.add_def("narrow", tool_permissions=["search"])
    unapproved = env.add_def("unapp", state="proposed")
    a = ok_spawn(env, old)
    c = env.as_("eng")
    url = f"{P}/r1/agents/{a['id']}/reassign"
    err(c.post(url, json={"agentId": narrow}), 403, "SCOPE_EXCEEDS_AGENT")
    err(c.post(url, json={"agentId": unapproved}), 409, "AGENT_NOT_APPROVED")
    err(c.post(url, json={"agentId": "nope"}), 404, "NOT_FOUND")
    assert c.post(url, json={"agentId": old}).json()["changed"] is False
    r = c.post(url, json={"agentId": new, "reason": "cheaper"})
    assert r.status_code == 200 and r.json()["agentName"] == "new"
    assert r.json()["limits"]["maxChildren"] == 1 and r.json()["model"]["used"] == "m-new"
    wide = env.add_def("wide", max_children=5)
    assert c.post(url, json={"agentId": wide}).json()["limits"]["maxChildren"] == 1  # never grows
    detail = json.loads(rows(env.f, "SELECT detail FROM audit_log WHERE action='agent.reassigned'"
                             " ORDER BY seq")[0]["detail"])
    assert detail["before"]["name"] == "old" and detail["after"]["name"] == "new"
    err(env.as_("evil").post(url, json={"agentId": new}), 404, "NOT_FOUND")


# ------------------------------------------------------------------ fallback


def test_model_fallback_is_declared_recorded_and_never_silent(env):
    d = env.add_def()
    env.mp.setenv("HARNESS_AGENT_UNAVAILABLE_MODELS", "m-primary")
    a = ok_spawn(env, d)
    assert a["model"] == {"requested": "m-primary", "used": "m-fallback",
                          "fallbackReason": "primary model m-primary unavailable"}
    assert audit_actions(env.f) == ["agent.spawned", "agent.fallback_used"]
    env.mp.setenv("HARNESS_AGENT_UNAVAILABLE_MODELS", "m-primary,m-fallback")
    err(spawn(env, d), 503, "AGENT_MODEL_UNAVAILABLE")
    no_fb = env.add_def("nofb", fallback_model=None)
    env.mp.setenv("HARNESS_AGENT_UNAVAILABLE_MODELS", "m-primary")
    err(spawn(env, no_fb), 503, "AGENT_MODEL_UNAVAILABLE")  # no undeclared downgrade
    assert len(rows(env.f, "SELECT id FROM agent_assignments")) == 1


def test_open_circuit_breaker_triggers_the_declared_fallback(env):
    d = env.add_def()
    env.svc.engine_runtime.breaker._open_until["deepseek-official|m-primary"] = (
        time.monotonic() + 60)
    assert ok_spawn(env, d)["model"]["used"] == "m-fallback"


def test_resume_reresolves_model_and_records_fallback(env):
    d = env.add_def()
    a = ok_spawn(env, d)
    c = env.as_("eng")
    c.post(f"{P}/r1/agents/{a['id']}/pause")
    env.mp.setenv("HARNESS_AGENT_UNAVAILABLE_MODELS", "m-primary")
    r = c.post(f"{P}/r1/agents/{a['id']}/resume")
    assert r.status_code == 200 and r.json()["model"]["used"] == "m-fallback"
    assert "agent.fallback_used" in audit_actions(env.f)


def test_tool_fallback_recorded_only_when_declared_and_in_scope(env):
    d = env.add_def()
    a = ok_spawn(env, d, scope=default_scope(tools=["read_file", "search", "run_tests"]))
    r = call(env, a["id"], tool="search", outcome="unavailable")
    assert r.status_code == 201 and r.json()["fallback"] == {"kind": "tool", "tool": "read_file"}
    assert r.json()["fallbackTool"] == "read_file"
    r = call(env, a["id"], tool="run_tests", outcome="unavailable")
    assert r.json()["fallback"] is None  # nothing declared: no silent substitution
    assert audit_actions(env.f).count("agent.fallback_used") == 1
    narrow = ok_spawn(env, d, scope=default_scope(tools=["search"]))
    assert call(env, narrow["id"], tool="search", outcome="unavailable").json()["fallback"] is None


# ------------------------------------------------------------------ tool-call summaries


def test_tool_call_records_summary_outcome_and_progress(env):
    d = env.add_def()
    a = ok_spawn(env, d)
    r = call(env, a["id"], target="src/a/x.py", summary="read x.py to find the bug",
             durationMs=12, costUsd="0.05", progress={"percent": 40, "note": "reading"})
    assert r.status_code == 201 and r.json()["outcome"] == "ok" and r.json()["costUsd"] == "0.05"
    listing = env.as_("view").get(f"{P}/r1/agents").json()
    item = listing["items"][0]
    assert item["recentToolCalls"][0]["summary"] == "read x.py to find the bug"
    assert item["progress"] == {"percent": 40, "note": "reading"}
    assert item["budget"]["spentUsd"] == "0.05"
    assert "(40%)" in listing["summary"]["lines"][0]
    assert audit_actions(env.f)[-1] == "agent.tool_called"


@pytest.mark.parametrize("extra", ["reasoning", "thoughts", "chain_of_thought", "scratchpad"])
def test_hidden_reasoning_has_nowhere_to_be_stored(env, extra):
    a = ok_spawn(env, env.add_def())
    r = call(env, a["id"], **{extra: "secret plan steps"})
    assert r.status_code == 422
    assert rows(env.f, "SELECT id FROM agent_tool_calls") == []


def test_secret_shapes_are_redacted_from_summaries(env):
    a = ok_spawn(env, env.add_def())
    call(env, a["id"], summary="curl with sk-abcdefghijklmnopqrstu and Bearer abc.def.ghi done")
    stored = rows(env.f, "SELECT summary FROM agent_tool_calls")[0]["summary"]
    assert "sk-abc" not in stored and "abc.def" not in stored and "[redacted]" in stored


def test_out_of_scope_tool_is_recorded_as_denied_and_refused(env):
    a = ok_spawn(env, env.add_def())
    err(call(env, a["id"], tool="write_file", costUsd="0.4"), 403, "TOOL_NOT_IN_SCOPE")
    row = rows(env.f, "SELECT outcome, cost_microusd FROM agent_tool_calls")[0]
    assert row["outcome"] == "denied" and row["cost_microusd"] == 0


def test_tool_call_idempotent_and_team_scoped(env):
    a = ok_spawn(env, env.add_def())
    first = call(env, a["id"], costUsd="0.1", requestId="t1")
    again = call(env, a["id"], costUsd="0.1", requestId="t1")
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert env.as_("eng").get(f"{P}/r1/agents").json()["items"][0]["budget"]["spentUsd"] == "0.1"
    err(call(env, a["id"], user="evil"), 404, "NOT_FOUND")
    err(call(env, a["id"], user="view"), 403, "ROLE_DENIED")


def test_exceeding_budget_pauses_the_agent_and_blocks_resume(env):
    a = ok_spawn(env, env.add_def(), budget="0.2")
    r = call(env, a["id"], costUsd="0.3")
    assert r.status_code == 201 and r.json()["budgetExceeded"] is True
    assert r.json()["assignmentState"] == "paused"
    assert state(env, a["id"])["pause_reason"] == "budget_exhausted"
    err(env.as_("eng").post(f"{P}/r1/agents/{a['id']}/resume"), 409, "AGENT_BUDGET_EXHAUSTED")
    assert "agent.paused" in audit_actions(env.f)


def test_finish_requires_no_live_children_and_rolls_up(env):
    d = env.add_def()
    root = ok_spawn(env, d, budget="1.0")
    kid = ok_spawn(env, d, parent=root["id"], budget="0.3")
    err(call(env, root["id"], finish="completed"), 409, "CHILDREN_ACTIVE")
    r = call(env, kid["id"], costUsd="0.2", finish="completed")
    assert r.status_code == 201 and r.json()["assignmentState"] == "completed"
    root_row = rows(env.f, "SELECT children_count, reserved_microusd, spent_microusd"
                    " FROM agent_assignments WHERE id=:i", i=root["id"])[0]
    assert (root_row["children_count"], root_row["reserved_microusd"],
            root_row["spent_microusd"]) == (0, 0, 200_000)
    r = call(env, root["id"], finish="failed")
    assert r.json()["assignmentState"] == "failed"
    pool = env.as_("eng").get(f"{P}/r1/agents").json()["pool"]
    assert pool["reservedUsd"] == "0" and pool["spentUsd"] == "0.2"
