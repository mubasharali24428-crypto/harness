"""M10-A4 tests: runs index API (contract M10-C2, server side).

Seeding/fixture approach copied from test_authz.py: sqlite+aiosqlite engine,
minimal frozen-schema tables (projects / agent_runs / agent_events /
auth_sessions / team_members), async_sessionmaker factory, TestClient with
either a dependency override (identity) or the real make_bearer_dep against
seeded auth_sessions rows (401 path).
"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.fastapi_auth import make_bearer_dep
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.runs_index import router

_NOW = datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def db(tmp_path):
    """sqlite DB holding frozen-schema tables + two teams' worth of runs."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/runs_index.db")

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
                    " state VARCHAR(40) NOT NULL,"
                    " created_at TIMESTAMP WITH TIME ZONE NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE agent_events ("
                    "seq INTEGER PRIMARY KEY, run_id VARCHAR(36),"
                    " type VARCHAR(60) NOT NULL,"
                    " ts TIMESTAMP WITH TIME ZONE NOT NULL)"
                )
            )
            # For make_bearer_dep lookups (auth_sessions ⋈ team_members).
            await conn.execute(
                text(
                    "CREATE TABLE auth_sessions ("
                    "token_hash VARCHAR(64) PRIMARY KEY, user_id VARCHAR(36),"
                    " team_id VARCHAR(36), expires_at TIMESTAMP WITH TIME ZONE)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE team_members ("
                    "user_id VARCHAR(36), team_id VARCHAR(36), role VARCHAR(20))"
                )
            )

    import asyncio as _asyncio

    _asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    _asyncio.run(engine.dispose())


def _seed_run(factory, run_id: str, project_id: str, state: str, minutes_ago: int):
    """Insert one agent_runs row (+ optional lifecycle events) synchronously."""
    created = _NOW - timedelta(minutes=minutes_ago)

    async def _go():
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state, created_at)"
                    " VALUES (:r, :p, :s, :c)"
                ),
                {"r": run_id, "p": project_id, "s": state, "c": created},
            )
            if state == "executing":
                events = [("execution_started", created + timedelta(seconds=30))]
            elif state == "pr_opened":
                events = [
                    ("execution_started", created + timedelta(seconds=30)),
                    ("engine_completed", created + timedelta(minutes=5)),
                    ("pr_opened", created + timedelta(minutes=6)),
                ]
            else:
                events = []
            for i, (etype, ts) in enumerate(events):
                await session.execute(
                    text(
                        "INSERT INTO agent_events (seq, run_id, type, ts)"
                        " VALUES (:seq, :r, :t, :ts)"
                    ),
                    {"seq": minutes_ago * 10 + i, "r": run_id, "t": etype, "ts": ts},
                )
            await session.commit()

    import asyncio as _asyncio

    _asyncio.run(_go())


@pytest.fixture
def seeded_db(db):
    """team-a owns proj-ours with 3 runs; team-b owns proj-theirs with 1."""
    async def _seed_projects():
        async with db() as session:
            await session.execute(
                text(
                    "INSERT INTO projects (id, team_id) VALUES"
                    " ('proj-ours', 'team-a'), ('proj-theirs', 'team-b')"
                )
            )
            await session.commit()

    import asyncio as _asyncio

    _asyncio.run(_seed_projects())
    for args in [
        ("run-1", "proj-ours", "planning", 10),
        ("run-2", "proj-ours", "executing", 20),
        ("run-3", "proj-ours", "pr_opened", 30),
        ("run-f", "proj-theirs", "executing", 40),
    ]:
        _seed_run(db, *args)
    return db


def _principal(
    user_id: str = "u-own", team_id: str = "team-a", role: Role = Role.ADMIN
) -> Principal:
    return Principal(userId=user_id, teamId=team_id, role=role)


def _client(monkeypatch, factory, principal: Principal) -> TestClient:
    from harness_orchestrator import api as orch_api

    monkeypatch.setattr(orch_api, "_service", None)

    class _Stub:
        session_factory = factory

    monkeypatch.setattr(orch_api, "_service", _Stub())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[orch_api.get_principal] = lambda: principal
    return TestClient(app)


# -------------------------------------------------------------------- clamps


def test_default_paging_and_shape(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get("/api/v1/orchestrator/runs")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert body["limit"] == 25 and body["offset"] == 0
    assert [i["id"] for i in body["items"]] == ["run-1", "run-2", "run-3"]
    item = body["items"][0]
    assert set(item) == {
        "id",
        "projectId",
        "status",
        "createdAt",
        "startedAt",
        "finishedAt",
    }


def test_limit_clamped_and_offset_floored(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())

    res = client.get("/api/v1/orchestrator/runs", params={"limit": 500, "offset": -7})
    assert res.status_code == 200
    body = res.json()
    assert body["limit"] == 100 and body["offset"] == 0
    assert len(body["items"]) == 3

    res = client.get("/api/v1/orchestrator/runs", params={"limit": 2})
    body = res.json()
    assert body["total"] == 3
    assert [i["id"] for i in body["items"]] == ["run-1", "run-2"]

    # offset walks the same result set without re-reading dropped rows.
    res = client.get("/api/v1/orchestrator/runs", params={"limit": 2, "offset": 2})
    assert [i["id"] for i in res.json()["items"]] == ["run-3"]


# ------------------------------------------------------------------- filters


def test_project_filter(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get(
        "/api/v1/orchestrator/runs", params={"projectId": "proj-theirs"}
    )
    # proj-theirs belongs to team-b: filtered out by team scoping before the
    # projectId filter even applies — never leaks across teams.
    assert res.json()["total"] == 0


def test_status_filter(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get("/api/v1/orchestrator/runs", params={"status": "executing"})
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "run-2"
    assert body["items"][0]["status"] == "executing"

    res = client.get(
        "/api/v1/orchestrator/runs",
        params={"status": "executing", "projectId": "proj-ours"},
    )
    assert res.json()["total"] == 1


def test_combined_filters_narrow(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get(
        "/api/v1/orchestrator/runs",
        params={"projectId": "proj-ours", "status": "planning"},
    )
    body = res.json()
    assert body["total"] == 1 and body["items"][0]["id"] == "run-1"


# ------------------------------------------------------- derived timestamps


def test_started_finished_derived_from_events(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    items = {i["id"]: i for i in client.get("/api/v1/orchestrator/runs").json()["items"]}

    assert items["run-1"]["startedAt"] is None  # planning: never started
    assert items["run-1"]["finishedAt"] is None

    assert items["run-2"]["startedAt"] is not None  # executing: started only
    assert items["run-2"]["finishedAt"] is None

    assert (
        items["run-3"]["startedAt"] is not None and items["run-3"]["finishedAt"] is not None
    )


# ------------------------------------------------------------ team isolation


def test_foreign_team_runs_invisible(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    ids = [i["id"] for i in client.get("/api/v1/orchestrator/runs?limit=100").json()["items"]]
    assert "run-f" not in ids
    assert len(ids) == 3

    b_client = _client(monkeypatch, seeded_db, _principal(team_id="team-b"))
    res = b_client.get("/api/v1/orchestrator/runs")
    assert [i["id"] for i in res.json()["items"]] == ["run-f"]


def test_viewer_may_read(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal(role=Role.VIEWER))
    assert client.get("/api/v1/orchestrator/runs").status_code == 200


# ---------------------------------------------------------------------- auth


def test_401_without_bearer(seeded_db):
    app = FastAPI()
    app.include_router(router)
    app.state.get_principal = make_bearer_dep(seeded_db)  # real dep, no override
    client = TestClient(app, raise_server_exceptions=False)

    res = client.get("/api/v1/orchestrator/runs")
    assert res.status_code == 401
    assert "bearer" in res.json()["detail"].lower()


def test_401_with_unknown_token(seeded_db):
    app = FastAPI()
    app.include_router(router)
    app.state.get_principal = make_bearer_dep(seeded_db)
    client = TestClient(app)

    res = client.get(
        "/api/v1/orchestrator/runs", headers={"Authorization": "Bearer nope"}
    )
    assert res.status_code == 401


def test_valid_bearer_reaches_data(seeded_db, monkeypatch):
    token = "tok-team-a"

    async def _seed_auth():
        async with seeded_db() as session:
            await session.execute(
                text(
                    "INSERT INTO auth_sessions VALUES (:h, 'u-a', 'team-a', :exp)"
                ),
                {
                    "h": hashlib.sha256(token.encode()).hexdigest(),
                    "exp": datetime.now(UTC) + timedelta(hours=1),
                },
            )
            await session.execute(
                text("INSERT INTO team_members VALUES ('u-a', 'team-a', 'admin')")
            )
            await session.commit()

    import asyncio as _asyncio

    _asyncio.run(_seed_auth())
    from harness_orchestrator import api as orch_api

    class _Stub:
        session_factory = seeded_db

    monkeypatch.setattr(orch_api, "_service", _Stub())
    app = FastAPI()
    app.include_router(router)
    app.state.get_principal = make_bearer_dep(seeded_db)
    client = TestClient(app)

    res = client.get(
        "/api/v1/orchestrator/runs", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3  # team-a scope applied end to end
    assert all(i["projectId"] == "proj-ours" for i in body["items"])
