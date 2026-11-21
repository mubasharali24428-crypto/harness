"""hy4 batch 8 K4 - pin the read-only W28 /synthesis conflict rules (test-only).

No production module is changed here; children are inserted as assignment rows
so each test can state an expected answer in plain terms.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from harness_orchestrator import delegation
from tests.unit.agent_tree_support import P, build


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_SUBAGENTS", raising=False)
    engine, factory, app, who, svc = asyncio.run(build(tmp_path, monkeypatch))
    app.include_router(delegation.router)

    def as_(user):
        who["u"] = user
        return TestClient(app)

    async def _ledger():
        async with factory() as db:
            await db.execute(text("CREATE TABLE IF NOT EXISTS cost_ledger"
                                  " (run_id VARCHAR(36), cost_usd NUMERIC)"))
            await db.commit()

    asyncio.run(_ledger())

    async def _result(run_id):
        return {"status": "not_started", "finalResponse": None}

    monkeypatch.setattr(svc, "get_engine_result", _result)
    yield SimpleNamespace(as_=as_, f=factory, svc=svc, mp=monkeypatch, who=who)
    asyncio.run(engine.dispose())


def child(env, aid: str, paths: list[str], *, parent: str | None = None,
          state: str = "active") -> None:
    """Add one live-or-finished sibling with its own child run and a path scope."""
    async def go():
        async with env.f() as db:
            await db.execute(text(
                "INSERT INTO agent_assignments"
                " (id, run_id, team_id, parent_id, agent_id, agent_name, agent_version, depth,"
                "  title, state, delegated_by, scope, max_depth, max_children, children_count,"
                "  budget_microusd, reserved_microusd, spent_microusd, model_requested,"
                "  model_used, request_id, created_at, updated_at, child_run_id)"
                " VALUES (:i, 'r1', 'team-a', :p, 'agent-k4', 'coder', 1, 0,"
                "  :t, :s, 'eng', :scope, 0, 0, 0, 0, 0, 0, 'm-primary', 'm-primary',"
                "  :rid, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :crid)"),
                {"i": aid, "p": parent, "t": f"child {aid}", "s": state,
                 "scope": json.dumps({"paths": paths, "permissions": ["read"]}),
                 "rid": f"k4-req-{aid}", "crid": f"k4-run-{aid}"})
            await db.commit()

    asyncio.run(go())


def synthesis(env) -> dict:
    r = env.as_("eng").get(f"{P}/r1/synthesis")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reviewRequired"] is True and body["autoMerge"] is False
    return body


def conflict_ids(body: dict) -> list[list[str]]:
    return [c["assignmentIds"] for c in body["conflicts"]]


# --------------------------------------------------------------------------- rows


def test_identical_paths_conflict(env):
    """Identical path scopes are one conflicting pair."""
    child(env, "k4-01", ["src/a"])
    child(env, "k4-02", ["src/a"])
    body = synthesis(env)
    assert conflict_ids(body) == [["k4-01", "k4-02"]]
    assert body["conflicts"][0]["paths"] == ["src/a"]


@pytest.mark.parametrize(("a_paths", "b_paths", "shared"), [
    pytest.param(["src"], ["src/a/b.py"], ["src/a/b.py"], id="parent-first"),
    pytest.param(["src/a/b.py"], ["src"], ["src/a/b.py"], id="parent-second"),
    pytest.param(["."], ["src/a"], ["src/a"], id="workspace-root-parent"),
])
def test_parent_directory_of_sibling_path_conflicts(env, a_paths, b_paths, shared):
    """A child whose path is a parent directory of a sibling's path conflicts."""
    child(env, "k4-01", a_paths)
    child(env, "k4-02", b_paths)
    body = synthesis(env)
    assert conflict_ids(body) == [["k4-01", "k4-02"]]
    assert body["conflicts"][0]["paths"] == shared


@pytest.mark.parametrize(("a_paths", "b_paths"), [
    pytest.param(["src/a"], ["tests/b"], id="src-vs-tests"),
    pytest.param(["src2"], ["src"], id="prefix-lookalike"),
    pytest.param(["src/a"], ["src/b"], id="disjoint-siblings"),
])
def test_unrelated_directories_do_not_conflict(env, a_paths, b_paths):
    """Two unrelated directories produce no conflict."""
    child(env, "k4-01", a_paths)
    child(env, "k4-02", b_paths)
    assert synthesis(env)["conflicts"] == []


@pytest.mark.parametrize(("a_paths", "b_paths"), [
    pytest.param(["src/a"], ["src/a/"], id="trailing-slash"),
    pytest.param(["src/a/"], ["src/a"], id="trailing-slash-reversed"),
    pytest.param(["./src/a"], ["src/a"], id="leading-dot-slash"),
    pytest.param(["src/a"], ["./src/a"], id="leading-dot-slash-reversed"),
    pytest.param(["src/a//"], ["src/a"], id="doubled-slash"),
    pytest.param(["./src/a"], ["src/a/"], id="two-noncanonical-spellings"),
])
def test_spelling_of_the_same_path_does_not_hide_a_conflict(env, a_paths, b_paths):
    """A trailing slash or ./ spelling of the same path does not hide a conflict."""
    child(env, "k4-01", a_paths)
    child(env, "k4-02", b_paths)
    assert conflict_ids(synthesis(env)) == [["k4-01", "k4-02"]]


@pytest.mark.parametrize("other_paths", [
    pytest.param(["src/a"], id="no-path-vs-one-path"),
    pytest.param([], id="no-path-vs-no-path"),
])
def test_child_with_no_paths_conflicts_with_nothing(env, other_paths):
    """A child with NO paths conflicts with nothing."""
    child(env, "k4-01", [])
    child(env, "k4-02", other_paths)
    assert synthesis(env)["conflicts"] == []


def test_synthesis_reports_each_conflicting_pair_once_in_row_order(env):
    """Three overlapping siblings give three pairs, each once, in row order."""
    child(env, "k4-01", ["src/a"])
    child(env, "k4-02", ["src/a"])
    child(env, "k4-03", ["src/a"])
    body = synthesis(env)
    assert conflict_ids(body) == [
        ["k4-01", "k4-02"], ["k4-01", "k4-03"], ["k4-02", "k4-03"],
    ]
    assert len(body["children"]) == 3
