"""W15 — the routing decision is a durable event, not process memory.

W14 shipped opt-in routing but recorded the outcome only in `run_data["routing"]`
and a log line, so after a restart — or from another replica, or from the run
page — nobody could check which model an attempt got or why. These tests pin the
`model_routed` emission at the service seam: one event per attempt, emitted
before the engine call, carrying the decision record and nothing else.
"""

import asyncio
import json

from harness_contracts.events import EventType
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.service import OrchestratorService

# `pull_requests` is not touched by routing itself; the aggregate's enrichment
# LEFT JOINs it, and a missing table would swallow the whole read (the routing
# field included), so the fixture mirrors enough of the real schema to be honest.
DDL = """
CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, budget_usd REAL NULL);
CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL);
CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,
                         pr_id VARCHAR(36) NULL);
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
);
CREATE TABLE agent_plans (
    id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL, goal TEXT NOT NULL,
    files_likely_to_change TEXT DEFAULT '[]', steps TEXT DEFAULT '[]', tests TEXT DEFAULT '[]',
    risks TEXT DEFAULT '[]', estimated_cost_usd REAL DEFAULT 0, forbidden_paths TEXT DEFAULT '[]',
    expected_result TEXT DEFAULT '', status VARCHAR(20) NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36), type VARCHAR(60) NOT NULL, payload TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
CREATE TABLE cost_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),
    agent_role VARCHAR(20) DEFAULT 'primary', model VARCHAR(120) DEFAULT 'stub',
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cost_usd NUMERIC(12,6) DEFAULT 0, latency_ms INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL, state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, worker_id VARCHAR(80) NOT NULL);
CREATE TABLE pull_requests (id VARCHAR(36) PRIMARY KEY, pr_url TEXT);
CREATE TABLE artifacts (
    id VARCHAR(64) PRIMARY KEY, team_id VARCHAR(64) NOT NULL, run_id VARCHAR(64) NOT NULL,
    name VARCHAR(200) NOT NULL, media_type VARCHAR(120) NOT NULL, size_bytes INTEGER NOT NULL,
    sha256 VARCHAR(64) NOT NULL, storage_ref VARCHAR(200) NOT NULL, kind VARCHAR(20) NOT NULL,
    state VARCHAR(20) NOT NULL, scan TEXT DEFAULT '{}', request_id VARCHAR(64) NULL,
    created_by VARCHAR(64) NOT NULL, created_at TEXT NOT NULL, retention_until TEXT NULL,
    deleted_at TEXT NULL);
"""


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class ModelCapturingEngine:
    """Records the model the service actually handed the call."""

    api_key = None

    def __init__(self):
        self.models = []

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        self.models.append(model)
        return {
            "sessionId": session_id,
            "engine": "capture",
            "status": "completed",
            "finalResponse": "done",
        }


def _started(policy: dict | None):
    """propose → approve, with the routing policy (if any) written as an admin
    would have written it, and an event capture wired to the service seam."""

    async def _build():
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
            if policy is not None:
                await db.execute(
                    text(
                        "INSERT INTO team_settings (team_id, key, value) "
                        "VALUES ('t1', 'model_routing', :v)"
                    ),
                    {"v": json.dumps(policy)},
                )
            await db.commit()
        return engine, factory

    engine, factory = run(_build())
    events: list[tuple[str, dict]] = []
    svc = OrchestratorService(
        engine=ModelCapturingEngine(),
        session_factory=factory,
        on_event=lambda etype, payload: events.append((str(etype), dict(payload))),
    )

    async def _go():
        await svc.propose_plan("r1", goal="route and record me", project_id="p1")
        plan_id = svc.runs["r1"]["plan_id"]
        await svc.approve_plan("r1", plan_id, "u-test")

    run(_go())
    plan = svc.planner.get_plan(svc.runs["r1"]["plan_id"])
    return svc, svc.runs["r1"], plan, events, engine


def _routed(events):
    return [payload for etype, payload in events if etype == EventType.MODEL_ROUTED.value]


def test_a_routed_attempt_records_its_decision_before_the_engine_call():
    svc, run_data, plan, events, engine = _started(
        {"enabled": True, "mechanicalModel": "cheap/x", "reasoningModel": "frontier/y"}
    )
    try:
        run(svc._execute(run_data, plan))

        routed = _routed(events)
        assert len(routed) == 1, f"exactly one decision per attempt, got {routed}"
        decision = routed[0]
        assert decision["runId"] == "r1"
        assert decision["enabled"] is True
        assert decision["source"] == "policy"
        assert decision["class"] in {"mechanical", "reasoning"}
        expected = "cheap/x" if decision["class"] == "mechanical" else "frontier/y"
        assert decision["model"] == expected
        # the recorded model is the one the engine was actually handed
        assert svc.engine.models == [decision["model"]]
        # the payload is the decision record: no secrets, no token counts
        assert set(decision) <= {"runId", "enabled", "class", "model", "source", "reason"}
    finally:
        run(engine.dispose())


def test_an_unrouted_attempt_records_that_routing_was_off():
    """The absence of routing is itself auditable: a run page must be able to
    say "primary model, routing disabled" instead of staying silent."""
    svc, run_data, plan, events, engine = _started(None)
    try:
        run(svc._execute(run_data, plan))
        decision = _routed(events)[0]
        assert decision["enabled"] is False
        assert decision["source"] == "primary"
        assert decision["model"] == "team/primary"
        assert svc.engine.models == ["team/primary"]
    finally:
        run(engine.dispose())


def test_a_resumed_attempt_records_its_own_decision():
    """One event per attempt, so a resume that re-resolves the policy (a team
    may have changed it between attempts) cannot be read as the first one."""
    svc, run_data, plan, events, engine = _started(
        {"enabled": True, "mechanicalModel": "cheap/x", "reasoningModel": "frontier/y"}
    )
    try:
        run(svc._execute(run_data, plan))
        run(svc._execute(run_data, plan))
        assert len(_routed(events)) == 2
        assert len(svc.engine.models) == 2
    finally:
        run(engine.dispose())


def test_the_run_aggregate_answers_from_the_durable_event(tmp_path):
    """`GET /runs/{id}` must report the model a run executed under, reading the
    event store — so any replica answers it, and a restart changes nothing.
    Absent history reads None; the newest attempt wins over an older one."""

    async def _factory():
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
            await db.execute(text("INSERT INTO agent_runs (id, project_id) VALUES ('r9', 'p1')"))
            await db.commit()
        return engine, factory

    engine, factory = run(_factory())
    svc = OrchestratorService(session_factory=factory)

    async def _seed(payloads: list[str]) -> None:
        async with factory() as db:
            for payload in payloads:
                await db.execute(
                    text("INSERT INTO agent_events (project_id, run_id, type, payload) "
                         "VALUES (:project_id, 'r9', :t, :p)"),
                    {"project_id": "p1", "t": EventType.MODEL_ROUTED.value, "p": payload},
                )
            await db.commit()

    async def _routing_field():
        return (await svc.get_status("r9"))["routing"]

    try:
        assert run(_routing_field()) is None  # no attempt: unknown, not "off"

        run(_seed([json.dumps({"runId": "r9", "enabled": True, "class": "mechanical",
                             "model": "cheap/x", "source": "policy"})]))
        assert run(_routing_field())["model"] == "cheap/x"

        # a later attempt (a resume after the team re-picked its models) wins
        run(_seed([json.dumps({"runId": "r9", "enabled": True, "class": "reasoning",
                             "model": "frontier/y", "source": "policy"})]))
        latest = run(_routing_field())
        assert (latest["model"], latest["class"]) == ("frontier/y", "reasoning")
    finally:
        run(engine.dispose())
