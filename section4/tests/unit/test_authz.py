"""D5 regression guards (wave 6 Phase 0 / P0.5).

Pins two previously-missing authorization layers on Section 4:

1. Team ownership: a principal may only touch runs whose project belongs to
   their team (agent_runs.project_id -> projects.team_id).
2. Role permission: viewer/guest cannot drive runs ("write") and cannot
   approve/reject plans ("approve"); admin/engineer can.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal, router
from harness_orchestrator.authz import OwnershipDeniedError, RoleDeniedError, require_role
from harness_orchestrator.service import OrchestratorService


def _principal(
    user_id: str = "u-own", team_id: str = "team-a", role: Role = Role.ADMIN
) -> Principal:
    return Principal(userId=user_id, teamId=team_id, role=role)


# --------------------------------------------------------------------- unit


def test_require_role_blocks_read_only_roles_on_write():
    for role in (Role.VIEWER, Role.GUEST):
        with pytest.raises(RoleDeniedError):
            require_role(_principal(role=role), "write")


def test_require_role_allows_staff_on_write_and_approve():
    for role in (Role.ADMIN, Role.ENGINEER):
        require_role(_principal(role=role), "write")
        require_role(_principal(role=role), "approve")


def test_require_role_viewer_may_read():
    require_role(_principal(role=Role.VIEWER), "read")


@pytest.fixture
def db_service(tmp_path):
    """OrchestratorService wired to a sqlite DB holding one projects row."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/authz.db")

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
                    "INSERT INTO projects (id, team_id) VALUES ('proj-ours', 'team-a'),"
                    " ('proj-theirs', 'team-b')"
                )
            )

    import asyncio as _asyncio

    _asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory)
    yield service
    _asyncio.run(engine.dispose())


async def test_authorize_run_rejects_foreign_team_project(db_service):
    await db_service.propose_plan("run-x1", project_id="proj-theirs")
    with pytest.raises(OwnershipDeniedError):
        from harness_orchestrator.authz import authorize_run

        await authorize_run(db_service, "run-x1", _principal(team_id="team-a"))


async def test_authorize_run_allows_own_team_project(db_service):
    await db_service.propose_plan("run-x2", project_id="proj-ours")
    from harness_orchestrator.authz import authorize_run

    run_data = await authorize_run(db_service, "run-x2", _principal(team_id="team-a"))
    assert run_data["projectId"] == "proj-ours"


async def test_authorize_run_skips_check_without_db():
    """Bare unit mode (no session factory): ownership check is not applicable."""
    from harness_orchestrator.authz import authorize_run

    service = OrchestratorService()  # no factory
    run_data = await authorize_run(service, "run-bare", _principal())
    assert run_data["runId"] == "run-bare"


# -------------------------------------------------------------------- routes


def _client(monkeypatch, service: OrchestratorService, principal: Principal) -> TestClient:
    monkeypatch.setattr(orch_api, "_service", service)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def test_route_rejects_run_of_other_team(monkeypatch, db_service):
    client = _client(monkeypatch, db_service, _principal(team_id="team-c"))
    res = client.post(
        "/api/v1/orchestrator/runs/run-y1/plan",
        json={"goal": "g", "projectId": "proj-theirs"},
    )
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "TEAM_OWNERSHIP"


def test_route_accepts_project_of_own_team(monkeypatch, db_service):
    client = _client(monkeypatch, db_service, _principal(team_id="team-a"))
    res = client.post(
        "/api/v1/orchestrator/runs/run-y2/plan",
        json={"goal": "g", "projectId": "proj-ours"},
    )
    assert res.status_code == 201


def test_route_viewer_cannot_start_but_can_read(monkeypatch, db_service):
    client = _client(monkeypatch, db_service, _principal(role=Role.VIEWER))
    plan = client.post("/api/v1/orchestrator/runs/run-v1/plan").json()["planId"]

    res = client.post("/api/v1/orchestrator/runs/run-v1/plan/approve", json={"planId": plan})
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"

    res = client.post("/api/v1/orchestrator/runs/run-v1/start")
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"

    res = client.get("/api/v1/orchestrator/runs/run-v1/status")
    assert res.status_code == 200  # read stays open to every authenticated role


def test_route_guest_cannot_pause(monkeypatch, db_service):
    client = _client(monkeypatch, db_service, _principal(role=Role.GUEST))
    res = client.post("/api/v1/orchestrator/runs/run-g1/pause")
    assert res.status_code == 403
