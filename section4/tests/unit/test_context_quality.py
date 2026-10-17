"""F017 — context-quality diagnostics surface.

The review gap: "Context paths/token drops stored on runs; no complete quality
diagnostic surface." One read now answers: what execution received (framed
prompt, tokens, rules bytes, truncation), what retrieval resolved (paths,
drop counts, budget), which instruction tiers were present/absent, and whether
the whole path degraded — with honest zeros for bare runs and the frozen
no-oracle 404 for foreign/unknown readers.
"""

import asyncio
import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal, router
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.run_queue import claim_next_run
from harness_orchestrator.service import OrchestratorService

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
CREATE TABLE project_rules (project_id VARCHAR(36) PRIMARY KEY, rules_text TEXT NOT NULL);
"""

PROJECT = "proj-q"
GOAL = "Fix retry in @upload.py"


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


def _make_service(factory, calls: list[str], transport=None) -> OrchestratorService:
    return OrchestratorService(
        engine=DeepSeekHarnessWrapper(
            backend_runner=lambda g: (calls.append(g), "ok")[1]
        ),
        session_factory=factory,
        run_queue_factory=factory,
        bus=_Bus(factory),
        context_transport=transport,
    )


async def _drive(svc, run_id: str) -> None:
    plan_id = await svc.propose_plan(run_id, goal=GOAL, project_id=PROJECT)
    await svc.approve_plan(run_id, plan_id, "u1")
    await svc.start_execution(run_id)
    claim = await claim_next_run(svc.run_queue_factory, svc.worker_id, max_per_team=4)
    assert claim is not None and claim["run_id"] == run_id
    assert await svc._dispatch_claim(run_id, claim) is True
    await asyncio.wait_for(svc.runs[run_id]["task"], timeout=5)


async def test_bare_run_reports_honest_zeros_and_degraded(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTEXT_SEARCH_BASE_URL", raising=False)
    engine, factory = await _make_store(tmp_path, "bare")
    try:
        svc = _make_service(factory, [])
        await _drive(svc, "run-q1")
        q = await svc.get_context_quality("run-q1")
        assert q["executionPrompt"]["framed"] is False
        # bare prompt = the goal itself: its own token estimate, zero injected bytes
        assert q["executionPrompt"]["tokensEstimated"] > 0
        assert q["executionPrompt"]["rulesBytes"] == 0
        assert q["executionPrompt"]["contextBytes"] == 0
        assert q["retrieval"]["resolved"] is False
        assert q["retrieval"]["paths"] == []
        assert q["degraded"] is True
        assert q["mode"] == "agent"
    finally:
        await engine.dispose()


async def test_framed_run_reports_sources_drops_and_tiers(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_SEARCH_BASE_URL", "http://ctx.test")
    calls: list[str] = []
    engine, factory = await _make_store(tmp_path, "framed")
    try:
        async with factory() as db:
            await db.execute(
                text("INSERT INTO project_rules VALUES (:p, 'Always use pnpm.')"),
                {"p": PROJECT},
            )
            await db.commit()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "path": "src/upload.py",
                            "lang": "python",
                            "startLine": 1,
                            "endLine": 3,
                            "content": "def upload(p):\n    return p",
                            "score": 0.5,
                        }
                    ]
                },
            )

        svc = _make_service(factory, calls, transport=httpx.MockTransport(handler))
        await _drive(svc, "run-q2")

        q = await svc.get_context_quality("run-q2")
        assert q["executionPrompt"]["framed"] is True
        assert q["executionPrompt"]["tokensEstimated"] > 0
        assert q["executionPrompt"]["rulesBytes"] > 0
        assert q["executionPrompt"]["contextBytes"] > 0
        assert q["executionPrompt"]["contextTruncated"] is False
        assert "src/upload.py" in q["retrieval"]["paths"]
        assert q["retrieval"]["resolved"] is True
        assert "project" in [t["tier"] for t in q["instructions"]["tiers"]]
        assert "directory" in q["instructions"]["absent"]  # stub engine: no workspace
        assert q["degraded"] is False
    finally:
        await engine.dispose()


def test_api_route_shape_and_no_oracle_404(tmp_path, monkeypatch):
    async def _setup():
        engine, factory = await _make_store(tmp_path, "api")
        return engine, factory

    engine, factory = asyncio.run(_setup())
    try:
        svc = _make_service(factory, [])

        async def _seed():
            await svc.propose_plan("run-api-q", goal="g", project_id=PROJECT, mode="plan")

        asyncio.run(_seed())

        monkeypatch.setattr(orch_api, "_service", svc)
        app = FastAPI()
        app.include_router(router)

        owner = Principal(userId="u-own", teamId="team-a", role=Role.VIEWER)
        app.dependency_overrides[get_principal] = lambda: owner
        client = TestClient(app)
        r = client.get("/api/v1/orchestrator/runs/run-api-q/context-quality")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["runId"] == "run-api-q"
        assert body["mode"] == "plan"
        assert body["degraded"] is True  # nothing executed yet

        # foreign team: identical 404 envelope (no-oracle law)
        foreign = Principal(userId="u-x", teamId="team-b", role=Role.VIEWER)
        app.dependency_overrides[get_principal] = lambda: foreign
        r = client.get("/api/v1/orchestrator/runs/run-api-q/context-quality")
        assert r.status_code == 404
        assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"
    finally:
        asyncio.run(engine.dispose())
