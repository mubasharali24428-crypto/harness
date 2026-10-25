"""M17-B5 (PBC1 F2) regression tests: dedicated bounded engine executor,
per-run deadline, and the hung-provider circuit breaker.

Gate law: the original finding's probe must FAIL pre-fix. Pre-fix, engine
calls rode ``asyncio.to_thread`` with NO deadline and NO breaker, so:

* Bound probes — pre-fix there was no explicit pool to interrogate at all;
  these pin the dedicated executor's identity AND its hard ceiling.
* test_run_timeout_fails_run_releases_claim_and_emits — pre-fix a hung
  engine NEVER settled: no ENGINE_FAILED, claim held until the reaper.
* The whole breaker section — pre-fix nothing failed fast; every new run
  piled onto the dead provider until the (invisible) pool saturated.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.engine_runtime import (
    EngineRuntime,
    ProviderCircuitBreaker,
    provider_key_for,
    resolve_engine_max_concurrency,
    resolve_engine_run_timeout_seconds,
)
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState

# --------------------------------------------------------------------------
# sqlite queue shape — frozen DDL, mirrors tests/test_heartbeat_claims.py.
# --------------------------------------------------------------------------
DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    -- M18-C2 tier 1: the cap override lives here (NULL = unset). Scratch
    -- mirrors without this column fail-closed at the budget gate and every
    -- run blocks before the engine call (teams unreadable ⇒ degraded).
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
    -- M18-C2: the spend window is the DB-clock month. The mirror carries the
    -- frozen column (0001). SQLite's date('now','start of month','-1 day')
    -- yields LAST month for any in-month timestamp, so every in-month row
    -- passes and last-month rows fail — identical semantics to date_trunc.
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

_LOOP = None


def run(coro):
    """Sync driver for coroutines (same single-loop discipline as
    tests/test_heartbeat_claims.py)."""
    global _LOOP
    if _LOOP is None:
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


@pytest.fixture()
def queue_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/engine_rt.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for statement in DDL.split(";"):
                stmt = statement.strip()
                if stmt:
                    await conn.execute(text(stmt))
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('proj-a', 'team-a')")
            )

    run(_setup())
    return factory


class ProbeEngine:
    """Counts peak concurrent executions; never blocks longer than needed."""

    backend_runner = None
    model = "probe-model"

    def __init__(self, hold_seconds: float = 0.05):
        self.hold_seconds = hold_seconds
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def execute_goal(self, goal=None, session_id=None):  # noqa: ARG002 - signature
        with self._lock:
            self.calls += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        time.sleep(self.hold_seconds)
        with self._lock:
            self.in_flight -= 1
        return {"status": "completed", "finalResponse": "through", "sessionId": session_id}


class HungEngine:
    """Blocks until released — the honest stand-in for a dead provider.

    Waits on an Event (not time.sleep) so the abandoned OS thread terminates
    promptly at teardown instead of leaking past the test session.
    """

    backend_runner = None
    model = "sleep-model"

    def __init__(self):
        self.release = threading.Event()

    def execute_goal(self, goal=None, session_id=None):  # noqa: ARG002 - signature
        self.release.wait(timeout=120)
        return {"status": "completed", "finalResponse": "late", "sessionId": session_id}


# --------------------------------------------------------------------------
# 1. Env resolution: honest defaults, overrides, garbage rejected.
# --------------------------------------------------------------------------
def test_defaults_are_honest(monkeypatch):
    """Shipped defaults match docs/engine-concurrency.md: bound 8 =
    per-team cap 4 × 2 seeded teams; deadline 600s; breaker 3 × 300s."""
    for name in (
        "ENGINE_MAX_CONCURRENCY",
        "ENGINE_RUN_TIMEOUT_SECONDS",
        "ENGINE_BREAKER_THRESHOLD",
        "ENGINE_BREAKER_COOLDOWN_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    assert resolve_engine_max_concurrency() == 8
    assert resolve_engine_run_timeout_seconds() == 600.0
    rt = EngineRuntime()
    try:
        assert rt.max_concurrency == 8
        assert rt.run_timeout_seconds == 600.0
        assert rt.breaker.threshold == 3
        assert rt.breaker.cooldown_seconds == 300.0
    finally:
        rt.shutdown()


def test_env_overrides_and_garbage(monkeypatch):
    monkeypatch.setenv("ENGINE_MAX_CONCURRENCY", "5")
    monkeypatch.setenv("ENGINE_RUN_TIMEOUT_SECONDS", "12.5")
    assert resolve_engine_max_concurrency() == 5
    assert resolve_engine_run_timeout_seconds() == 12.5
    # Garbage falls back to defaults (same law as HEARTBEAT_TIMEOUT_SECONDS).
    monkeypatch.setenv("ENGINE_MAX_CONCURRENCY", "many")
    monkeypatch.setenv("ENGINE_RUN_TIMEOUT_SECONDS", "later")
    assert resolve_engine_max_concurrency() == 8
    assert resolve_engine_run_timeout_seconds() == 600.0
    # Below-minimum values are ignored too.
    monkeypatch.setenv("ENGINE_MAX_CONCURRENCY", "0")
    monkeypatch.setenv("ENGINE_RUN_TIMEOUT_SECONDS", "-3")
    assert resolve_engine_max_concurrency() == 8
    assert resolve_engine_run_timeout_seconds() == 600.0


# --------------------------------------------------------------------------
# 2. Executor bound is REAL under simulated 20 concurrent runs.
# --------------------------------------------------------------------------
async def test_executor_bound_respected_under_concurrent_runs():
    """20 simultaneous runs against the dedicated 8-slot executor: at most 8
    ever execute concurrently (the rest queue), all complete, and every
    engine thread is a named member of THIS pool."""
    rt = EngineRuntime(max_concurrency=8, run_timeout_seconds=60)
    try:
        known_threads = {t.name for t in threading.enumerate()}
        engine = ProbeEngine(hold_seconds=0.05)
        results = await asyncio.gather(
            *(rt.run(engine, goal=f"g{i}", session_id=f"s{i}") for i in range(20))
        )
        assert all(r["status"] == "completed" for r in results)
        assert engine.calls == 20
        # THE bound: peak concurrency never exceeded the configured slots...
        assert engine.max_in_flight <= 8
        # ...and the pool really is sized 8 with named engine threads only.
        assert rt._executor._max_workers == 8
        new_threads = {t.name for t in threading.enumerate()} - known_threads
        assert len(new_threads) <= 8
        assert all(name.startswith("engine-run_") for name in new_threads)
    finally:
        rt.shutdown()


async def test_service_routes_engine_calls_through_dedicated_executor(queue_factory):
    """The service's execution path runs engine calls on dedicated
    ``engine-run_N`` threads, not asyncio.to_thread's anonymous pool."""
    svc = OrchestratorService(run_queue_factory=queue_factory)
    seen: dict[str, str] = {}

    class NameProbeEngine(ProbeEngine):
        def execute_goal(self, goal=None, session_id=None):  # noqa: ARG002
            seen["thread"] = threading.current_thread().name
            return super().execute_goal(goal=goal, session_id=session_id)

    svc.engine = NameProbeEngine()
    plan_id = await svc.propose_plan("probe-run", project_id="proj-a")
    await svc.approve_plan("probe-run", plan_id, "u-test")
    task = asyncio.create_task(
        svc._execute(svc.runs["probe-run"], svc.planner.get_plan(plan_id))
    )
    await asyncio.wait_for(task, timeout=10)
    assert seen["thread"].startswith("engine-run_"), seen["thread"]


# --------------------------------------------------------------------------
# 3. Timeout path: honest failure + event + released claim.
# --------------------------------------------------------------------------
def test_run_timeout_fails_run_releases_claim_and_emits(queue_factory):
    """A hung engine hits the deadline → ENGINE_FAILED(reason starts
    'timeout'), state BLOCKED for human re-drive, and the run_claims row is
    GONE (released by finish_run in _execute's finally)."""
    f = queue_factory
    events: list[tuple[str, dict]] = []
    engine = HungEngine()
    svc = OrchestratorService(
        engine=engine,
        run_queue_factory=f,
        engine_runtime=EngineRuntime(max_concurrency=2, run_timeout_seconds=0.05),
    )
    svc.on_event = lambda etype, payload: events.append((etype.value, payload))
    try:
        run(svc.propose_plan("hung-run", project_id="proj-a"))
        run(svc.approve_plan("hung-run", svc.runs["hung-run"]["plan_id"], "u-test"))
        run(svc.start_execution("hung-run"))  # queue table present ⇒ queued
        assert run(svc.poll_once()) == "hung-run"
        assert svc.runs["hung-run"]["state_machine"].state is AgentState.EXECUTING

        async def _await_settled():
            task = svc.runs["hung-run"]["task"]
            await asyncio.wait_for(asyncio.shield(task), timeout=10)

        run(_await_settled())

        sm = svc.runs["hung-run"]["state_machine"]
        assert sm.state is AgentState.BLOCKED

        failed = [(t, p) for t, p in events if t == "engine_failed"]
        assert len(failed) == 1
        assert failed[0][1]["runId"] == "hung-run"
        assert failed[0][1]["reason"].startswith("timeout:"), failed[0][1]

        result = svc.runs["hung-run"].get("engine_result") or {}
        assert result.get("status") == "error"
        assert result.get("engine") == "timeout"

        snap = run(_snap(f))
        assert snap == [], "claim must be released after the deadline fires"

        # The timeout fed the breaker streak for this provider|model.
        key = provider_key_for(HungEngine())
        assert svc.engine_runtime.breaker._consecutive_timeouts.get(key) == 1
    finally:
        engine.release.set()  # free the abandoned OS thread NOW
        svc.shutdown()


async def _snap(factory):
    from sqlalchemy import text as _text

    async with factory() as db:
        rows = (await db.execute(_text("SELECT * FROM run_claims"))).mappings().all()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# 4. Circuit breaker opens / closes per config.
# --------------------------------------------------------------------------
def test_breaker_opens_after_threshold_then_fails_fast():
    brk = ProviderCircuitBreaker(threshold=3, cooldown_seconds=60)
    key = "deepseek-official|deepseek-chat"
    brk.check(key)  # closed: no raise
    assert brk.record_timeout(key) is False  # 1/3
    assert brk.is_open(key) is False
    assert brk.record_timeout(key) is False  # 2/3
    assert brk.record_timeout(key) is True  # 3/3 → OPEN
    assert brk.is_open(key) is True
    with pytest.raises(Exception, match="circuit open"):
        brk.check(key)
    remaining = brk.retry_after_seconds(key)
    assert remaining is not None and 0 < remaining <= 60

    # Success clears a streak before it opens (healthy traffic interleaved).
    brk2 = ProviderCircuitBreaker(threshold=3, cooldown_seconds=60)
    brk2.record_timeout(key)
    brk2.record_success(key)
    assert brk2._consecutive_timeouts.get(key) is None


def test_breaker_closes_after_cooldown_half_open():
    brk = ProviderCircuitBreaker(threshold=1, cooldown_seconds=0.05)
    key = "deepseek-official|m"
    assert brk.record_timeout(key) is True
    assert brk.is_open(key) is True
    time.sleep(0.06)
    assert brk.is_open(key) is False  # cool-down elapsed
    brk.check(key)  # half-open: probe allowed, streak restarted at zero
    assert brk._consecutive_timeouts.get(key) == 0


def test_breaker_keys_isolate_providers_and_models():
    brk = ProviderCircuitBreaker(threshold=1, cooldown_seconds=60)
    assert brk.record_timeout("prov-a|m1") is True
    assert brk.is_open("prov-a|m1") is True
    # Another model on the same provider, and another provider entirely,
    # stay CLOSED — one hung endpoint never blocks another.
    assert brk.is_open("prov-a|m2") is False
    assert brk.is_open("prov-b|m1") is False
    brk.check("prov-b|m1")  # no raise


def test_breaker_env_config(monkeypatch):
    monkeypatch.setenv("ENGINE_BREAKER_THRESHOLD", "7")
    monkeypatch.setenv("ENGINE_BREAKER_COOLDOWN_SECONDS", "45")
    brk = ProviderCircuitBreaker()
    assert brk.threshold == 7
    assert brk.cooldown_seconds == 45.0
    monkeypatch.setenv("ENGINE_BREAKER_THRESHOLD", "lots")
    monkeypatch.setenv("ENGINE_BREAKER_COOLDOWN_SECONDS", "soon")
    brk2 = ProviderCircuitBreaker()
    assert brk2.threshold == 3
    assert brk2.cooldown_seconds == 300.0


def test_open_circuit_fails_run_before_spawning_any_work(queue_factory):
    """Circuit OPEN ⇒ the run fails fast (ENGINE_FAILED mentioning the
    circuit), its claim is released, and the engine is NEVER invoked."""
    f = queue_factory
    events: list[tuple[str, dict]] = []

    class NeverCalledEngine(ProbeEngine):
        model = "never-model"

    engine = NeverCalledEngine(hold_seconds=0.0)
    brk = ProviderCircuitBreaker(threshold=1, cooldown_seconds=60)
    rt = EngineRuntime(max_concurrency=2, run_timeout_seconds=60, breaker=brk)
    brk.record_timeout(provider_key_for(engine))  # OPEN for this provider|model
    assert brk.is_open(provider_key_for(engine))

    svc = OrchestratorService(engine=engine, run_queue_factory=f, engine_runtime=rt)
    svc.on_event = lambda etype, payload: events.append((etype.value, payload))

    run(svc.propose_plan("breaker-run", project_id="proj-a"))
    run(svc.approve_plan("breaker-run", svc.runs["breaker-run"]["plan_id"], "u-test"))
    run(svc.start_execution("breaker-run"))
    run(svc.poll_once())

    async def _await_settled():
        await asyncio.wait_for(
            asyncio.shield(svc.runs["breaker-run"]["task"]), timeout=10
        )

    run(_await_settled())

    assert engine.calls == 0, "open circuit must refuse before touching the engine"
    sm = svc.runs["breaker-run"]["state_machine"]
    assert sm.state is AgentState.BLOCKED
    failed = [(t, p) for t, p in events if t == "engine_failed"]
    assert len(failed) == 1
    assert "provider circuit open" in failed[0][1]["reason"]

    snap = run(_snap(f))
    assert snap == [], "claim must be released on the fail-fast path too"


# --------------------------------------------------------------------------
# 5. Key derivation honesty + lifecycle seam.
# --------------------------------------------------------------------------
def test_provider_key_names_the_model_that_would_hang():
    w = DeepSeekHarnessWrapper(model="primary-model")
    assert provider_key_for(w) == "deepseek-official|primary-model"

    class Injected:
        backend_runner = staticmethod(lambda g: g)  # noqa: ARG005 - probe

    assert provider_key_for(Injected()).startswith("injected|")


def test_service_shutdown_releases_executor():
    svc = OrchestratorService()
    rt = svc.engine_runtime
    svc.shutdown()  # must not raise; drops queued executor work
    assert rt._executor._shutdown is True


class KeyEchoEngine:
    """Raises immediately with the configured key inside the message —
    honest stand-in for a provider that echoes auth material."""

    backend_runner = None
    model = "echo-model"
    api_key = "sk-serviceecho-test"

    def execute_goal(self, goal=None, session_id=None):  # noqa: ARG002 - signature
        raise RuntimeError(
            "boom Bearer sk-serviceecho-test endpoint unreachable"
        )


def test_engine_exception_text_is_scrubbed_before_events_and_result(queue_factory):
    """The raw-exception branch of _execute must not leak the provider key
    into either engine_result.finalResponse or the ENGINE_FAILED event."""
    f = queue_factory
    events: list[tuple[str, dict]] = []
    engine = KeyEchoEngine()
    svc = OrchestratorService(
        engine=engine,
        run_queue_factory=f,
        engine_runtime=EngineRuntime(max_concurrency=2, run_timeout_seconds=10),
    )
    svc.on_event = lambda etype, payload: events.append((etype.value, payload))
    try:
        run(svc.propose_plan("echo-run", project_id="proj-a"))
        run(svc.approve_plan("echo-run", svc.runs["echo-run"]["plan_id"], "u-test"))
        run(svc.start_execution("echo-run"))
        assert run(svc.poll_once()) == "echo-run"

        task = svc.runs["echo-run"]["task"]
        run(asyncio.wait_for(asyncio.shield(task), timeout=10))

        failed = [(t, p) for t, p in events if t == "engine_failed"]
        assert len(failed) == 1
        reason = failed[0][1]["reason"]
        assert "sk-serviceecho-test" not in reason, reason
        assert "[redacted]" in reason, reason
        assert "boom" in reason, reason

        result = svc.runs["echo-run"].get("engine_result") or {}
        assert result.get("status") == "error"
        assert "sk-serviceecho-test" not in (result.get("finalResponse") or "")
    finally:
        svc.shutdown()


@pytest.mark.asyncio
async def test_runtime_passes_an_explicit_team_model():
    class ModelProbe:
        backend_runner = None
        model = "default"

        def __init__(self):
            self.models = []

        def execute_goal(self, goal=None, session_id=None, model=None):  # noqa: ARG002
            self.models.append(model)
            return {"status": "completed"}

    runtime = EngineRuntime(max_concurrency=1)
    engine = ModelProbe()
    try:
        await runtime.run(engine, goal="g", session_id="r1")
        await runtime.run(engine, goal="g", session_id="r2", model="team-model")
    finally:
        runtime.shutdown()
    assert engine.models == [None, "team-model"]
