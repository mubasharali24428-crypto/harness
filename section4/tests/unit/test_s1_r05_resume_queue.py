"""S1-R05d — resume must re-drive a SANDBOX-suspended run in queue mode.

The bug: ``resume_agent`` skipped ``sm.resume()`` whenever the durable
lifecycle head showed execution had started — which is exactly the case for
a paused run. No RESUMED envelope was persisted, the re-queued claim then
replayed PAUSED at dispatch time, failed ``_dispatch_claim``'s EXECUTING
gate, and was silently dropped: the resume vanished. Bare mode never saw it
because an unwired store answers "not started".

These tests run the REAL queue machinery (sqlite store + run_claims +
persisting bus) across a simulated restart (fresh replica, shared store).
"""

import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.run_queue import claim_next_run
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState

DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    budget_usd REAL NULL
);
CREATE TABLE projects (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL
);
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
CREATE TABLE agent_runs (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL
);
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
"""

RUN = "run-q-resume"
PROJECT = "proj-q"


class PersistingBus:
    """Bus double honoring the S1 delivery contract: publish persists the
    real envelope into agent_events BEFORE fan-out."""

    def __init__(self, factory) -> None:
        self._factory = factory
        self.published: list[str] = []

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        self.published.append(f"{run_id}:{name}")
        async with self._factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_events (run_id, type, payload)"
                    " VALUES (:r, :t, :payload)"
                ),
                {
                    "r": run_id,
                    "t": name,
                    "payload": json.dumps(payload or {}, default=str),
                },
            )
            await db.commit()

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        if not project_id:
            return
        async with self._factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id) VALUES (:r, :p)"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"r": run_id, "p": project_id},
            )
            await db.commit()


async def _make_store(tmp_path, tag: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{tag}.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for statement in DDL.split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(text(stmt))
        await conn.execute(
            text("INSERT INTO teams (id, name) VALUES ('team-a', 'Team A')")
        )
        await conn.execute(
            text("INSERT INTO projects (id, team_id) VALUES (:p, 'team-a')"),
            {"p": PROJECT},
        )
    return engine, factory


def _make_service(factory, calls: list[str], delay: float = 0.0):
    def runner(goal: str) -> str:
        calls.append(goal)
        if len(calls) == 1 and delay:
            import time

            time.sleep(delay)
        return f"ran:{goal}#{len(calls)}"

    return OrchestratorService(
        engine=DeepSeekHarnessWrapper(backend_runner=runner),
        session_factory=factory,
        run_queue_factory=factory,
        bus=PersistingBus(factory),
    )


async def _drain_publish_chain(svc, run_id: str) -> None:
    """Await the service's in-flight publish chain (M19-N1 ordering law):
    lifecycle announcements are persisted asynchronously after the RAM
    transition, so the store only witnesses them once the chain resolves."""
    chain = svc._publish_chains.get(run_id)
    if chain is not None:
        try:
            await chain
        except Exception:  # noqa: BLE001 — chain errors are logged, not raised
            pass


async def _event_types(factory, run_id: str) -> list[str]:
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    text("SELECT type FROM agent_events WHERE run_id = :r ORDER BY seq"),
                    {"r": run_id},
                )
            )
            .mappings()
            .all()
        )
    return [row["type"] for row in rows]


async def test_resume_after_restart_redrives_paused_run_in_queue_mode(tmp_path):
    calls: list[str] = []
    engine, factory = await _make_store(tmp_path, "q")
    try:
        svc_a = _make_service(factory, calls, delay=0.3)
        plan_id = await svc_a.propose_plan(RUN, goal="fix bug", project_id=PROJECT)
        await svc_a.approve_plan(RUN, plan_id, "u1")
        await svc_a.start_execution(RUN)
        await svc_a.bus.ensure_run(PROJECT, RUN)

        claim = await claim_next_run(factory, svc_a.worker_id, max_per_team=4)
        assert claim is not None and claim["run_id"] == RUN
        assert await svc_a._dispatch_claim(RUN, claim) is True

        await asyncio.sleep(0.05)  # engine (0.3s) is mid-flight
        await svc_a.pause_agent(RUN)  # PAUSED envelope persisted
        await _drain_publish_chain(svc_a, RUN)
        assert "paused" in await _event_types(factory, RUN)

        # ---- restart: fresh replica, shared store, empty RAM ----
        svc_b = _make_service(factory, calls)
        await svc_b.resume_agent(RUN)
        await _drain_publish_chain(svc_b, RUN)

        # The resume must be DURABLE: RESUMED persisted (pre-fix: skipped).
        assert "resumed" in await _event_types(factory, RUN)

        # The re-queued claim must actually spawn the engine (pre-fix: the
        # dispatch replayed PAUSED, failed the EXECUTING gate, dropped).
        claim2 = await claim_next_run(factory, svc_b.worker_id, max_per_team=4)
        assert claim2 is not None and claim2["run_id"] == RUN
        assert await svc_b._dispatch_claim(RUN, claim2) is True
        assert svc_b.runs[RUN]["state_machine"].state is AgentState.EXECUTING

        await asyncio.wait_for(svc_b.runs[RUN]["task"], timeout=5)
        assert len(calls) == 2  # original leg + resumed continuation
    finally:
        await engine.dispose()


async def test_resume_after_restart_on_done_run_stays_noop(tmp_path):
    """A stopped run must never resurrect through the same queue path."""
    calls: list[str] = []
    engine, factory = await _make_store(tmp_path, "qd")
    try:
        svc_a = _make_service(factory, calls, delay=0.3)
        plan_id = await svc_a.propose_plan(RUN, goal="fix bug", project_id=PROJECT)
        await svc_a.approve_plan(RUN, plan_id, "u1")
        await svc_a.start_execution(RUN)
        await svc_a.bus.ensure_run(PROJECT, RUN)
        claim = await claim_next_run(factory, svc_a.worker_id, max_per_team=4)
        await svc_a._dispatch_claim(RUN, claim)
        await asyncio.sleep(0.05)
        await svc_a.stop_agent(RUN)  # DONE + stop tombstone

        svc_b = _make_service(factory, calls)
        await svc_b.resume_agent(RUN)  # silent no-op (DONE is terminal)
        claim2 = await claim_next_run(factory, svc_b.worker_id, max_per_team=4)
        if claim2 is not None:  # a claim may re-queue; it must NOT spawn
            assert await svc_b._dispatch_claim(RUN, claim2) is False
        assert "resumed" not in await _event_types(factory, RUN)
        assert len(calls) == 1
    finally:
        await engine.dispose()
