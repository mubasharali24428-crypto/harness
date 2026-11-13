"""M10-A4 tests: runs index API (contract M10-C2, server side).

M14-W4 F3a update: the index paginates by CURSOR (?limit=&cursor= -> {items,
nextCursor}) per frozen api-conventions.md; legacy ``?offset=`` is accepted
but ignored during its one-release deprecation window, and ``total`` is gone
(keyset paging has no stable total). Garbage cursors answer 400
INVALID_CURSOR.

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
                    " type VARCHAR(60) NOT NULL, payload TEXT,"
                    " ts TIMESTAMP WITH TIME ZONE NOT NULL)"
                )
            )
            # For make_bearer_dep lookups (auth_sessions ⋈ team_members ⋈ users).
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
            # W16: the shared bearer dependency joins users.is_active, so a
            # fixture that leaves the table out cannot resolve a principal — and
            # a fixture that omits the column would prove a token works when the
            # real schema would refuse it.
            await conn.execute(
                text(
                    "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(200),"
                    " name VARCHAR(200), is_active BOOLEAN NOT NULL DEFAULT 1)"
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

    class _Stub:
        session_factory = factory

    monkeypatch.setattr(orch_api, "_service", _Stub())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[orch_api.get_principal] = lambda: principal
    return TestClient(app)


# -------------------------------------------------------------------- paging


def test_default_paging_and_shape(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get("/api/v1/orchestrator/runs")
    assert res.status_code == 200
    body = res.json()
    assert body["nextCursor"] is None  # fewer than one page: end of collection
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


def test_limit_clamped(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())

    res = client.get("/api/v1/orchestrator/runs", params={"limit": 500})
    assert res.status_code == 200
    assert len(res.json()["items"]) == 3

    res = client.get("/api/v1/orchestrator/runs", params={"limit": 2})
    body = res.json()
    assert [i["id"] for i in body["items"]] == ["run-1", "run-2"]
    assert body["nextCursor"]  # more pages exist


# ------------------------------------------------------------ cursor walking


def test_cursor_round_trip_walks_every_row_once(monkeypatch, seeded_db):
    """Full pagination walk: pages chain via nextCursor until it goes null,
    yielding each run exactly once in stable (created_at DESC, id DESC) order.
    """
    _seed_run(seeded_db, "run-4", "proj-ours", "planning", 50)
    client = _client(monkeypatch, seeded_db, _principal())

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = client.get("/api/v1/orchestrator/runs", params=params).json()
        seen.extend(i["id"] for i in body["items"])
        pages += 1
        if body["nextCursor"] is None:
            break
        assert body["nextCursor"] != cursor  # cursor must advance
        cursor = body["nextCursor"]
        assert pages < 10  # safety: never loop forever

    assert seen == ["run-1", "run-2", "run-3", "run-4"]
    assert pages == 2  # two full pages of two, second page ends the collection


def test_cursor_pages_are_stable_against_inserts(monkeypatch, seeded_db):
    """A new run landing between pages must not shift the keyset window."""
    client = _client(monkeypatch, seeded_db, _principal())
    first = client.get("/api/v1/orchestrator/runs", params={"limit": 2}).json()
    assert [i["id"] for i in first["items"]] == ["run-1", "run-2"]

    # Newest-possible row arrives AFTER the caller took their first page.
    _seed_run(seeded_db, "run-new", "proj-ours", "planning", 1)

    second = client.get(
        "/api/v1/orchestrator/runs",
        params={"limit": 2, "cursor": first["nextCursor"]},
    ).json()
    # Offset paging would have skipped run-3 here; the cursor does not.
    assert [i["id"] for i in second["items"]] == ["run-3"]
    assert second["nextCursor"] is None


def test_cursor_respects_filters(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    # team-a owns exactly one 'executing' run: a single-row collection must
    # end after one page (nextCursor null), never fabricate another page.
    first = client.get(
        "/api/v1/orchestrator/runs", params={"status": "executing", "limit": 1}
    ).json()
    assert [i["id"] for i in first["items"]] == ["run-2"]
    assert first["nextCursor"] is None

    # With two matching runs the cursor walk stays scoped to the filter AND
    # the team — run-f (team-b) is invisible across the boundary. run-2b is
    # seeded older than run-2 so it lands on page two.
    _seed_run(seeded_db, "run-2b", "proj-ours", "executing", 25)
    first = client.get(
        "/api/v1/orchestrator/runs", params={"status": "executing", "limit": 1}
    ).json()
    assert [i["id"] for i in first["items"]] == ["run-2"]
    second = client.get(
        "/api/v1/orchestrator/runs",
        params={"status": "executing", "limit": 1, "cursor": first["nextCursor"]},
    ).json()
    assert [i["id"] for i in second["items"]] == ["run-2b"]
    assert second["nextCursor"] is None


def test_garbage_cursor_answers_400_invalid_cursor(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get("/api/v1/orchestrator/runs", params={"cursor": "not-a-cursor"})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["error"]["code"] == "INVALID_CURSOR"


def test_legacy_offset_accepted_but_ignored(monkeypatch, seeded_db):
    """One-release deprecation window: offset neither errors nor applies."""
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get(
        "/api/v1/orchestrator/runs", params={"limit": 2, "offset": 99}
    )
    assert res.status_code == 200
    body = res.json()
    assert [i["id"] for i in body["items"]] == ["run-1", "run-2"]  # page ONE


# ------------------------------------------------------------------- filters


def test_project_filter(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get(
        "/api/v1/orchestrator/runs", params={"projectId": "proj-theirs"}
    )
    # proj-theirs belongs to team-b: filtered out by team scoping before the
    # projectId filter even applies — never leaks across teams.
    assert res.json()["items"] == []


def test_status_filter(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get("/api/v1/orchestrator/runs", params={"status": "executing"})
    body = res.json()
    assert [i["id"] for i in body["items"]] == ["run-2"]
    assert body["items"][0]["status"] == "executing"

    res = client.get(
        "/api/v1/orchestrator/runs",
        params={"status": "executing", "projectId": "proj-ours"},
    )
    assert [i["id"] for i in res.json()["items"]] == ["run-2"]


def test_combined_filters_narrow(monkeypatch, seeded_db):
    client = _client(monkeypatch, seeded_db, _principal())
    res = client.get(
        "/api/v1/orchestrator/runs",
        params={"projectId": "proj-ours", "status": "planning"},
    )
    assert [i["id"] for i in res.json()["items"]] == ["run-1"]


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
            await session.execute(
                text("INSERT INTO users VALUES ('u-a', 'a@example.com', 'A', 1)")
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
    ids = [i["id"] for i in body["items"]]
    assert len(ids) == 3  # team-a scope applied end to end
    assert all(i["projectId"] == "proj-ours" for i in body["items"])


# ------------------------------------------- 2026-11-13: state replayed from events


def _add_events(factory, run_id: str, events: list[tuple[str, str | None]], seq0: int):
    async def _go():
        async with factory() as session:
            for i, (etype, payload) in enumerate(events):
                await session.execute(
                    text(
                        "INSERT INTO agent_events (seq, run_id, type, payload, ts)"
                        " VALUES (:seq, :r, :t, :p, :ts)"
                    ),
                    {"seq": seq0 + i, "r": run_id, "t": etype, "p": payload, "ts": _NOW},
                )
            await session.commit()

    import asyncio as _asyncio

    _asyncio.run(_go())


def test_run_waiting_for_plan_approval_is_not_reported_idle(monkeypatch, seeded_db):
    """The column says idle (it is only written at creation); the log says the plan is waiting."""
    _seed_run(seeded_db, "run-plan", "proj-ours", "idle", 5)
    _add_events(seeded_db, "run-plan", [("plan_proposed", '{"planId": "p1"}')], 9000)
    client = _client(monkeypatch, seeded_db, _principal())
    items = {i["id"]: i for i in client.get("/api/v1/orchestrator/runs").json()["items"]}
    assert items["run-plan"]["status"] == "awaiting_plan_approval"
    res = client.get("/api/v1/orchestrator/runs", params={"status": "awaiting_plan_approval"})
    assert [i["id"] for i in res.json()["items"]] == ["run-plan"]
    res = client.get("/api/v1/orchestrator/runs", params={"status": "idle"})
    assert "run-plan" not in [i["id"] for i in res.json()["items"]]


def test_filtered_paging_walks_every_match_once(monkeypatch, seeded_db):
    """With the filter applied after replay, cursors must still visit each match exactly once."""
    for n in range(7):
        rid = f"run-w{n}"
        _seed_run(seeded_db, rid, "proj-ours", "idle", 50 + n)
        _add_events(seeded_db, rid, [("plan_proposed", None)], 10000 + n * 10)
        _seed_run(seeded_db, f"run-x{n}", "proj-ours", "idle", 60 + n)  # never proposed
    client = _client(monkeypatch, seeded_db, _principal())
    seen, cursor = [], None
    while True:
        params = {"status": "awaiting_plan_approval", "limit": 3}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/api/v1/orchestrator/runs", params=params).json()
        seen += [i["id"] for i in body["items"]]
        cursor = body["nextCursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(f"run-w{n}" for n in range(7))
    assert len(seen) == len(set(seen))


from harness_orchestrator.runs_index import replay_state  # noqa: E402


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([], "idle"),
        ([("plan_proposed", None)], "awaiting_plan_approval"),
        ([("plan_proposed", None), ("plan.rejected", None)], "idle"),
        ([("execution_started", None), ("engine_completed", None)], "awaiting_done_signal"),
        # done gate opened by the engine, then review passes: finished
        (
            [("execution_started", None), ("engine_completed", None),
             ("done_signal_received", None), ("review_passed", None)],
            "done",
        ),
        # review passes without the gate: the legacy loop continues
        ([("execution_started", None), ("review_passed", None)], "executing"),
        # engine_completed only counts from executing
        ([("engine_completed", None)], "idle"),
        # stop tombstone is terminal; later events cannot resurrect the run
        (
            [("execution_started", None), ("paused", '{"reason": "user_requested_stop"}'),
             ("resumed", None)],
            "done",
        ),
        ([("execution_started", None), ("paused", '{"reason": "user_requested"}')], "paused"),
        ([("execution_started", None), ("engine_failed", None)], "blocked"),
        ([("execution_started", None), ("pr_opened", None)], "pr_opened"),
        ([("paused", {"stopped": True})], "done"),
    ],
)
def test_replay_state_follows_the_state_machine(events, expected):
    assert replay_state(events, "idle") == expected
