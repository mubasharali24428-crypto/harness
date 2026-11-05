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
