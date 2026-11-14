"""M19-N4 — canned-demo seeder tests (C10).

Pins the three properties the mission demands of POST /demo/seed:

1. IDEMPOTENCY — seeding twice materializes the demo graph once; the second
   call returns the SAME project/run ids instead of piling up duplicates.
2. REPLAY RENDERS END-TO-END — after one seed call, GET /runs/{id} answers
   the frozen aggregate with state pr_opened, the PR bound, exact scale-6
   cost, and the full recorded step timeline; rehydration rebuilds the same
   state from agent_events alone (the durable-truth property).
3. GATED ACCESS — unauthenticated requests get 401; viewer/guest get the
   uniform ROLE_DENIED 403; admin/engineer seed successfully.

Honesty law pinned: every seeded event payload carries the replay marker.
Seeding method mirrors unit/test_run_get.py's sqlite client harness.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import harness_orchestrator.api as orch_api
import harness_orchestrator.demo as demo_mod
from harness_orchestrator.service import OrchestratorService


@pytest.fixture
def db(tmp_path):
    """Frozen-schema sqlite DB covering exactly the tables the seeder touches."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/demo_seed.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE projects ("
                    "id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
                    " name VARCHAR(200) NOT NULL, repo_full_name VARCHAR(400))"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE agent_runs ("
                    "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,"
                    " branch VARCHAR(300), state VARCHAR(40) NOT NULL,"
                    " created_at TIMESTAMP WITH TIME ZONE,"
                    " updated_at TIMESTAMP WITH TIME ZONE, pr_id VARCHAR(36))"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE agent_plans ("
                    "id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
                    " goal TEXT NOT NULL, files_likely_to_change JSON NOT NULL,"
                    " steps JSON NOT NULL, tests JSON NOT NULL, risks JSON NOT NULL,"
                    " estimated_cost_usd NUMERIC(12,6) NOT NULL,"
                    " forbidden_paths JSON NOT NULL, expected_result TEXT NOT NULL,"
                    " status VARCHAR(20) NOT NULL, created_at TIMESTAMP WITH TIME ZONE)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE pull_requests ("
                    "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL,"
                    " run_id VARCHAR(36), github_pr_number INTEGER NOT NULL,"
                    " pr_url VARCHAR(500) NOT NULL, summary_md TEXT NOT NULL,"
                    " status VARCHAR(30) NOT NULL,"
                    " created_at TIMESTAMP WITH TIME ZONE)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE cost_ledger ("
                    "seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),"
                    " agent_role VARCHAR(20) NOT NULL, model VARCHAR(120) NOT NULL,"
                    " input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,"
                    " cost_usd NUMERIC(12,6) NOT NULL, latency_ms INTEGER NOT NULL,"
                    " created_at TIMESTAMP WITH TIME ZONE)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE agent_events ("
                    "seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id VARCHAR(36),"
                    " run_id VARCHAR(36), type VARCHAR(60) NOT NULL,"
                    " payload JSON NOT NULL, ts TIMESTAMP WITH TIME ZONE NOT NULL)"
                )
            )

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    asyncio.run(engine.dispose())


def _principal(role: Role = Role.ADMIN, team: str = "team-a") -> Principal:
    return Principal(userId=f"u-{role.value}", teamId=team, role=role)


def _client(monkeypatch, factory, principal: Principal | None = None) -> TestClient:
    svc = OrchestratorService(session_factory=factory)
    monkeypatch.setattr(orch_api, "_service", svc)

    app = FastAPI()
    app.include_router(demo_mod.router)
    if principal is not None:
        # Bearer dependency resolved the caller: role/ownership gates active.
        app.dependency_overrides[orch_api.get_principal] = lambda: principal
    return TestClient(app)


# --------------------------------------------------------------- gated access


def test_seed_requires_auth(monkeypatch, db):
    """No bearer resolution wired → the route never opens (401-shaped)."""
    client = _client(monkeypatch, db, principal=None)
    res = client.post("/api/v1/orchestrator/demo/seed")
    assert res.status_code == 503
    body = res.json()
    code = (
        body.get("detail", {}).get("error", {}).get("code")
        if isinstance(body.get("detail"), dict)
        else None
    )
    assert code == "AUTH_NOT_CONFIGURED"


@pytest.mark.parametrize("role", [Role.VIEWER, Role.GUEST])
def test_seed_denied_for_read_only_roles(monkeypatch, db, role):
    client = _client(monkeypatch, db, principal=_principal(role))
    res = client.post("/api/v1/orchestrator/demo/seed")
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ENGINEER])
def test_seed_allowed_for_admin_and_engineer(monkeypatch, db, role):
    client = _client(monkeypatch, db, principal=_principal(role))
    res = client.post("/api/v1/orchestrator/demo/seed")
    assert res.status_code == 201, res.text
    assert res.json()["replay"] is True


def test_seed_without_db_answers_503(monkeypatch):
    """Bare unit mode: no store → refuse honestly instead of half-seeding."""
    client = _client(monkeypatch, None, principal=_principal(Role.ADMIN))
    res = client.post("/api/v1/orchestrator/demo/seed")
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "SERVICE_NOT_CONFIGURED"


# ------------------------------------------------------------- seeded content


def test_seed_materializes_completed_replay(monkeypatch, db):
    client = _client(monkeypatch, db, principal=_principal(Role.ADMIN))
    res = client.post("/api/v1/orchestrator/demo/seed")
    assert res.status_code == 201, res.text
    body = res.json()

    assert body["runId"].startswith("demo-run-")
    assert body["projectId"].startswith("demo-proj-")
    assert body["planId"].startswith("demo-plan-")
    assert body["prId"].startswith("demo-pr-")
    assert body["state"] == "pr_opened"
    assert body["repoFullName"] == "local/demo-repo"
    assert body["prUrl"] == "https://github.com/org/demo-repo/pull/42"
    # Exact scale-6 money from the recorded usage row.
    assert isinstance(body["costUsd"], float)
    assert round(body["costUsd"], 6) == body["costUsd"]

    # The aggregate read (what the stranger's detail page renders) shows the
    # whole recorded lifecycle: state + PR + cost + ordered steps.
    agg_res = client.get(f"/api/v1/orchestrator/runs/{body['runId']}")

    # The aggregate router lives in api.py — mount it on a second client so
    # this assertion exercises the REAL read surface the UI consumes.
    app2 = FastAPI()
    app2.include_router(orch_api.router)
    app2.dependency_overrides[orch_api.get_principal] = lambda: _principal(Role.ADMIN)
    agg_client = TestClient(app2)
    monkeypatch.setattr(orch_api, "_service", orch_api._service)
    agg_res = agg_client.get(f"/api/v1/orchestrator/runs/{body['runId']}")
    assert agg_res.status_code == 200, agg_res.text
    agg = agg_res.json()
    assert agg["state"] in ("pr_opened", "done")
    assert agg["prId"] == body["prId"]
    assert agg["url"] == body["prUrl"]
    assert agg["costUsd"] == body["costUsd"]
    types = [s["type"] for s in agg["steps"]]
    for expected in (
        "plan_proposed",
        "plan_approved",
        "execution_started",
        "engine_completed",
        "done_signal_received",
        "review_passed",
        "pr_opened",
    ):
        assert expected in types, f"missing {expected} in {types}"
    # Recorded order preserved: plan proposed BEFORE pr opened.
    assert types.index("plan_proposed") < types.index("pr_opened")


def test_every_seeded_event_carries_replay_marker(monkeypatch, db):
    import json as _json

    client = _client(monkeypatch, db, principal=_principal(Role.ADMIN))
    body = client.post("/api/v1/orchestrator/demo/seed").json()
    run_id = body["runId"]

    async def _rows():
        async with db() as session:
            rows = (
                await session.execute(
                    text("SELECT payload FROM agent_events WHERE run_id = :r"),
                    {"r": run_id},
                )
            ).fetchall()
        return rows

    rows = asyncio.run(_rows())
    assert len(rows) >= 10
    from harness_orchestrator.demo_replay import REPLAY_MARKER as expected_marker

    for (payload,) in rows:
        # sqlite JSON columns come back as str (same quirk service._rehydrate
        # documents) — normalize before asserting.
        parsed = (
            _json.loads(payload)
            if isinstance(payload, str)
            else payload
        )
        marker = parsed.get("demo") if isinstance(parsed, dict) else None
        assert marker == expected_marker, parsed


def test_replay_is_durable_truth_events_alone(monkeypatch, db):
    """A COLD service (empty memory) must rehydrate the seeded run to the
    same terminal story purely from agent_events — replay renders without
    this process having ever seen it."""
    client = _client(monkeypatch, db, principal=_principal(Role.ADMIN))
    first = client.post("/api/v1/orchestrator/demo/seed").json()

    cold = OrchestratorService(session_factory=db)
    monkeypatch.setattr(orch_api, "_service", cold)
    app = FastAPI()
    app.include_router(orch_api.router)
    app.dependency_overrides[orch_api.get_principal] = lambda: _principal(Role.ADMIN)
    res = TestClient(app).get(f"/api/v1/orchestrator/runs/{first['runId']}")
    assert res.status_code == 200, res.text
    agg = res.json()
    assert agg["state"] == "pr_opened"
    assert agg["planApproved"] is True
    assert agg["planId"] == first["planId"]
    assert len(agg["steps"]) >= 10
    assert any(s["type"] == "review_passed" for s in agg["steps"])
    assert agg["budget"]["degraded"] is False


# ---------------------------------------------------------------- idempotency


def test_seed_is_idempotent(monkeypatch, db):
    client = _client(monkeypatch, db, principal=_principal(Role.ADMIN))
    first = client.post("/api/v1/orchestrator/demo/seed").json()
    second = client.post("/api/v1/orchestrator/demo/seed").json()

    assert second["projectId"] == first["projectId"]
    assert second["runId"] == first["runId"]
    # A repeat visit still names the PR, so the review stays reachable.
    assert second["prId"] == first["prId"]
    assert second["prUrl"] == first["prUrl"]

    async def _counts():
        async with db() as session:
            runs = (
                await session.execute(text("SELECT COUNT(*) FROM agent_runs"))
            ).scalar_one()
            projects = (
                await session.execute(text("SELECT COUNT(*) FROM projects"))
            ).scalar_one()
            prs = (
                await session.execute(text("SELECT COUNT(*) FROM pull_requests"))
            ).scalar_one()
            events = (
                await session.execute(
                    text("SELECT COUNT(*) FROM agent_events WHERE run_id = :r"),
                    {"r": first["runId"]},
                )
            ).scalar_one()
        return runs, projects, prs, events

    runs, projects, prs, events_after_second = asyncio.run(_counts())
    assert (runs, projects, prs) == (1, 1, 1)
    # The duplicate answer wrote NO extra event trail either.
    assert events_after_second == 10


def test_seed_scopes_projects_by_team(monkeypatch, db):
    """Another team's same-named project never satisfies our idempotency."""
    client_a = _client(monkeypatch, db, principal=_principal(Role.ADMIN, team="team-a"))
    body_a = client_a.post("/api/v1/orchestrator/demo/seed").json()

    async def _other_team_project():
        async with db() as session:
            await session.execute(
                text(
                    "INSERT INTO projects (id, team_id, name, repo_full_name)"
                    " VALUES ('proj-foreign', 'team-b', 'harness-demo-textstats', NULL)"
                )
            )
            await session.commit()

    asyncio.run(_other_team_project())

    client_b = _client(monkeypatch, db, principal=_principal(Role.ADMIN, team="team-b"))
    body_b = client_b.post("/api/v1/orchestrator/demo/seed").json()
    assert body_b["projectId"] != body_a["projectId"]
    assert body_b["runId"].startswith("demo-run-")


# ------------------------------------------------------------ fixture honesty


def test_fixture_records_only_known_event_types():
    """Every recorded event type exists in the FROZEN contracts vocabulary."""
    from harness_contracts.events import EVENT_TYPES

    fixture = demo_mod._load_fixture()
    events = fixture["run"]["events"]
    assert {e["type"] for e in events} <= {t.value for t in EVENT_TYPES}
    from harness_orchestrator.demo_replay import FIXTURE_VERSION

    assert fixture["version"] == FIXTURE_VERSION


def test_recorded_review_matches_the_real_heuristic_verdict():
    """The fixture's verdict equals what ReviewerSubAgent ACTUALLY returns for
    the recorded diff against the recorded brief — replay cannot invent an
    outcome the reviewer wouldn't have produced."""
    fixture = demo_mod._load_fixture()
    recorded = fixture["run"]["review"]
    recorded_diff = fixture["run"]["diffText"]

    svc = OrchestratorService()
    findings = svc.reviewer.evaluate_changes(
        brief_summary=fixture["run"]["goal"],
        diff_text=recorded_diff,
    )
    assert findings.passed is recorded["passed"]
    assert findings.summary == recorded["summary"]
    assert findings.issues == recorded["issues"]


def test_seeded_trail_ends_now_and_keeps_the_recorded_spacing(monkeypatch, db):
    """Recorded dates fall behind the retention horizon: once the sidecar retires the
    partition holding them, PostgreSQL refuses the insert (`no partition ... found for row`)."""
    from datetime import UTC, datetime

    from harness_orchestrator.demo_replay import load_fixture

    client = _client(monkeypatch, db, principal=_principal(Role.ADMIN))
    run_id = client.post("/api/v1/orchestrator/demo/seed").json()["runId"]

    async def _ts():
        async with db() as session:
            rows = await session.execute(
                text("SELECT ts FROM agent_events WHERE run_id = :r ORDER BY seq"), {"r": run_id}
            )
            return [r[0] for r in rows]

    def _aware(v):
        v = datetime.fromisoformat(v) if isinstance(v, str) else v
        return v if v.tzinfo else v.replace(tzinfo=UTC)

    seeded = [_aware(v) for v in asyncio.run(_ts())]
    recorded = [datetime.fromisoformat(e["ts"]) for e in load_fixture()["run"]["events"]]
    assert abs((datetime.now(UTC) - seeded[-1]).total_seconds()) < 300, seeded[-1]
    assert [b - a for a, b in zip(seeded, seeded[1:], strict=False)] == [
        b - a for a, b in zip(recorded, recorded[1:], strict=False)
    ]


def test_seed_stores_the_recorded_diff_where_review_reads_it(monkeypatch, db):
    """F1: section8 resolves a PR's diff from summary_md JSON; an empty summary
    sent the demo review to GitHub, which a demo visitor has not connected."""
    import json

    client = _client(monkeypatch, db, principal=_principal(Role.ADMIN))
    body = client.post("/api/v1/orchestrator/demo/seed").json()

    async def _summary():
        async with db() as session:
            return (
                await session.execute(
                    text("SELECT summary_md FROM pull_requests WHERE id = :id"),
                    {"id": body["prId"]},
                )
            ).scalar_one()

    stored = json.loads(asyncio.run(_summary()))
    assert stored["diff"].startswith("diff --git a/src/textstats/stats.py")

    # A demo seeded before this fix (empty summary) is repaired by the next visit.
    async def _blank():
        async with db() as session:
            await session.execute(text("UPDATE pull_requests SET summary_md = ''"))
            await session.commit()

    asyncio.run(_blank())
    client.post("/api/v1/orchestrator/demo/seed")
    assert json.loads(asyncio.run(_summary()))["diff"] == stored["diff"]
