"""M13-H1b: heartbeat-guarded claims — SKIP LOCKED claim query, staleness
threshold (HEARTBEAT_TIMEOUT_SECONDS, default 90), zombie reaper semantics,
and healthy-claim safety.

The frozen run_claims schema (0006) types claimed_at/heartbeat_at as UTC ISO
TEXT, so every assertion here compares aware-UTC instants parsed from those
strings — never naive wall-clock bookkeeping.
"""

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from harness_orchestrator.run_queue import (
    _CLAIM_SQL,
    HEARTBEAT_TIMEOUT_SECONDS,
    _inject_skip_locked,
    _stale_predicate,
    claim_next_run,
    enqueue_run,
    get_queue_snapshot,
    heartbeat_age_seconds,
    heartbeat_timed_out,
    release_timed_out_claims,
)
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState


def _utc_now_iso() -> str:
    """Aware-UTC instant as ISO text — same shape the queue stores."""
    return datetime.now(UTC).isoformat()

DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    -- M18-C2 tier 1: cap override (NULL = unset). Scratch mirrors without
    -- this table fail closed at the budget gate and every run blocks before
    -- the engine call (teams unreadable ⇒ degraded denial).
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
    -- M18-C2: the spend window is the DB-clock month. Frozen column (0001).
    -- In-month rows pass this default window, pre-month rows fail it —
    -- identical semantics to date_trunc on PG.
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


@pytest.fixture
def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/hb.db")
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


async def _age_heartbeat(factory, run_id: str, heartbeat_at: str) -> None:
    async with factory() as db:
        await db.execute(
            text("UPDATE run_claims SET heartbeat_at = :h WHERE run_id = :r"),
            {"h": heartbeat_at, "r": run_id},
        )
        await db.commit()


# --------------------------------------------------------------------------
# 1. Claim-query contention: SKIP LOCKED on PG, clean guard on SQLite.
# --------------------------------------------------------------------------
def test_claim_query_contention_skip_locked_guard():
    """PG dialect gets FOR UPDATE OF rc SKIP LOCKED; SQLite never sees the
    clause; a row already claimed ('running') cannot be double-claimed."""
    # The compiled PG statement carries the clause against the rc alias...
    pg_compiled = text(_CLAIM_SQL + "\nFOR UPDATE OF rc SKIP LOCKED").compile(
        dialect=postgresql.dialect()
    )
    assert "FOR UPDATE OF rc SKIP LOCKED" in str(pg_compiled)

    # ...and the runtime guard appends it ONLY for postgresql.
    class FakeBind:
        def __init__(self, name):
            self.dialect = type("D", (), {"name": name})()

    class FakeResult:
        class _M:
            def first(self):
                return None

        def mappings(self):
            return self._M()

    class FakeDB:
        bind = FakeBind("postgresql")

        async def execute(self, *a, **k):
            return FakeResult()

        async def commit(self):
            return None

    captured = {}

    async def fake_exec(sql, params):
        captured["sql"] = str(sql)
        return FakeResult()

    db = FakeDB()
    db.execute = fake_exec
    run(claim_next_run(lambda: _ctx(db), "w1", max_per_team=4))
    assert "FOR UPDATE OF rc SKIP LOCKED" in captured["sql"]

    captured.clear()
    db_sqlite = FakeDB()
    db_sqlite.bind = FakeBind("sqlite")
    db_sqlite.execute = fake_exec
    run(claim_next_run(lambda: _ctx(db_sqlite), "w1", max_per_team=4))
    assert "SKIP LOCKED" not in captured["sql"]

    # Behavioral backstop on a real sqlite queue: two workers contending for
    # one queued run — exactly one wins, the loser gets nothing (no double
    # claim), which SKIP LOCKED guarantees under real PG concurrency.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def _contention():
        async with engine.begin() as conn:
            for statement in DDL.split(";"):
                stmt = statement.strip()
                if stmt:
                    await conn.execute(text(stmt))
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('proj-a', 'team-a')")
            )
        f = async_sessionmaker(engine, expire_on_commit=False)
        assert await enqueue_run(f, "contended", "proj-a") is True
        winner = await claim_next_run(f, "w1", max_per_team=4)
        loser = await claim_next_run(f, "w2", max_per_team=4)
        snap = await get_queue_snapshot(f)
        await engine.dispose()
        return winner, loser, snap

    winner, loser, snap = run(_contention())
    assert winner == {"run_id": "contended", "project_id": "proj-a"}
    assert loser is None
    assert [row["state"] for row in snap] == ["running"]


class _Ctx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _ctx(db):
    return _Ctx(db)


# --------------------------------------------------------------------------
# 2. Staleness boundary: DB-stored instant vs now, threshold default 90.
# --------------------------------------------------------------------------
def test_staleness_boundary_default_90(monkeypatch):
    """heartbeat_timed_out flips exactly at HEARTBEAT_TIMEOUT_SECONDS (default
    90): younger is fresh, >= threshold is timed out, unparseable never lies."""
    monkeypatch.delenv("HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    assert HEARTBEAT_TIMEOUT_SECONDS == 90

    now = datetime.now(UTC)
    assert heartbeat_age_seconds(_utc_now_iso(), now=now) == 0.0
    assert heartbeat_age_seconds(now.isoformat(), now=now) == 0.0
    # Future heartbeat (clock skew): clamped at zero, never negative.
    assert heartbeat_age_seconds((now + timedelta(seconds=30)).isoformat(), now=now) == 0.0

    # Boundary: just under 90 → fresh; exactly 90 → timed out.
    under = (now - timedelta(seconds=89)).isoformat()
    at = (now - timedelta(seconds=90)).isoformat()
    over = (now - timedelta(seconds=91)).isoformat()
    assert heartbeat_timed_out(under) is False
    assert heartbeat_timed_out(at) is True
    assert heartbeat_timed_out(over) is True

    # Naive ISO text (legacy rows) is interpreted as UTC by the repo law.
    naive = (now - timedelta(seconds=90)).replace(tzinfo=None).isoformat()
    assert heartbeat_timed_out(naive) is True

    # Garbage/unparseable values must never read as timed out (fail-open to a
    # human decision, not an automatic reap).
    assert heartbeat_age_seconds("not-a-timestamp") is None
    assert heartbeat_timed_out("not-a-timestamp") is False
    assert heartbeat_timed_out(None) is False


def test_threshold_env_override_read_by_service(tmp_path, monkeypatch):
    """HEARTBEAT_TIMEOUT_SECONDS env var overrides the 90s default on the
    service's reaper threshold."""
    monkeypatch.setenv("HEARTBEAT_TIMEOUT_SECONDS", "37")
    svc = OrchestratorService()
    assert svc.heartbeat_timeout_seconds == 37
    monkeypatch.setenv("HEARTBEAT_TIMEOUT_SECONDS", "90")
    assert OrchestratorService().heartbeat_timeout_seconds == 90


# --------------------------------------------------------------------------
# 3. Zombie reaper: release + BLOCK('heartbeat_lost') + ENGINE_FAILED emit.
# --------------------------------------------------------------------------
def test_reaper_releases_blocks_and_emits(db_factory):
    f = db_factory
    events: list[tuple[str, dict]] = []

    svc = OrchestratorService(run_queue_factory=f)
    svc.on_event = lambda etype, payload: events.append((etype.value, payload))

    run(enqueue_run(f, "zombie-run", "proj-a"))
    assert run(claim_next_run(f, "dead-worker", max_per_team=4)) is not None
    # Dead worker's last beat predates the threshold.
    stale_beat = (datetime.now(UTC) - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS + 5)).isoformat()
    run(_age_heartbeat(f, "zombie-run", stale_beat))

    released = run(svc.reap_zombies())
    assert released == ["zombie-run"]

    snap = run(get_queue_snapshot(f))
    assert snap == []  # claim released (same terminal shape as finish_run)

    # Honest block: state machine moved to BLOCKED and ENGINE_FAILED emitted
    # via the existing eventbus bridge with reason 'heartbeat_lost'.
    sm = svc.runs["zombie-run"]["state_machine"]
    assert sm.state is AgentState.BLOCKED
    failed = [(t, p) for t, p in events if t == "engine_failed"]
    assert len(failed) == 1
    assert failed[0][1]["runId"] == "zombie-run"
    assert failed[0][1]["reason"] == "heartbeat_lost"

    # Released work is requeueable: a new enqueue + claim succeeds cleanly.
    assert run(enqueue_run(f, "zombie-run", "proj-a")) is True
    assert run(claim_next_run(f, "w2", max_per_team=4))["run_id"] == "zombie-run"


# --------------------------------------------------------------------------
# 4. Healthy claims are untouched by the reaper.
# --------------------------------------------------------------------------
def test_reaper_leaves_healthy_claims_untouched(db_factory):
    f = db_factory

    class BlockedEngine:
        """Engine that stays mid-flight until the test releases it.

        The block is a threading.Event (not time.sleep): execute_goal runs on
        the engine executor's THREAD, which task.cancel() cannot interrupt —
        a sleeping thread would make the cleanup below time out raggedly.
        Setting `release` unblocks the thread so the task can resolve.
        """

        def __init__(self) -> None:
            self.release = threading.Event()

        def execute_goal(self, goal=None, session_id=None):
            self.release.wait(timeout=30)

    engine = BlockedEngine()
    svc = OrchestratorService(engine=engine, run_queue_factory=f)
    events: list[tuple[str, dict]] = []
    svc.on_event = lambda etype, payload: events.append((etype.value, payload))

    # Drive a healthy run through the real service path: plan → approve →
    # start → claim → spawn. Its state machine is genuinely EXECUTING and its
    # heartbeat is current.
    run(svc.propose_plan("healthy-run", project_id="proj-a"))
    run(svc.approve_plan("healthy-run", svc.runs["healthy-run"]["plan_id"], "u-test"))
    run(svc.start_execution("healthy-run"))
    claimed = run(svc.poll_once())
    assert claimed == "healthy-run"
    sm = svc.runs["healthy-run"]["state_machine"]
    assert sm.state is AgentState.EXECUTING

    # A queued sibling (never claimed) must also survive the sweep.
    run(enqueue_run(f, "queued-run", "proj-a"))

    assert run(svc.reap_zombies()) == []

    snap = {row["run_id"]: row for row in run(get_queue_snapshot(f))}
    assert set(snap) == {"healthy-run", "queued-run"}
    assert snap["healthy-run"]["state"] == "running"
    assert snap["healthy-run"]["worker_id"] == svc.worker_id
    assert snap["queued-run"]["state"] == "queued"

    # No failure/block emissions were fabricated for live work.
    assert [t for t, _p in events if t == "engine_failed"] == []
    assert svc.runs["healthy-run"]["state_machine"].state is AgentState.EXECUTING

    # Cleanup: cancel the blocked engine task, then release its thread so the
    # cancellation can land (execute_goal runs on an executor thread that
    # task.cancel() cannot interrupt), and pump the shared loop briefly until
    # the task settles. Settling is best-effort by design: when cancellation
    # lands mid-DB-await, the service's finally-block `finish_run` (service.py
    # `_execute`) can await a StaticPool/aiosqlite connection that the
    # abandoned operation poisoned — that future never resolves in this
    # environment (production uses asyncpg pools). All reaper assertions above
    # have already run, so cleanup must not turn environment quirks into red.
    task = svc.runs["healthy-run"].get("task")
    if task is not None and not task.done():
        task.cancel()
    engine.release.set()
    for _ in range(40):  # bounded: ≤ ~2 s of loop pumping
        if task is None or task.done():
            break
        try:
            run(asyncio.sleep(0.05))
        except asyncio.CancelledError:
            pass


# --------------------------------------------------------------------------
# 5. Attempt-2 pins: lock-clause placement, DB-clock staleness, per-instance
#    threshold. All verified live against PostgreSQL 18 (probe evidence).
# --------------------------------------------------------------------------
def test_claim_sql_lock_clause_sits_inside_subquery():
    """FOR UPDATE OF rc SKIP LOCKED lands INSIDE the candidate subquery —
    before statement-level RETURNING, replacing (never duplicating) its
    closing paren. PostgreSQL 18 rejects both the after-RETURNING placement
    and a doubled ')': this shape is what the live probe accepted."""
    injected = _inject_skip_locked(_CLAIM_SQL)
    lock_pos = injected.index("FOR UPDATE OF rc SKIP LOCKED")
    returning_pos = injected.index("RETURNING")
    assert lock_pos < returning_pos, "lock clause must precede RETURNING"
    assert injected.count("FOR UPDATE OF rc SKIP LOCKED") == 1
    # Balanced parens preserved: exactly one ')' immediately before RETURNING.
    before_returning = injected[:returning_pos].rstrip()
    assert before_returning.endswith(")")
    assert before_returning.endswith("\n)") and not before_returning.endswith("\n))")
    # Idempotent seam handling: a template missing the seam passes through
    # unchanged instead of emitting broken SQL.
    assert _inject_skip_locked("SELECT 1") == "SELECT 1"

    pg_compiled = text(injected).compile(dialect=postgresql.dialect())
    rendered = str(pg_compiled)
    assert rendered.index("FOR UPDATE OF rc SKIP LOCKED") < rendered.index("RETURNING")


def test_reaper_staleness_is_decided_by_db_clock(db_factory):
    """release_timed_out_claims judges staleness with the DATABASE's own
    clock: heartbeats are written here by SQL strftime('now', ...) so the
    Python process contributes no timestamp at all."""
    f = db_factory

    async def _seed_db_clock():
        async with f() as db:
            await db.execute(
                text(
                    "INSERT INTO run_claims (run_id, project_id, state, claimed_at,"
                    " heartbeat_at, worker_id) VALUES ('db-old', 'proj-a', 'running',"
                    " strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '-200 seconds'),"
                    " strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '-95 seconds'),"
                    " 'dead-worker')"
                )
            )
            await db.execute(
                text(
                    "INSERT INTO run_claims (run_id, project_id, state, claimed_at,"
                    " heartbeat_at, worker_id) VALUES ('db-new', 'proj-a', 'running',"
                    " strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),"
                    " strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '-10 seconds'),"
                    " 'alive-worker')"
                )
            )
            await db.commit()

    run(_seed_db_clock())
    released = run(release_timed_out_claims(f, timeout_seconds=90))
    assert [row["run_id"] for row in released] == ["db-old"]
    assert released[0]["worker_id"] == "dead-worker"
    states = {row["run_id"]: row["state"] for row in run(get_queue_snapshot(f))}
    assert states == {"db-new": "running"}


def test_reaper_fail_closed_on_unknown_dialect():
    """A dialect with no DB-clock predicate reaps NOTHING (fail closed)
    rather than guessing staleness with the wrong clock."""

    class FakeDialect:
        name = "oracle"

    class FakeBind:
        dialect = FakeDialect()

    class FakeDB:
        bind = FakeBind()

        async def execute(self, *a, **k):  # pragma: no cover - must not be hit
            raise AssertionError("no statement may execute for unknown dialect")

        async def commit(self):  # pragma: no cover
            return None

    assert _stale_predicate("oracle") is None
    assert run(release_timed_out_claims(lambda: _ctx(FakeDB()), timeout_seconds=90)) == []


def test_reaper_uses_per_instance_threshold_not_module_constant(db_factory):
    """The sweep honors THIS service's heartbeat_timeout_seconds: a threshold
    of 30s reaps a 60s-stale claim even though the imported module constant
    says 90 (the pre-fix code read only the frozen module value)."""
    f = db_factory
    svc = OrchestratorService(run_queue_factory=f)
    svc.heartbeat_timeout_seconds = 30
    assert HEARTBEAT_TIMEOUT_SECONDS == 90  # module default untouched

    run(enqueue_run(f, "mid-stale", "proj-a"))
    run(claim_next_run(f, "dead-worker", max_per_team=4))
    beat = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    run(_age_heartbeat(f, "mid-stale", beat))

    assert run(svc.reap_zombies()) == ["mid-stale"]
    sm = svc.runs["mid-stale"]["state_machine"]
    assert sm.state is AgentState.BLOCKED
