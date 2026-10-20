"""M12-W2 lane E4a + M14-W4 lane F3a: the run aggregate read surface.

The producer half of prId lives in Section 2 (test_pr_open.py there); these
pin the consumer-facing S4 surface:
- GET /api/v1/orchestrator/runs/{runId} answers the FROZEN pair-contract
  W4-C2 shape {runId, state, costUsd?, prId?, url?, steps?} (+ the legacy
  status fields) — camelCase, explicit nulls while sources are absent.
- costUsd sums cost_ledger for the run at exact scale-6 money (H-023).
- prId/url come from agent_runs.pr_id ⟕ pull_requests.pr_url.
- steps are ordered [{seq, type, ts}] summaries of agent_events, capped at
  the LAST 100.
- Unknown run -> 404 NOT_FOUND envelope; foreign team -> the IDENTICAL 404
  (no-oracle law); DB trouble degrades enrichment, never breaks the read.
Seeding/client approach mirrors unit/test_runs_index.py.
"""

import asyncio
from datetime import UTC, datetime, timedelta

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
    """Minimal frozen-schema sqlite DB (0001 tables this aggregate touches)."""
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
                    "CREATE TABLE agent_events ("
                    "seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),"
                    " type VARCHAR(60) NOT NULL, payload JSON NOT NULL,"
                    " ts TIMESTAMP WITH TIME ZONE NOT NULL)"
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
                    " status VARCHAR(20) NOT NULL,"
                    " created_at TIMESTAMP WITH TIME ZONE)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE cost_ledger ("
                    "seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),"
                    " agent_role VARCHAR(20) NOT NULL, model VARCHAR(120) NOT NULL,"
                    " input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,"
                    " cost_usd NUMERIC(12, 6) NOT NULL, latency_ms INTEGER NOT NULL,"
                    " created_at TIMESTAMP WITH TIME ZONE)"
                )
            )

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    asyncio.run(engine.dispose())


def _principal(team: str = "team-a") -> Principal:
    return Principal(userId="u-own", teamId=team, role=Role.ADMIN)


def _client(monkeypatch, factory, seeded: bool = True) -> TestClient:
    if seeded and factory is not None:
        _seed_project(factory, "proj-ours", "team-a")
        _seed_run(factory, "run-with-pr", "proj-ours", pr_id="pr-77")
        async def _pr_row():
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO pull_requests (id, project_id, run_id,"
                        " github_pr_number, pr_url, summary_md, status)"
                        " VALUES ('pr-77', 'proj-ours', 'run-with-pr', 77,"
                        " 'https://github.com/acme/widget/pull/77', '', 'open')"
                    )
                )
                await session.commit()
        asyncio.run(_pr_row())

    svc = OrchestratorService(session_factory=factory)
    monkeypatch.setattr(orch_api, "_service", svc)

    app = FastAPI()
    app.include_router(orch_api.router)
    app.dependency_overrides[orch_api.get_principal] = lambda: _principal()
    return TestClient(app)


def _seed_project(factory, project_id: str, team_id: str) -> None:
    async def _go():
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT OR IGNORE INTO projects (id, team_id) VALUES (:p, :t)"
                ),
                {"p": project_id, "t": team_id},
            )
            await session.commit()

    asyncio.run(_go())


def _seed_run(
    factory,
    run_id: str,
    project_id: str,
    *,
    pr_id: str | None = None,
    team: str = "team-a",
) -> None:
    _seed_project(factory, project_id, team)

    async def _go():
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state, created_at, pr_id)"
                    " VALUES (:r, :proj, 'pr_opened', CURRENT_TIMESTAMP, :p)"
                ),
                {"r": run_id, "proj": project_id, "p": pr_id},
            )
            await session.commit()

    asyncio.run(_go())


def _add_events(factory, run_id: str, count: int, *, start_seq: int | None = None):
    """Insert ``count`` ordered lifecycle-ish events for a run.

    Timestamps ascend strictly with seq (the aggregate's ordered summary must
    show seq order and ts order agreeing).
    """
    base_dt = datetime(2026, 8, 1, tzinfo=UTC)

    async def _go():
        async with factory() as session:
            base = (
                start_seq
                if start_seq is not None
                else (await session.execute(
                    text("SELECT COALESCE(MAX(seq), 0) FROM agent_events WHERE run_id = :r"),
                    {"r": run_id},
                )).scalar_one()
            )
            for i in range(count):
                await session.execute(
                    text(
                        "INSERT INTO agent_events (run_id, type, payload, ts)"
                        " VALUES (:r, :t, :payload, :ts)"
                    ),
                    {
                        "r": run_id,
                        "t": "diff_progress",
                        "payload": f'{{"runId": "{run_id}"}}',
                        "ts": (base_dt + timedelta(seconds=base + i)).isoformat(),
                    },
                )
            await session.commit()

    asyncio.run(_go())


def _add_costs(factory, run_id: str, amounts: list[str]) -> None:
    async def _go():
        async with factory() as session:
            for a in amounts:
                await session.execute(
                    text(
                        "INSERT INTO cost_ledger (run_id, agent_role, model,"
                        " input_tokens, output_tokens, cost_usd, latency_ms)"
                        " VALUES (:r, 'worker', 'test-model', 1, 1, :c, 5)"
                    ),
                    {"r": run_id, "c": a},
                )
            await session.commit()

    asyncio.run(_go())


# ------------------------------------------------------------- E4a originals


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
        "costUsd",
        "prId",
        "url",
        "steps",
        "planId",
        "planApproved",
        "isHumanEditing",
        "executing",
        "engineMode",
        # M18-C2: explicit budget-truth flag — operators read degraded at a
        # glance (flips true when a gate ran fail-closed on a DB error).
        "budget",
        # M22-M2: structured tier-cap upgrade CTA ({tier, cap, capField, used,
        # upgradeTo, cta}); None here — no cap blocked this run.
        "upgrade",
        # Part 10: honest lifecycle {phase, cancelRequested, cancelConfirmed,
        # mutationPossible}.
        "lifecycle",
        # Part 09.010: workspace identity (null unless the engine is isolated).
        "workspace",
    }


def test_get_run_prid_null_when_no_pr(monkeypatch, db):
    client = _client(monkeypatch, db)
    _seed_run(db, "run-bare", "proj-ours", pr_id=None)
    res = client.get("/api/v1/orchestrator/runs/run-bare")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["prId"] is None


def test_status_route_also_carries_prid(monkeypatch, db):
    """The legacy /status read shares the same aggregate (one source of truth)."""
    client = _client(monkeypatch, db)
    res = client.get("/api/v1/orchestrator/runs/run-with-pr/status")
    assert res.status_code == 200, res.text
    assert res.json()["prId"] == "pr-77"


def test_get_run_without_db_wiring_still_answers(monkeypatch):
    """Bare unit mode: no session factory → enrichment fields stay null."""
    client = _client(monkeypatch, None, seeded=False)
    res = client.get("/api/v1/orchestrator/runs/any-run")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["prId"] is None
    assert body["url"] is None
    assert body["costUsd"] is None
    assert body["steps"] == []


def test_aggregate_degrades_on_db_error(monkeypatch):
    """A broken DB must degrade to null enrichment, never break the read."""
    svc = OrchestratorService()

    class BoomFactory:
        def __call__(self):
            raise RuntimeError("db gone")

    svc.session_factory = BoomFactory()
    status = asyncio.run(svc.get_status("r1"))
    assert status["prId"] is None
    assert status["costUsd"] is None
    assert status["steps"] == []


# ------------------------------------------------------------ W4-C2 (F3a)


def test_aggregate_full_shape_cost_pr_url_steps(monkeypatch, db):
    """W4-C2 parity: every field populated from its frozen-schema source."""
    _add_costs(db, "run-with-pr", ["0.000004", "0.000002"])
    client = _client(monkeypatch, db)
    res = client.get("/api/v1/orchestrator/runs/run-with-pr")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["costUsd"] == 0.000006  # NUMERIC-exact sum, scale-6 money
    assert body["prId"] == "pr-77"
    assert body["url"] == "https://github.com/acme/widget/pull/77"
    assert body["steps"] == []
    assert set(body["steps"]) == set()


def test_aggregate_cost_null_when_ledger_empty_and_exact_when_not(monkeypatch, db):
    """No ledger rows -> explicit null; rows -> exact Decimal sum, no drift."""
    client = _client(monkeypatch, db)
    body = client.get("/api/v1/orchestrator/runs/run-with-pr").json()
    assert body["costUsd"] is None

    _add_costs(db, "run-with-pr", ["0.10", "0.20"])
    body = client.get("/api/v1/orchestrator/runs/run-with-pr").json()
    # 0.1 + 0.2 must be exactly 0.3 on the wire (float money died in H-023).
    assert body["costUsd"] == 0.3
    assert repr(body["costUsd"]) == "0.3"


def test_aggregate_steps_ordered_and_capped_at_last_100(monkeypatch, db):
    """150 events -> exactly the LAST 100, wire order = ascending seq/ts."""
    _add_events(db, "run-with-pr", 150, start_seq=1)
    client = _client(monkeypatch, db)
    body = client.get("/api/v1/orchestrator/runs/run-with-pr").json()
    steps = body["steps"]
    assert len(steps) == 100
    seqs = [s["seq"] for s in steps]
    assert seqs == list(range(51, 151))  # last 100, ascending order
    assert all(set(s) == {"seq", "type", "ts"} for s in steps)
    assert all(s["type"] == "diff_progress" for s in steps)
    # Timestamps ascend together with seq (ordered summary, not a scramble).
    tss = [s["ts"] for s in steps]
    assert tss == sorted(tss)


def test_aggregate_steps_empty_list_when_no_events(monkeypatch, db):
    client = _client(monkeypatch, db)
    body = client.get("/api/v1/orchestrator/runs/run-with-pr").json()
    assert body["steps"] == []  # present-and-empty, never a missing key


# ------------------------------------------------------------------- authz


_NOT_FOUND_BODY = {"error": {"code": "NOT_FOUND", "message": "run not found"}}


def test_unknown_run_404_envelope(monkeypatch, db):
    client = _client(monkeypatch, db)
    res = client.get("/api/v1/orchestrator/runs/never-heard-of-it")
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == _NOT_FOUND_BODY


def test_foreign_team_run_same_404_as_unknown(monkeypatch, db):
    """No-oracle law: a foreign team's run is byte-identical to an unknown one."""
    _seed_run(db, "run-theirs", "proj-theirs", pr_id=None, team="team-b")
    client = _client(monkeypatch, db)
    res_foreign = client.get("/api/v1/orchestrator/runs/run-theirs")
    res_unknown = client.get("/api/v1/orchestrator/runs/also-unknown")
    assert res_foreign.status_code == 404
    assert res_unknown.status_code == 404
    # Byte-compare the envelopes — no observable difference across tenants.
    assert res_foreign.content == res_unknown.content
    assert res_foreign.json()["detail"] == _NOT_FOUND_BODY


def test_unknown_run_still_200_in_bare_unit_mode(monkeypatch):
    """Without a DB there is no durable existence to consult: legacy behavior."""
    client = _client(monkeypatch, None, seeded=False)
    assert client.get("/api/v1/orchestrator/runs/any-run").status_code == 200
