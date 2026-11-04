"""W14 — opt-in model routing: classify, resolve, never downgrade on failure.

The policy is a team setting written by Section 1 (admin only). Section 4
resolves it when a run's execution model is chosen. These tests pin the rules
that make the switch safe: off by default, conservative classification, and a
missing class model keeps the configured primary instead of borrowing the other
class's model.
"""

import json

from harness_contracts.plan import AgentPlan
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.model_routing import (
    MECHANICAL,
    REASONING,
    classify_plan,
    parse_policy,
    resolve,
)
from harness_orchestrator.service import OrchestratorService

DDL = """
CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL);
CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL);
CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL);
CREATE TABLE team_model_prefs (
    team_id VARCHAR(36) PRIMARY KEY,
    primary_model VARCHAR(120) NOT NULL,
    reviewer_model VARCHAR(120) NOT NULL
);
CREATE TABLE team_settings (
    team_id VARCHAR(36) NOT NULL,
    key VARCHAR(80) NOT NULL,
    value TEXT,
    PRIMARY KEY (team_id, key)
)
"""


def _plan(steps: int, files: int) -> AgentPlan:
    return AgentPlan(
        goal="route me",
        steps=[f"step {index}" for index in range(steps)],
        filesLikelyToChange=[f"file{index}.py" for index in range(files)],
        expectedResult="done",
    )


# ------------------------------------------------------------------ classify


def test_classification_boundaries():
    assert classify_plan(3, 3) == MECHANICAL
    assert classify_plan(0, 0) == MECHANICAL
    assert classify_plan(4, 0) == REASONING
    assert classify_plan(0, 4) == REASONING
    assert classify_plan(-2, -2) == MECHANICAL


# --------------------------------------------------------------- parse policy


def test_policy_is_opt_in_and_lenient():
    assert parse_policy(None) is None
    assert parse_policy("") is None
    assert parse_policy("not json") is None
    assert parse_policy({"mechanicalModel": "cheap/x"}) is None  # no enabled flag
    assert parse_policy({"enabled": False, "mechanicalModel": "cheap/x"}) is None
    assert parse_policy({"enabled": True}) is None  # nothing usable
    assert parse_policy({"enabled": True, "mechanicalModel": "  "}) is None
    assert parse_policy({"enabled": True, "mechanicalModel": "cheap/x"}) == {
        "mechanicalModel": "cheap/x"
    }
    assert parse_policy('{"enabled": true, "reasoningModel": "frontier/y"}') == {
        "reasoningModel": "frontier/y"
    }


# --------------------------------------------------------------------- resolve


def test_disabled_or_absent_policy_is_the_status_quo():
    model, decision = resolve("team/primary", None, MECHANICAL)
    assert model == "team/primary"
    assert decision["source"] == "primary"
    assert decision["enabled"] is False


def test_enabled_policy_routes_each_class():
    policy = {"mechanicalModel": "cheap/x", "reasoningModel": "frontier/y"}
    model, decision = resolve("team/primary", policy, MECHANICAL)
    assert (model, decision["source"]) == ("cheap/x", "policy")
    model, decision = resolve("team/primary", policy, REASONING)
    assert (model, decision["source"]) == ("frontier/y", "policy")


def test_missing_reasoning_model_never_borrows_the_mechanical_one():
    policy = {"mechanicalModel": "cheap/x"}
    model, decision = resolve("team/primary", policy, REASONING)
    assert model == "team/primary"
    assert decision["source"] == "policy-fallback"
    assert model != "cheap/x"


def test_missing_mechanical_model_keeps_primary_too():
    policy = {"reasoningModel": "frontier/y"}
    model, decision = resolve("team/primary", policy, MECHANICAL)
    assert model == "team/primary"
    assert decision["source"] == "policy-fallback"


# ----------------------------------------------------------------- integration


async def _store(tmp_path=None):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for statement in DDL.split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(text(stmt))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text("INSERT INTO teams (id, name) VALUES ('t1', 'T')"))
        await db.execute(text("INSERT INTO projects (id, team_id) VALUES ('p1', 't1')"))
        await db.execute(text("INSERT INTO agent_runs (id, project_id) VALUES ('r1', 'p1')"))
        await db.execute(
            text(
                "INSERT INTO team_model_prefs (team_id, primary_model, reviewer_model) "
                "VALUES ('t1', 'team/primary', 'team/reviewer')"
            )
        )
        await db.commit()
    return engine, factory


async def _set_policy(factory, document: str | None) -> None:
    async with factory() as db:
        await db.execute(text("DELETE FROM team_settings"))
        if document is not None:
            await db.execute(
                text(
                    "INSERT INTO team_settings (team_id, key, value) "
                    "VALUES ('t1', 'model_routing', :v)"
                ),
                {"v": document},
            )
        await db.commit()


async def test_service_routes_by_plan_shape_and_keeps_primary_without_policy():
    engine, factory = await _store()
    service = OrchestratorService(session_factory=factory)
    try:
        model, decision = await service._routed_model_for_run("r1", _plan(2, 1))
        assert (model, decision["enabled"]) == ("team/primary", False)

        await _set_policy(
            factory,
            json.dumps(
                {"enabled": True, "mechanicalModel": "cheap/x", "reasoningModel": "frontier/y"}
            ),
        )
        model, decision = await service._routed_model_for_run("r1", _plan(2, 1))
        assert (model, decision["class"], decision["source"]) == (
            "cheap/x",
            MECHANICAL,
            "policy",
        )
        model, decision = await service._routed_model_for_run("r1", _plan(9, 6))
        assert (model, decision["class"], decision["source"]) == (
            "frontier/y",
            REASONING,
            "policy",
        )

        await _set_policy(factory, "{corrupt")
        model, decision = await service._routed_model_for_run("r1", _plan(9, 6))
        assert (model, decision["enabled"]) == ("team/primary", False)
    finally:
        await engine.dispose()


async def test_service_without_a_store_returns_the_default_model():
    service = OrchestratorService()
    model, decision = await service._routed_model_for_run("r1", _plan(9, 6))
    assert model is None
    assert decision["source"] == "primary"
    assert decision["enabled"] is False


async def test_decision_names_the_deployment_model_when_none_is_configured(monkeypatch):
    monkeypatch.setenv("DSH_MODEL", "anthropic/claude-haiku-5.5")
    service = OrchestratorService()
    model, decision = await service._routed_model_for_run("r1", _plan(9, 6))
    assert model is None  # the engine still picks its own default
    assert decision["model"] == "anthropic/claude-haiku-5.5"


async def test_partial_plan_object_never_aborts_execution():
    """Regression: some callers hand a duck-typed plan with only `goal`.

    Classifying it must not raise AttributeError — routing is opt-in, so a
    plan-shape surprise keeps the configured primary and the run continues.
    """
    from types import SimpleNamespace

    engine, factory = await _store()
    service = OrchestratorService(session_factory=factory)
    try:
        model, decision = await service._routed_model_for_run(
            "r1", SimpleNamespace(goal="fix bug 12")
        )
        assert model == "team/primary"
        assert decision["source"] == "primary"
        assert decision["class"] == MECHANICAL  # empty plan is the small case
    finally:
        await engine.dispose()
