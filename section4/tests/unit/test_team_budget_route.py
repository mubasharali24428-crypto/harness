"""W15 slice 2 — run start consumes a TEAM budget after authorization.

The orchestrator's own limiter keys per IP + route template. Run start is the
one route that spawns work, so it also consumes a team bucket once the run's
team is resolved; an over-budget team answers 429 RATE_LIMITED and the run is
never started.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal, router
from harness_orchestrator.service import OrchestratorService


def _principal(team_id: str = "team-a") -> Principal:
    return Principal(userId="u-w15", teamId=team_id, role=Role.ADMIN)


@pytest.fixture
def started_service(tmp_path, monkeypatch):
    """Service over sqlite with one attributable run and no engine work."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/w15.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36))")
            )
            await conn.execute(
                text("CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36))")
            )
            await conn.execute(
                text(
                    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, "
                    "team_id VARCHAR(36), user_id VARCHAR(36))"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE rate_buckets (bucket_key VARCHAR(200) PRIMARY KEY, "
                    "window_start TIMESTAMPTZ NOT NULL, count INTEGER NOT NULL DEFAULT 0)"
                )
            )
            await conn.execute(
                text("INSERT INTO projects VALUES ('proj-a','team-a'), ('proj-b','team-b')")
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_runs VALUES ('run-a','proj-a'), ('run-b','proj-b')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO team_members VALUES ('m-a','team-a','u-w15'),"
                    " ('m-b','team-b','u-w15')"
                )
            )

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory)
    started: list[str] = []

    async def _start(run_id: str) -> None:
        started.append(run_id)

    monkeypatch.setattr(service, "start_execution", _start)
    monkeypatch.setenv("HARNESS_TEAM_RUN_START_LIMIT", "1")
    yield service, started
    asyncio.run(engine.dispose())


def _client(monkeypatch, service, principal) -> TestClient:
    monkeypatch.setattr(orch_api, "_service", service)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def test_second_start_for_the_same_team_answers_429(monkeypatch, started_service):
    service, started = started_service
    client = _client(monkeypatch, service, _principal("team-a"))

    first = client.post("/api/v1/orchestrator/runs/run-a/start")
    assert first.status_code == 200
    blocked = client.post("/api/v1/orchestrator/runs/run-a/start")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["error"]["code"] == "RATE_LIMITED"
    assert blocked.headers["retry-after"].isdigit()
    assert started == ["run-a"]


def test_another_team_still_starts(monkeypatch, started_service):
    service, started = started_service
    busy = _client(monkeypatch, service, _principal("team-a"))
    idle = _client(monkeypatch, service, _principal("team-b"))

    assert busy.post("/api/v1/orchestrator/runs/run-a/start").status_code == 200
    assert busy.post("/api/v1/orchestrator/runs/run-a/start").status_code == 429
    assert idle.post("/api/v1/orchestrator/runs/run-b/start").status_code == 200
    assert started == ["run-a", "run-b"]
