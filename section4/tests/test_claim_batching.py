"""M20-T1 — claim batching + adaptive worker cadence (the first 10x lever).

Covers (docs/briefs/mission20-graph.md lane T1, against docs/benchmarks/
BASELINE.md's measured wall — one admission per poll cycle ≈ 2 journeys/s):

- BATCH CLAIMS: ``OrchestratorService.poll_once(max_claims=N)`` admits
  min(queued-claimable, N) runs in ONE cycle; every claimed run still walks
  the unchanged pre-spawn gate chain individually (_dispatch_claim).
- LEGACY SHAPE PRESERVED: no-arg ``poll_once()`` still returns str | None
  (every existing caller/test unchanged).
- NO DOUBLE DISPATCH UNDER EXECUTOR SATURATION: a real bounded
  ``EngineRuntime(max_concurrency=1)`` plus a gate-blocked engine — a full
  batch spawns every run exactly once; further polls admit nothing while
  slots are occupied; releasing the gate yields exactly one engine leg per
  run (B5's executor/timeout/breaker untouched).
- ADAPTIVE CADENCE: full batch → floor, partial → hold, drained → exponential
  backoff capped at the configured idle interval (pure ``_next_interval``
  transitions + env knob resolution).
- N2 SLIM-BUS WAKE SEAM: ``RunQueueWorker.wake()`` ends a long backoff sleep
  early (a slim signal substitutes for polling as a timing hint); waking
  before the loop starts is harmless.
- INTEGRATION: the real RunQueueWorker loop drains a queued trio through the
  real service and survives a duck-typed legacy service lacking ``max_claims``
  (TypeError fallback).

Schema note: scratch mirrors per section convention (conftest.py untouched);
same DDL family as unit/test_run_queue.py.
"""

import asyncio
import threading
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.engine_runtime import EngineRuntime
from harness_orchestrator.run_queue import enqueue_run, get_queue_snapshot
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.worker import RunQueueWorker, resolve_worker_claim_batch

_DDL = """
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

_LOOP = None


def run(coro):
    global _LOOP
    if _LOOP is None:
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


class StubEngine:
    """Instant honest completion — mirrors unit/test_run_queue.py's stub."""

    def __init__(self):
        self.calls = []

    def execute_goal(self, goal=None, session_id=None):
        self.calls.append(session_id)
        return {
            "sessionId": session_id,
            "engine": "stub",
            "status": "completed",
            "finalResponse": "done",
        }


@pytest.fixture
def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/batch.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for statement in _DDL.split(";"):
                stmt = statement.strip()
                if stmt:
                    await conn.execute(text(stmt))
            await conn.execute(
                text(
                    "INSERT INTO projects (id, team_id) VALUES "
                    "('proj-a', 'team-a'), ('proj-b', 'team-b')"
                )
            )

    run(_setup())
    yield factory
    run(engine.dispose())


def _enqueue_and_start(svc, factory, run_id, project_id):
    """Full lifecycle up to 'queued': durable plan → CAS approval → enqueue."""
    run(svc.propose_plan(run_id, goal=f"goal {run_id}", project_id=project_id))
    plan_id = svc.runs[run_id]["plan_id"]
    run(svc.approve_plan(run_id, plan_id, "u-test"))
    run(svc.start_execution(run_id))  # queue table present ⇒ enqueued


def _queued_ids(factory):
    return {row["run_id"] for row in run(get_queue_snapshot(factory)) if row["state"] == "queued"}


# ---------------------------------------------------------------- batch claims


def test_one_cycle_claims_min_of_queue_and_batch(db_factory):
    f = db_factory
    engine = StubEngine()
    svc = OrchestratorService(engine=engine, session_factory=f)
    ids = [f"m20-{i}" for i in range(5)]
    for i, rid in enumerate(ids):
        _enqueue_and_start(svc, f, rid, "proj-a" if i % 2 == 0 else "proj-b")
    assert len(_queued_ids(f)) == 5

    claimed = run(svc.poll_once(max_claims=3))
    assert isinstance(claimed, list)
    assert len(claimed) == 3, "one cycle must claim min(queue, batch)"
    assert set(claimed) <= set(ids)
    assert _queued_ids(f) == set(ids) - set(claimed)


def test_full_batch_drains_whole_queue(db_factory):
    f = db_factory
    svc = OrchestratorService(engine=StubEngine(), session_factory=f)
    ids = [f"drain-{i}" for i in range(5)]
    for i, rid in enumerate(ids):
        _enqueue_and_start(svc, f, rid, "proj-a" if i % 2 == 0 else "proj-b")

    claimed = run(svc.poll_once(max_claims=8))
    assert sorted(claimed) == sorted(ids), "batch wider than the queue drains it"
    assert _queued_ids(f) == set()


def test_batch_stops_at_per_team_running_cap(db_factory):
    f = db_factory
    blocking = BlockingGateEngine()
    svc = OrchestratorService(
        engine=blocking,
        session_factory=f,
        engine_runtime=EngineRuntime(max_concurrency=8),
    )
    try:
        # Six runs, ONE team (default max_per_team=4): the cap binds mid-batch
        # (blocking legs keep admitted runs genuinely 'running' while polled).
        for i in range(6):
            _enqueue_and_start(svc, f, f"cap-{i}", "proj-a")

        claimed = run(svc.poll_once(max_claims=8))
        assert len(claimed) == 4, "per-team running cap must hold inside a batch"
        assert _queued_ids(f) == {f"cap-{4}", f"cap-{5}"}
        states = {r["run_id"]: r["state"] for r in run(get_queue_snapshot(f))}
        assert sorted(v for v in states.values() if v == "running") == ["running"] * 4
    finally:
        blocking.gate.set()
        svc.shutdown()


def test_legacy_single_claim_shape_unchanged(db_factory):
    f = db_factory
    engine = StubEngine()
    svc = OrchestratorService(engine=engine, session_factory=f)
    _enqueue_and_start(svc, f, "legacy-1", "proj-a")
    _enqueue_and_start(svc, f, "legacy-2", "proj-b")

    claimed = run(svc.poll_once())
    assert isinstance(claimed, str), "no-arg poll_once keeps the str|None contract"
    assert claimed in {"legacy-1", "legacy-2"}
    again = run(svc.poll_once())
    assert isinstance(again, str) and again != claimed
    assert run(svc.poll_once()) is None  # drained


def test_bad_claim_inside_batch_keeps_earlier_admissions(db_factory):
    f = db_factory
    engine = StubEngine()
    svc = OrchestratorService(engine=engine, session_factory=f)
    _enqueue_and_start(svc, f, "good-1", "proj-a")

    # Poison the NEXT claim attempt: the batch must keep 'good-1' admitted.
    original = svc._dispatch_claim

    async def _poison_next(rid, claim):
        if rid == "good-1":
            return await original(rid, claim)
        raise RuntimeError("boom")

    svc._dispatch_claim = _poison_next
    run(enqueue_run(f, "poison", "proj-b"))
    claimed = run(svc.poll_once(max_claims=4))
    assert claimed == ["good-1"], "one bad claim must not lose earlier admissions"


# --------------------------------------- no double dispatch under saturation


def test_no_double_dispatch_when_executor_saturated(db_factory):
    f = db_factory
    blocking = BlockingGateEngine()
    runtime = EngineRuntime(max_concurrency=1)
    svc = OrchestratorService(
        engine=blocking, session_factory=f, engine_runtime=runtime
    )
    ids = [f"sat-{i}" for i in range(3)]
    try:
        for rid in ids:
            _enqueue_and_start(svc, f, rid, "proj-a")

        claimed = run(svc.poll_once(max_claims=8))
        assert sorted(claimed) == sorted(ids), "all three admitted in ONE cycle"
        # Executor width is 1: runs 2 and 3 sit QUEUED FOR THE EXECUTOR while
        # run 1 blocks its slot. Nothing may dispatch twice meanwhile.
        assert len(blocking.started) == 1
        assert run(svc.poll_once(max_claims=8)) == []
        assert len(blocking.started) == 1, "saturated executor admits no second leg"

        blocking.gate.set()
        for rid in ids:
            run(asyncio.wait_for(asyncio.shield(svc.runs[rid]["task"]), timeout=10))

        assert sorted(blocking.started) == sorted(ids)
        assert len(blocking.started) == len(set(blocking.started)) == 3, (
            "exactly one engine leg per run despite batching + saturation"
        )
    finally:
        blocking.gate.set()
        svc.shutdown()


class BlockingGateEngine:
    """Engine leg that parks on a threading gate (executor saturation)."""

    def __init__(self):
        self.gate = threading.Event()
        self.started = []

    def execute_goal(self, goal=None, session_id=None):
        self.started.append(session_id)
        if not self.gate.wait(timeout=10):
            raise TimeoutError("test gate never opened")
        return {
            "sessionId": session_id,
            "engine": "stub",
            "status": "completed",
            "finalResponse": "released",
        }


# ------------------------------------------------------- adaptive poll cadence


def _worker(**overrides):
    service = overrides.pop("service", None) or _DrainedService()
    defaults = dict(
        poll_interval=0.5,
        claim_batch=8,
        min_poll_seconds=0.01,
        backoff_factor=2.0,
    )
    defaults.update(overrides)
    return RunQueueWorker(service, **defaults)


class _DrainedService:
    """Duck-typed service that never has work (and sweeps like the real one)."""

    def __init__(self):
        self.polls = 0

    async def recover_orphans(self):
        return []

    async def reap_zombies(self):
        return []

    async def poll_once(self, max_claims=None):
        self.polls += 1
        return []


def test_adaptive_interval_full_batch_drops_to_floor():
    w = _worker()
    assert w._next_interval(w.claim_batch, 0.4) == w.min_poll_seconds
    assert w._next_interval(w.claim_batch + 3, w.poll_interval) == w.min_poll_seconds


def test_adaptive_interval_partial_batch_holds():
    w = _worker(poll_interval=0.5, min_poll_seconds=0.01, backoff_factor=2.0)
    # Productive-but-not-saturated: hold the current cadence…
    assert w._next_interval(3, 0.25) == 0.25
    # …but never below the floor and never above the configured idle base.
    assert w._next_interval(1, 0.001) == 0.01
    assert w._next_interval(1, 0.9) == 0.5


def test_adaptive_interval_drained_backs_off_to_idle_ceiling():
    w = _worker(poll_interval=0.5, min_poll_seconds=0.01, backoff_factor=2.0)
    # Real lifecycle: a full batch drops the cadence to the floor, then work
    # drains — backoff climbs geometrically from the floor…
    current = w._next_interval(w.claim_batch, 0.5)
    assert current == 0.01
    seen = [current]
    for _ in range(10):
        current = w._next_interval(0, current)
        seen.append(current)
    assert seen[1] == 0.02 and seen[2] == 0.04, "geometric growth while drained"
    # …and the configured idle cadence (poll_interval) is the hard ceiling.
    assert seen[-1] == 0.5
    assert all(v <= 0.5 for v in seen)


def test_env_knob_resolution(monkeypatch):
    monkeypatch.delenv("WORKER_CLAIM_BATCH", raising=False)
    monkeypatch.setenv("ENGINE_MAX_CONCURRENCY", "5")
    assert resolve_worker_claim_batch() == 5, "unset batch defaults to executor width"

    monkeypatch.setenv("WORKER_CLAIM_BATCH", "3")
    assert resolve_worker_claim_batch() == 3

    monkeypatch.setenv("WORKER_CLAIM_BATCH", "many")
    assert resolve_worker_claim_batch() == 5, "garbage falls back to executor width"

    monkeypatch.setenv("WORKER_CLAIM_BATCH", "0")
    assert resolve_worker_claim_batch() == 5, "out-of-range falls back to executor width"


def test_worker_resolves_knobs_from_constructor_args():
    w = _worker(claim_batch=2, min_poll_seconds=0.02, backoff_factor=4.0)
    assert w.claim_batch == 2
    assert w.min_poll_seconds == 0.02
    assert w.backoff_factor == 4.0


# ------------------------------------------------------------- N2 wake seam


def _pump(seconds):
    run(asyncio.sleep(seconds))


def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        _pump(0.05)
    return False


def test_wake_ends_long_backoff_sleep_early():
    class SlowlyDraining(_DrainedService):
        def __init__(self):
            super().__init__()
            self.first_poll_at = None
            self.second_poll_at = None

        async def poll_once(self, max_claims=None):
            now = time.monotonic()
            if self.first_poll_at is None:
                self.first_poll_at = now
            elif self.second_poll_at is None:
                self.second_poll_at = now
            self.polls += 1
            return []  # drained ⇒ backoff toward the idle ceiling

    svc = SlowlyDraining()
    w = RunQueueWorker(
        svc,
        poll_interval=30.0,  # drained backoff would sleep ~30 s…
        claim_batch=8,
        min_poll_seconds=0.01,
        backoff_factor=2.0,
    )

    async def _kick():
        w.run_forever()

    run(_kick())
    try:
        assert _wait_until(lambda: svc.first_poll_at is not None), "first poll never ran"
        w.wake()  # the slim-bus signal lands right after a drained cycle
        assert _wait_until(lambda: svc.second_poll_at is not None), "wake did not produce a poll"
        gap = svc.second_poll_at - svc.first_poll_at
        assert gap < 15.0, f"wake must cut the 30 s backoff short; took {gap:.1f}s"
    finally:
        w.stop()


def test_wake_before_loop_start_is_harmless():
    w = _worker()
    w.wake()  # no loop yet — must not raise
    assert w._wake is None


def test_worker_loop_drains_real_service_end_to_end(db_factory):
    f = db_factory
    engine = StubEngine()
    svc = OrchestratorService(engine=engine, session_factory=f)
    ids = [f"loop-{i}" for i in range(3)]
    for rid in ids:
        _enqueue_and_start(svc, f, rid, "proj-a")

    w = RunQueueWorker(
        svc,
        poll_interval=0.02,
        claim_batch=8,
        min_poll_seconds=0.01,
    )

    async def _kick():
        w.run_forever()

    run(_kick())
    try:
        assert _wait_until(
            lambda: all(
                svc.runs.get(rid, {}).get("engine_result", {}).get("status") == "completed"
                for rid in ids
            ),
            timeout=10,
        ), f"worker never completed all runs; engine calls={engine.calls}"
        assert sorted(engine.calls) == sorted(ids), "exactly one leg per run"
        assert _queued_ids(f) == set()
    finally:
        w.stop()


def test_worker_survives_legacy_service_without_max_claims():
    class LegacyService(_DrainedService):
        async def poll_once(self):
            self.polls += 1
            return None  # old single-claim signature: no **kwargs

    svc = LegacyService()
    w = RunQueueWorker(
        svc,
        poll_interval=0.02,
        claim_batch=8,
        min_poll_seconds=0.01,
    )

    async def _kick():
        w.run_forever()

    run(_kick())
    try:
        assert _wait_until(lambda: svc.polls >= 2, timeout=10), (
            "TypeError fallback must keep the loop alive and polling"
        )
    finally:
        w.stop()
