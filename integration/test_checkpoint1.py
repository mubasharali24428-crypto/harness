"""Checkpoint 1 — headless integration of Sections 1, 3, 4, 5.

Proves: protocol conformance to frozen contracts, event semantics ⊆ contracts,
S1 bus persistence + replay of an S4 run, S3 powering S4's reviewer,
S5 executing S4's engine work.
"""

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "section1" / "src"))
sys.path.insert(0, str(ROOT / "section3" / "src"))
sys.path.insert(0, str(ROOT / "section4" / "src"))
sys.path.insert(0, str(ROOT / "section5" / "src"))

import harness_contracts as contracts
from harness_contracts.events import EventType
from harness_contracts.signatures import ModelRouter, Orchestrator, Sandbox

from harness_identity.eventbus import EventBus
from harness_identity.models import Base as S1Base
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.reviewer import ReviewerSubAgent
from harness_orchestrator.service import OrchestratorService
from harness_router.providers import ModelProviderClient
from harness_router.service import ModelRouterService
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService


@pytest.fixture(scope="session")
def event_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def bus():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(S1Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield EventBus(factory)
    await engine.dispose()


@pytest.fixture
def router():
    client = ModelProviderClient(api_keys={"deepseek": "test-key"})
    client.set_mock_response("", "Review looks clean; changes align with the brief.")
    return ModelRouterService(client=client)


@pytest.fixture
def sandbox(tmp_path):
    return SandboxService(runner=LocalSandboxRunner(storage_root=tmp_path / "tasks"))


def sandbox_backend(sandbox_svc):
    """S4 engine backend backed by S5: runs a real command inside the sandbox."""

    import asyncio
    import concurrent.futures

    def run(goal: str) -> str:
        handle = sandbox_svc.run_task("int-001", "org/repo", "agent/demo", [])

        async def collect():
            chunks = []
            async for chunk in sandbox_svc.runner.exec(handle, "echo integration-ok"):
                chunks.append(chunk)
            return "".join(chunks)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            output = pool.submit(lambda: asyncio.run(collect())).result()
        sandbox_svc.destroy(handle)
        return f"{goal} => {output.strip()}"

    return run


@pytest.mark.asyncio
async def test_protocols_conform_to_contracts(router, sandbox):
    assert isinstance(router, ModelRouter)
    assert isinstance(sandbox, Sandbox)


@pytest.mark.asyncio
async def test_checkpoint1_full_flow(bus, router, sandbox):
    emitted: list[tuple[str, dict]] = []

    reviewer = ReviewerSubAgent(model_router=router)
    orchestrator = OrchestratorService(
        reviewer=reviewer,
        engine=DeepSeekHarnessWrapper(backend_runner=sandbox_backend(sandbox)),
        on_event=lambda etype, payload: emitted.append((etype, payload)),
    )

    plan_id = await orchestrator.propose_plan("run-int-001")
    assert plan_id
    await orchestrator.approve_plan("run-int-001", plan_id, principal_user_id="u1")
    await orchestrator.start_execution("run-int-001")
    findings = await orchestrator.signal_done(
        "run-int-001", diff_text="+print('hello from agent')"
    )
    assert findings.passed

    sm = orchestrator.runs["run-int-001"]["state_machine"]
    sm.open_pr({"number": 7, "url": "https://github.com/org/repo/pull/7"})

    names = [e.value if hasattr(e, "value") else e for e, _ in emitted]
    assert names == [
        "plan_proposed",
        "plan_approved",
        "execution_started",
        "done_signal_received",
        "review_passed",
        "pr_opened",
    ]
    assert set(names) <= {t.value for t in contracts.EVENT_TYPES}

    for etype, payload in emitted:
        await bus.publish(EventType(etype), "proj-int", payload, run_id="run-int-001")

    replayed = await bus.replay("proj-int", after_seq=0)
    assert [e.type.value for e in replayed] == names
    for env in replayed:
        assert {"type", "payload", "ts", "seq", "projectId", "runId"} == set(env.model_dump())

    review_calls = [c for c in router.call_history if c["role"] == "reviewer"]
    assert len(review_calls) >= 1, "reviewer must call models through Section 3"

    engine_out = orchestrator.engine.execute_goal("wire sections")
    assert "integration-ok" in engine_out["finalResponse"], "engine must run through Section 5"


@pytest.mark.asyncio
async def test_orchestrator_rehydrates_state_from_event_log():
    """dsh-inspired core: agent_events is the source of truth. A fresh S4 process
    with only a DB handle rebuilds run state by replaying persisted events."""
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm, create_async_engine as _cae

    from harness_identity.eventbus import EventBus

    eng = _cae("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await conn.run_sync(S1Base.metadata.create_all)
    factory = _asm(eng, expire_on_commit=False)

    producer_bus = EventBus(factory)
    rid = "run-rehydrate-001"
    await producer_bus.ensure_run("proj-re", rid)
    await producer_bus.publish(
        EventType.PLAN_PROPOSED, "proj-re", {"planId": "plan-rx", "runId": rid}, rid
    )
    await producer_bus.publish(
        EventType.PLAN_APPROVED, "proj-re", {"planId": "plan-rx", "runId": rid}, rid
    )
    await producer_bus.publish(EventType.EXECUTION_STARTED, "proj-re", {"runId": rid}, rid)
    await producer_bus.publish(EventType.PAUSED, "proj-re", {"runId": rid}, rid)

    fresh_service = OrchestratorService(session_factory=factory)  # empty memory, DB only
    status = await fresh_service.get_status(rid)
    assert status["state"] == "paused"
    assert status["planApproved"] is True
    assert status["planId"] == "plan-rx"

    # FK safety: publishing with an unknown run_id must not explode (ensure_run seeds).
    await producer_bus.publish(EventType.RESUMED, "proj-re", {"runId": "brand-new-run"}, "brand-new-run")

    await eng.dispose()



@pytest.mark.asyncio
async def test_cross_instance_live_fanout_via_pg_notify():
    """G-LIVE: two EventBus instances over ONE Postgres DB must still deliver
    live events to each other's subscribers (production runs one bus instance
    per service process). Skips on sqlite drivers (no NOTIFY support)."""
    import os

    if os.environ.get("HARNESS_TEST_PG") != "1":
        pytest.skip("requires Postgres (set HARNESS_TEST_PG=1)")

    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm2, create_async_engine as _cae2

    from sqlalchemy import text

    from harness_identity.eventbus import EventBus

    url = os.environ["HARNESS_DATABASE_URL"]
    eng_a = _cae2(url)
    eng_b = _cae2(url)
    factory_a = _asm2(eng_a, expire_on_commit=False)
    factory_b = _asm2(eng_b, expire_on_commit=False)

    # Seed real parent rows — against PG the frozen FKs are enforced.
    async with eng_a.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO teams (id, name) VALUES ('team-x', 'fanout') "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO projects (id, team_id, name) "
                "VALUES ('proj-x', 'team-x', 'fanout-test') ON CONFLICT (id) DO NOTHING"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO agent_runs (id, project_id, state) "
                "VALUES ('run-x', 'proj-x', 'idle') ON CONFLICT (id) DO NOTHING"
            )
        )

    bus_publisher = EventBus(factory_a)   # lives in "S5's process"
    bus_subscriber = EventBus(factory_b)  # lives in "S1's process"

    q = await bus_subscriber.subscribe("proj-x")

    await bus_publisher.publish(EventType.EXECUTION_STARTED, "proj-x", {"runId": "run-x"}, "run-x")

    env = await asyncio.wait_for(q.get(), timeout=5)
    assert env.type == EventType.EXECUTION_STARTED
    assert env.seq >= 1

    await eng_a.dispose()
    await eng_b.dispose()
