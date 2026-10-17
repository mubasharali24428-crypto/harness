"""F111 — replay wiring: settle archives the record; replay reads it back.

Covers the review gap end to end: the exact composed prompt (the S2 anchor's
engine input), the engine result, tool observations, and file observations
survive on the durable trail — and a FRESH service instance (restart
simulation, empty RAM) resolves them with no loss. Absence is always explicit.
"""

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal, router
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.replay_archive import record_root
from harness_orchestrator.run_queue import claim_next_run
from harness_orchestrator.service import OrchestratorService

SECRET = "sekret-api-key-42"

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

PROJECT = "proj-r"
RUN = "run-replay-1"
GOAL = "Implement median_word_length"


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


class _FakeEngine:
    """Live-shaped engine: returns observations + a result that leaks the key
    (proving settle-time redaction), without needing the SDK installed."""

    def __init__(self) -> None:
        self.api_key = SECRET
        self.model = "fake-model"

    def execute_goal(self, goal: str, session_id: str = "s") -> dict:
        return {
            "sessionId": session_id,
            "engine": "fake-live",
            "status": "completed",
            "finalResponse": f"done with {SECRET}",
            "observations": [
                {"kind": "assistant/chunk", "chunkType": "tool_use", "toolName": "exec"},
                {"kind": "assistant/chunk", "chunkType": "tool_result", "text": "1 passed"},
            ],
        }


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


def _make_service(factory, engine=None) -> OrchestratorService:
    return OrchestratorService(
        engine=engine or _FakeEngine(),
        session_factory=factory,
        run_queue_factory=factory,
        bus=_Bus(factory),
    )


async def _drive(svc, run_id: str) -> None:
    plan_id = await svc.propose_plan(run_id, goal=GOAL, project_id=PROJECT)
    await svc.approve_plan(run_id, plan_id, "u1")
    await svc.start_execution(run_id)
    claim = await claim_next_run(svc.run_queue_factory, svc.worker_id, max_per_team=4)
    assert claim is not None and claim["run_id"] == run_id
    assert await svc._dispatch_claim(run_id, claim) is True
    await asyncio.wait_for(svc.runs[run_id]["task"], timeout=5)


async def test_settlement_archives_record_and_restart_resolves_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HARNESS_REPLAY_ROOT", str(tmp_path / "replay"))
    monkeypatch.delenv("CONTEXT_SEARCH_BASE_URL", raising=False)
    engine, factory = await _make_store(tmp_path, "replay")
    try:
        svc = _make_service(factory)
        await _drive(svc, RUN)

        view = await svc.get_run_replay(RUN)
        assert view["available"] is True
        # the exact composed prompt was the goal (bare run) — present, and
        # the leaked secret was redacted before persistence
        assert view["prompt"]["available"] is True
        assert view["prompt"]["text"] == GOAL
        assert view["engineResult"]["status"] == "completed"
        assert SECRET not in json.dumps(view)
        assert "[redacted]" in view["engineResult"]["finalResponse"]
        assert view["redaction"]["applied"] is True
        # engine observations arrived, followed by the event-derived settle mark
        kinds = [o.get("chunkType") or o["kind"] for o in view["observations"]]
        assert "tool_use" in kinds and "tool_result" in kinds
        assert kinds[-1] == "engine_settled"
        # event trail order preserved
        assert [t["type"] for t in view["timeline"]][-1] == "engine_completed"

        # raw bytes on disk NEVER contain the secret
        ref = view["recordRef"]
        raw = (record_root() / ref["path"]).read_bytes()
        assert SECRET.encode() not in raw
        assert b"[redacted]" in raw

        # RESTART: a fresh service with empty RAM still resolves the record
        # purely from the durable trail (contextRef) + disk archive
        restarted = _make_service(factory)
        assert RUN not in restarted.runs
        fresh = await restarted.get_run_replay(RUN)
        assert fresh["available"] is True
        assert fresh["prompt"]["text"] == GOAL
        assert SECRET not in json.dumps(fresh)
    finally:
        await engine.dispose()


async def test_unavailable_gaps_are_explicit_for_unknown_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_REPLAY_ROOT", str(tmp_path / "replay"))
    engine, factory = await _make_store(tmp_path, "gaps")
    try:
        svc = _make_service(factory)
        view = await svc.get_run_replay("run-never-existed")
        assert view["available"] is False
        assert view["timeline"] == []
        assert view["prompt"]["available"] is False
        assert "reason" in view["prompt"]
        assert view["unavailable"] and "reason" in view["unavailable"][0]
    finally:
        await engine.dispose()


async def test_honest_engine_input_is_archived_verbatim(tmp_path, monkeypatch):
    """The archived prompt is the ACTUAL framed engine input, not the raw goal."""
    monkeypatch.setenv("HARNESS_REPLAY_ROOT", str(tmp_path / "replay"))
    monkeypatch.delenv("CONTEXT_SEARCH_BASE_URL", raising=False)
    calls: list[str] = []
    engine, factory = await _make_store(tmp_path, "verbatim")
    try:
        async with factory() as db:
            await db.execute(
                text("INSERT INTO project_rules VALUES (:p, 'Always use pnpm.')"),
                {"p": PROJECT},
            )
            await db.commit()
        svc = _make_service(
            factory,
            engine=DeepSeekHarnessWrapper(
                backend_runner=lambda g: (calls.append(g), "ok")[1]
            ),
        )
        await _drive(svc, RUN)

        view = await svc.get_run_replay(RUN)
        assert calls and view["prompt"]["available"] is True
        assert view["prompt"]["text"] == calls[0]  # byte-identical to what ran
        assert "Always use pnpm." in view["prompt"]["text"]
        assert view["prompt"]["meta"]["framed"] is True
        # F018: the instruction text execution ran with is content-hashed
        assert view["instructions"]["available"] is True
        assert view["instructions"]["bytes"] > 0
        assert len(view["instructions"]["sha256"]) == 64
    finally:
        await engine.dispose()


def test_api_route_shape_and_no_oracle_404(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_REPLAY_ROOT", str(tmp_path / "replay"))

    async def _setup():
        engine, factory = await _make_store(tmp_path, "api")
        svc = _make_service(factory)
        await svc.propose_plan("run-api-r", goal="g", project_id=PROJECT, mode="plan")
        return engine, svc

    engine, svc = asyncio.run(_setup())
    try:
        monkeypatch.setattr(orch_api, "_service", svc)
        app = FastAPI()
        app.include_router(router)

        owner = Principal(userId="u-own", teamId="team-a", role=Role.VIEWER)
        app.dependency_overrides[get_principal] = lambda: owner
        client = TestClient(app)
        r = client.get("/api/v1/orchestrator/runs/run-api-r/replay")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["runId"] == "run-api-r"
        assert body["available"] is False  # proposed but never executed
        assert body["unavailable"]

        foreign = Principal(userId="u-x", teamId="team-b", role=Role.VIEWER)
        app.dependency_overrides[get_principal] = lambda: foreign
        r = client.get("/api/v1/orchestrator/runs/run-api-r/replay")
        assert r.status_code == 404
        assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"
    finally:
        asyncio.run(engine.dispose())
