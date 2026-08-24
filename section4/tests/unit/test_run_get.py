"""M12-W2 lane E4a: prId on the run aggregate + GET /api/v1/orchestrator/runs/{runId}.

The producer half lives in Section 2 (test_pr_open.py there); these pin the
consumer-facing S4 surface:
- get_status() (hence the new GET /runs/{runId}) carries ``prId`` — camelCase,
  None until a PR is opened, populated from agent_runs.pr_id when wired to a DB.
- The prId lookup degrades to None (never raises) on DB trouble.
Seeding/client approach mirrors unit/test_runs_index.py.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import harness_orchestrator.api as orch_api
from harness_orchestrator.service import OrchestratorService


@pytest.fixture
def db(tmp_path):
    """Minimal frozen-schema sqlite DB: projects + agent_runs with a pr_id column."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/run_get.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE projects ("
                    "id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE agent_runs ("
                    "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,"
                    " state VARCHAR(40) NOT NULL, created_at TIMESTAMP WITH TIME ZONE,"
                    " pr_id VARCHAR(36))"
                )
            )

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    asyncio.run(engine.dispose())


def _principal() -> Principal:
    return Principal(userId="u-own", teamId="team-a", role=Role.ADMIN)


def _seed_run(factory, run_id: str, pr_id: str | None) -> None:
    async def _go():
        async with factory() as session:
            await session.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('proj-ours', 'team-a')")
                if run_id == "run-with-pr"  # only once; INSERT would dup-key otherwise
                else text("SELECT 1")
            )
            await session.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state, created_at, pr_id)"
                    " VALUES (:r, 'proj-ours', 'pr_opened', CURRENT_TIMESTAMP, :p)"
                ),
                {"r": run_id, "p": pr_id},
            )
            await session.commit()

    asyncio.run(_go())


def _client(monkeypatch, factory, seeded: bool = True) -> TestClient:
    if seeded and factory is not None:
        _seed_run(factory, "run-with-pr", "pr-77")

    svc = OrchestratorService(session_factory=factory)
    monkeypatch.setattr(orch_api, "_service", svc)

    app = FastAPI()
    app.include_router(orch_api.router)
    app.dependency_overrides[orch_api.get_principal] = lambda: _principal()
    return TestClient(app)


def test_get_run_includes_prid_from_db(monkeypatch, db):
    client = _client(monkeypatch, db)
    res = client.get("/api/v1/orchestrator/runs/run-with-pr")
    assert res.status_code == 200, res.text
    body = res.json()
    # prId is read from agent_runs.pr_id even though the in-memory machine
    # boots idle (no agent_events rows were seeded for it to replay).
    assert body["prId"] == "pr-77"
    assert set(body) == {
        "runId",
        "state",
        "planId",
        "planApproved",
        "isHumanEditing",
        "executing",
        "engineMode",
        "prId",
    }


def test_get_run_prid_null_when_no_pr(monkeypatch, db):
    client = _client(monkeypatch, db)

    async def _go():
        async with db() as session:
            await session.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state, created_at)"
                    " VALUES ('run-bare', 'proj-ours', 'executing', CURRENT_TIMESTAMP)"
                )
            )
            await session.commit()

    asyncio.run(_go())
    res = client.get("/api/v1/orchestrator/runs/run-bare")
    assert res.status_code == 200, res.text
    assert res.json()["prId"] is None


def test_status_route_also_carries_prid(monkeypatch, db):
    """The legacy /status read shares the same aggregate (one source of truth)."""
    client = _client(monkeypatch, db)
    res = client.get("/api/v1/orchestrator/runs/run-with-pr/status")
    assert res.status_code == 200, res.text
    assert res.json()["prId"] == "pr-77"


def test_get_run_without_db_wiring_still_answers(monkeypatch):
    """Bare unit mode: no session factory → prId is None, never a crash."""
    client = _client(monkeypatch, None, seeded=False)
    res = client.get("/api/v1/orchestrator/runs/any-run")
    assert res.status_code == 200, res.text
    assert res.json()["prId"] is None


def test_aggregate_degrades_on_db_error(monkeypatch):
    """A broken DB must degrade to prId=None, never break the status read."""
    svc = OrchestratorService()

    class BoomFactory:
        def __call__(self):
            raise RuntimeError("db gone")

    svc.session_factory = BoomFactory()
    status = asyncio.run(svc.get_status("r1"))
    assert status["prId"] is None
