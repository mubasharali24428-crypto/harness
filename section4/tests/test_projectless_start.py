"""R3-S2: a run that is not bound to a project must not be queued.

``POST /plans`` leaves the run unbound when the caller's team has no project, or several, and
sends none. The claim query joins ``run_claims`` to ``projects`` on ``project_id``, so a claim
queued under ``''`` can never be claimed: the start answered ``executing`` and nothing ever
ran, while ``harness_queue_depth`` stayed above zero and the stalled-queue and event-bus-lag
alerts fired (Stage 2 record, Unit I: one such row sat queued for 67 days).
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.plan_gate import _plan_gate_principal, configure_plan_gate, router
from harness_orchestrator.service import OrchestratorService

_DDL = (
    "CREATE TABLE agent_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " project_id VARCHAR(36), run_id VARCHAR(36), type VARCHAR(60) NOT NULL,"
    " payload TEXT NOT NULL, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE agent_plans (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " goal TEXT NOT NULL, files_likely_to_change JSON NOT NULL, steps JSON NOT NULL,"
    " tests JSON NOT NULL, risks JSON NOT NULL, estimated_cost_usd NUMERIC(12,6) NOT NULL,"
    " forbidden_paths JSON NOT NULL, expected_result TEXT NOT NULL, status VARCHAR(20) NOT NULL,"
    " version INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " actor_member_id VARCHAR(36), actor_kind VARCHAR(16) NOT NULL, action VARCHAR(80) NOT NULL,"
    " target_type VARCHAR(60) NOT NULL, target_id VARCHAR(80) NOT NULL, detail JSON NOT NULL,"
    " ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)",
    "CREATE TABLE run_claims (run_id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,"
    " state VARCHAR(20) NOT NULL, claimed_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,"
    " worker_id VARCHAR(80) NOT NULL)",
)


class _Bus:
    async def publish(self, *a, **k) -> None:
        return None

    async def ensure_run(self, *a, **k) -> None:
        return None


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def parts(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/projectless.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for ddl in _DDL:
                await conn.execute(text(ddl))

    _run(_setup())
    service = OrchestratorService(session_factory=factory, run_queue_factory=factory, bus=_Bus())
    yield service, factory
    _run(engine.dispose())


def _client(service: OrchestratorService) -> TestClient:
    configure_plan_gate(service)
    app = FastAPI()
    app.include_router(router)

    async def _principal() -> Principal:
        return Principal(userId="u-1", teamId="team-a", role=Role.ADMIN)

    app.dependency_overrides[_plan_gate_principal] = _principal
    return TestClient(app)


def _claims(factory) -> list[tuple[str, str, str]]:
    async def _read():
        async with factory() as db:
            rows = await db.execute(text("SELECT run_id, project_id, state FROM run_claims"))
            return [tuple(r) for r in rows]

    return _run(_read())


def _walk(client: TestClient, body: dict):
    base = "/api/v1/orchestrator"
    plan_id = client.post(f"{base}/plans", json=body).json()["planId"]
    run_id = client.post(f"{base}/plans/{plan_id}/approve").json()["runId"]
    return run_id, client.post(f"{base}/runs/{run_id}/start", json={})


def test_a_bound_run_is_queued_under_its_project(parts):
    """Control: this fixture reaches the queue, so the refusal below is not vacuous."""
    service, factory = parts

    async def _seed():
        async with factory() as db:
            await db.execute(text("INSERT INTO projects (id, team_id) VALUES ('p-1', 'team-a')"))
            await db.commit()

    _run(_seed())
    run_id, started = _walk(_client(service), {"goal": "g", "projectId": "p-1"})
    assert started.status_code == 200, started.text
    assert _claims(factory) == [(run_id, "p-1", "queued")]


def test_an_unbound_run_is_refused_at_start_and_never_queued(parts):
    """The team has no project and the propose named none: the run is unbound."""
    service, factory = parts
    _, started = _walk(_client(service), {"goal": "g"})
    assert started.status_code == 400, started.text
    detail = started.json()["detail"]["error"]
    assert detail["code"] == "INVALID_STATE" and "project" in detail["message"]
    assert _claims(factory) == []
