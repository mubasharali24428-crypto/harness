"""S1-R05e — run.started / run.stopped rows land in audit_log.

S1's ``emit_run_started/stopped`` helpers had zero production callers, so
lifecycle intent existed nowhere durable. The fix: S4's start/stop ROUTES
emit the audit row (principal-aware) through a raw-SQL helper — sections may
not import each other's code, so canonical allowlist membership is declared
here and mirrored in S1's audit.py.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.authz import (
    AUDIT_ACTION_RUN_STARTED,
    AUDIT_ACTION_RUN_STOPPED,
    emit_run_lifecycle,
)
from harness_orchestrator.service import OrchestratorService

DDL = (
    (
        "CREATE TABLE projects ("
        "id VARCHAR(36) PRIMARY KEY,"
        " team_id VARCHAR(36) NOT NULL,"
        " name VARCHAR(200) NOT NULL DEFAULT 'x')"
    ),
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY,"
    " project_id VARCHAR(36), state VARCHAR(40))",
    (
        "CREATE TABLE team_members ("
        "id VARCHAR(36) PRIMARY KEY,"
        " team_id VARCHAR(36) NOT NULL,"
        " user_id VARCHAR(36),"
        " display_name VARCHAR(200) NOT NULL DEFAULT 'x',"
        " member_type VARCHAR(16) NOT NULL DEFAULT 'human',"
        " role VARCHAR(16) NOT NULL DEFAULT 'engineer')"
    ),
    (
        "CREATE TABLE audit_log ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
        "actor_member_id VARCHAR(36),"
        "actor_kind VARCHAR(16) NOT NULL,"
        "action VARCHAR(80) NOT NULL,"
        "target_type VARCHAR(60) NOT NULL,"
        "target_id VARCHAR(80) NOT NULL,"
        "detail TEXT NOT NULL DEFAULT '{}',"
        "ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "FOREIGN KEY(actor_member_id) REFERENCES team_members (id))"
    ),
)


@pytest.fixture(autouse=True)
def _no_legacy_authz_mode(monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)


class RecordingBus:
    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        return None

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        return None


def _principal(
    user_id: str = "u-life", team_id: str = "team-a", role: Role = Role.ENGINEER
) -> Principal:
    return Principal(userId=user_id, teamId=team_id, role=role)


@pytest.fixture()
def db_parts(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/life.db")

    from sqlalchemy import event as sa_event

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async def _create():
        async with engine.begin() as conn:
            for ddl in DDL:
                await conn.execute(text(ddl))
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('proj-a', 'team-a')")
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state)"
                    " VALUES ('run-x', 'proj-a', 'idle')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO team_members (id, team_id, user_id, role)"
                    " VALUES ('mem-1', 'team-a', 'u-life', 'engineer')"
                )
            )

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=RecordingBus())
    yield {"service": service, "factory": factory}
    asyncio.run(engine.dispose())


def _client(monkeypatch, service: OrchestratorService, principal: Principal):
    monkeypatch.setattr(orch_api, "_service", service)
    app = FastAPI()
    app.include_router(orch_router)
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def _stub_verbs(monkeypatch, service: OrchestratorService) -> None:
    monkeypatch.setattr(service, "start_execution", AsyncMock())
    monkeypatch.setattr(service, "stop_agent", AsyncMock())


async def _rows(factory):
    async with factory() as db:
        return (
            (
                await db.execute(
                    text(
                        "SELECT actor_member_id, action, target_type, target_id, detail"
                        " FROM audit_log ORDER BY seq"
                    )
                )
            )
            .mappings()
            .all()
        )


def test_start_route_writes_run_started_audit_row(db_parts, monkeypatch):
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    _stub_verbs(monkeypatch, service)
    client = _client(monkeypatch, service, _principal())

    res = client.post("/api/v1/orchestrator/runs/run-x/start")
    assert res.status_code == 200, res.text

    rows = asyncio.run(_rows(factory))
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == AUDIT_ACTION_RUN_STARTED == "run.started"
    assert row["actor_member_id"] == "mem-1"  # team_members.id, not users.id
    assert row["target_type"] == "agent_run"
    assert row["target_id"] == "run-x"
    assert json.loads(row["detail"])["userId"] == "u-life"


def test_stop_route_writes_run_stopped_audit_row(db_parts, monkeypatch):
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    _stub_verbs(monkeypatch, service)
    client = _client(monkeypatch, service, _principal())

    res = client.post("/api/v1/orchestrator/runs/run-x/stop")
    assert res.status_code == 200, res.text

    rows = asyncio.run(_rows(factory))
    # P2.3: request -> stop -> confirmation are each durable, in order.
    assert [r["action"] for r in rows] == [
        "run.cancel_requested",
        AUDIT_ACTION_RUN_STOPPED,
        "run.cancel_confirmed",
    ]
    assert AUDIT_ACTION_RUN_STOPPED == "run.stopped"
    assert all(r["actor_member_id"] == "mem-1" for r in rows)


def test_stop_without_confirmation_records_no_cancel_confirmed(db_parts, monkeypatch):
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    _stub_verbs(monkeypatch, service)
    monkeypatch.setattr(service, "engine_quiesced", lambda run_id: False)
    client = _client(monkeypatch, service, _principal())

    res = client.post("/api/v1/orchestrator/runs/run-x/stop")
    assert res.json()["status"] == "stopping"
    rows = asyncio.run(_rows(factory))
    assert [r["action"] for r in rows] == ["run.cancel_requested", "run.stopped"]


def test_non_member_actor_binds_null_and_keeps_detail_identity(db_parts, monkeypatch):
    """A principal without a team_members row (provisioning drift) still
    yields a durable row — NULL actor, identity in detail."""
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    _stub_verbs(monkeypatch, service)
    client = _client(monkeypatch, service, _principal(user_id="u-ghost"))

    res = client.post("/api/v1/orchestrator/runs/run-x/stop")
    assert res.status_code == 200, res.text

    rows = asyncio.run(_rows(factory))
    assert rows and all(r["actor_member_id"] is None for r in rows)
    assert json.loads(rows[0]["detail"])["userId"] == "u-ghost"


async def test_bare_mode_emit_is_a_silent_noop():
    """No session factory (bare unit mode) must not raise — the helper is
    wired into routes that legacy bare tests exercise."""
    await emit_run_lifecycle(
        OrchestratorService(),
        action=AUDIT_ACTION_RUN_STARTED,
        run_id="run-bare",
        principal=_principal(),
    )


def test_pause_and_resume_routes_write_audit_rows(db_parts, monkeypatch):
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    monkeypatch.setattr(service, "pause_agent", AsyncMock())
    monkeypatch.setattr(service, "resume_agent", AsyncMock())
    monkeypatch.setattr(service, "get_status", AsyncMock(return_value={"state": "EXECUTING"}))
    client = _client(monkeypatch, service, _principal())

    assert client.post("/api/v1/orchestrator/runs/run-x/pause").status_code == 200
    assert client.post("/api/v1/orchestrator/runs/run-x/resume").status_code == 200

    rows = asyncio.run(_rows(factory))
    assert [r["action"] for r in rows] == ["run.paused", "run.resumed"]
    assert all(r["actor_member_id"] == "mem-1" for r in rows)
