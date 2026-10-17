"""F021 — explicit Ask/Plan/Agent modes with RUNTIME read-only enforcement.

The review gap: "Plan gate and dry/live modes; no full read-only Ask/Plan/Agent
permission model." The load-bearing proof here is runtime denial, not prompt
wording: an ask-mode run can never reach a write-capable engine — no matter
whether execution is attempted through start_execution or directly dispatched
(resume/worker path) — and the refusal is a first-class result, not silence.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal, router
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.run_queue import claim_next_run
from harness_orchestrator.service import MODE_WRITE_DENIED_CODE, OrchestratorService

DDL = """
CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, budget_usd REAL NULL);
CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL);
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL
);
CREATE TABLE cost_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36),
    agent_role VARCHAR(20) NOT NULL DEFAULT 'primary',
    model VARCHAR(120) NOT NULL DEFAULT 'stub',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12,6) NOT NULL,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36) NOT NULL,
    type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL);
CREATE TABLE agent_plans (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    goal TEXT NOT NULL,
    files_likely_to_change TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL DEFAULT '[]',
    tests TEXT NOT NULL DEFAULT '[]',
    risks TEXT NOT NULL DEFAULT '[]',
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    forbidden_paths TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
"""

PROJECT = "proj-m"


class _Bus:
    def __init__(self, factory) -> None:
        self._factory = factory

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        async with self._factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_events (run_id, type, payload)"
                    " VALUES (:r, :t, :payload)"
                ),
                {"r": run_id, "t": name, "payload": json.dumps(payload or {}, default=str)},
            )
            await db.commit()

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        pass


async def _make_store(tmp_path, tag: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{tag}.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for stmt in DDL.split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
        await conn.execute(text("INSERT INTO teams VALUES ('team-a', 'A', NULL)"))
        await conn.execute(text(f"INSERT INTO projects VALUES ('{PROJECT}', 'team-a')"))
    return engine, factory


def _make_service(factory, calls: list[str]) -> OrchestratorService:
    def runner(goal: str) -> str:
        calls.append(goal)
        return "ok"

    return OrchestratorService(
        engine=DeepSeekHarnessWrapper(backend_runner=runner),
        session_factory=factory,
        run_queue_factory=factory,
        bus=_Bus(factory),
    )


async def test_unknown_mode_rejected_before_any_state_change(tmp_path):
    engine, factory = await _make_store(tmp_path, "modes")
    try:
        svc = _make_service(factory, [])
        with pytest.raises(ValueError, match="unknown run mode"):
            await svc.propose_plan("run-bad", goal="x", project_id=PROJECT, mode="yolo")
        assert "run-bad" not in svc.runs  # rejected before run creation
    finally:
        await engine.dispose()


async def test_default_mode_is_agent_and_explicit_modes_stored(tmp_path):
    engine, factory = await _make_store(tmp_path, "modes2")
    try:
        svc = _make_service(factory, [])
        await svc.propose_plan("run-d", goal="x", project_id=PROJECT)
        assert svc.runs["run-d"]["mode"] == "agent"  # legacy default
        await svc.propose_plan("run-p", goal="x", project_id=PROJECT, mode="plan")
        assert svc.runs["run-p"]["mode"] == "plan"
    finally:
        await engine.dispose()


async def test_ask_mode_denies_execution_at_start_even_when_approved(tmp_path):
    calls: list[str] = []
    engine, factory = await _make_store(tmp_path, "ask")
    try:
        svc = _make_service(factory, calls)
        plan_id = await svc.propose_plan(
            "run-ask", goal="refactor everything", project_id=PROJECT, mode="ask"
        )
        await svc.approve_plan("run-ask", plan_id, "u1")
        run_data = svc.runs["run-ask"]
        state_before = run_data["state_machine"].state

        await svc.start_execution("run-ask")

        assert calls == []  # the engine (and every write it could perform) never ran
        result = run_data["engine_result"]
        assert result["errorCode"] == MODE_WRITE_DENIED_CODE
        assert result["engine"] == "mode_guard"
        assert "read-only" in result["finalResponse"]
        assert run_data["mode_denied"] is True
        assert run_data["state_machine"].state == state_before  # no false FAILED
        assert run_data.get("task") is None
        # no queue election happened: nothing write-adjacent was engaged
        async with factory() as db:
            n = (
                await db.execute(text("SELECT COUNT(*) FROM run_claims"))
            ).scalar_one()
        assert n == 0
    finally:
        await engine.dispose()


async def test_ask_mode_dispatch_path_is_also_denied(tmp_path):
    """Defense in depth: a direct spawn (resume/worker path) hits the same
    guard inside _execute — start_execution is not the only entrance."""
    calls: list[str] = []
    engine, factory = await _make_store(tmp_path, "ask2")
    try:
        svc = _make_service(factory, calls)
        plan_id = await svc.propose_plan(
            "run-ask2", goal="write everything", project_id=PROJECT, mode="ask"
        )
        run_data = svc.runs["run-ask2"]
        svc._spawn_execution(run_data, svc.planner.get_plan(plan_id))
        await asyncio.wait_for(run_data["task"], timeout=5)

        assert calls == []
        assert run_data["engine_result"]["errorCode"] == MODE_WRITE_DENIED_CODE
    finally:
        await engine.dispose()


@pytest.mark.parametrize("mode", ["plan", "agent"])
async def test_plan_and_agent_modes_execute_through_the_gate(mode, tmp_path):
    calls: list[str] = []
    engine, factory = await _make_store(tmp_path, f"ok-{mode}")
    try:
        svc = _make_service(factory, calls)
        run_id = f"run-{mode}"
        plan_id = await svc.propose_plan(
            run_id, goal=f"do work in {mode}", project_id=PROJECT, mode=mode
        )
        await svc.approve_plan(run_id, plan_id, "u1")
        await svc.start_execution(run_id)
        claim = await claim_next_run(svc.run_queue_factory, svc.worker_id, max_per_team=4)
        assert claim is not None and claim["run_id"] == run_id
        assert await svc._dispatch_claim(run_id, claim) is True
        await asyncio.wait_for(svc.runs[run_id]["task"], timeout=5)

        assert len(calls) == 1  # exactly one engine call, through the gate
        assert svc.runs[run_id]["execution_prompt"]["framed"] is False  # no ctx here
        assert svc.runs[run_id].get("mode_denied") is not True
    finally:
        await engine.dispose()


def _principal() -> Principal:
    return Principal(userId="u-m", teamId="team-a", role=Role.ADMIN)


def test_api_accepts_ask_mode_and_rejects_unknown(tmp_path, monkeypatch):
    async def _setup():
        engine, factory = await _make_store(tmp_path, "api")
        return engine, factory

    engine, factory = asyncio.run(_setup())
    try:
        svc = _make_service(factory, [])
        monkeypatch.setattr(orch_api, "_service", svc)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_principal] = _principal
        client = TestClient(app)

        r = client.post(
            "/api/v1/orchestrator/runs/run-api/plan",
            json={"goal": "read only please", "projectId": PROJECT, "mode": "ask"},
        )
        assert r.status_code == 201, r.text
        assert svc.runs["run-api"]["mode"] == "ask"

        r = client.post(
            "/api/v1/orchestrator/runs/run-api2/plan",
            json={"goal": "x", "projectId": PROJECT, "mode": "yolo"},
        )
        assert r.status_code == 422  # pydantic Literal rejects unknown modes
        assert "run-api2" not in svc.runs
    finally:
        asyncio.run(engine.dispose())


async def test_ask_mode_survives_replica_restart_and_cannot_flip(tmp_path):
    """F025: mode rides PLAN_PROPOSED; a fresh replica restores it and ask is sticky."""
    calls: list[str] = []
    engine, factory = await _make_store(tmp_path, "askdur")
    try:
        a = _make_service(factory, calls)
        plan_id = await a.propose_plan("run-dur", goal="x", project_id=PROJECT, mode="ask")
        await asyncio.sleep(0.2)  # event publication is fire-and-forget
        async with factory() as db:
            row = (
                await db.execute(
                    text("SELECT payload FROM agent_events WHERE type='plan_proposed'")
                )
            ).first()
        assert json.loads(row[0])["mode"] == "ask"

        b = _make_service(factory, calls)  # cold replica: no in-process state
        await b.approve_plan("run-dur", plan_id, "u1")
        assert b.runs["run-dur"]["mode"] == "ask"
        await b.start_execution("run-dur")
        assert calls == []
        assert b.runs["run-dur"]["engine_result"]["errorCode"] == MODE_WRITE_DENIED_CODE
    finally:
        await engine.dispose()


async def test_explicit_agent_mode_cannot_flip_an_ask_run(tmp_path):
    engine, factory = await _make_store(tmp_path, "askflip")
    try:
        svc = _make_service(factory, [])
        await svc.propose_plan("run-f", goal="x", project_id=PROJECT, mode="ask")
        run_data = svc.runs["run-f"]
        run_data["state_machine"].state = type(run_data["state_machine"].state).IDLE
        await svc.propose_plan("run-f", goal="x", project_id=PROJECT, mode="agent")
        assert svc.runs["run-f"]["mode"] == "ask"
    finally:
        await engine.dispose()
