"""M19-N1 — stateless orchestrator phase 1 (lane N1).

Deliverables under test (docs/briefs/mission19-graph.md, PBC1 F1):

- DURABLE-FIRST LIFECYCLE READS: plan approval is read from migration 0013's
  ``agent_plans`` row; the in-RAM planner dict is a cache that converges FROM
  the store. A replica whose cache was never told about another replica's
  decision still sees it.

- APPROVALS AS CAS: service-level approve/reject compare-and-swap the
  version column BEFORE any memory mutation or emission — two concurrent
  decisions produce exactly one winner durably AND exactly one event on the
  bus (replica B cannot double-apply replica A's decision).

- TERMINAL EVICTION: a run whose durable event log replays DONE leaves
  ``service.runs`` (cache-only); runs whose store never persisted their
  lifecycle keep their RAM entry (the dict would be the only witness).
  Evicted terminal runs stay readable (status aggregate + engine result via
  event replay) instead of answering the no-oracle 404 or losing data.

- TWO-REPLICA SIMULATION: the same propose→approve→start sequence driven
  through TWO OrchestratorService instances against ONE SQLite file asserts
  exactly-one-winner / exactly-one-emission at every step.

Schema note: self-contained scratch mirrors per section convention
(conftest.py stays untouched); agent_plans carries migration 0013's version
column so the CAS participates.
"""

import asyncio
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.run_queue import get_queue_snapshot
from harness_orchestrator.service import (
    OrchestratorService,
    PlanAlreadyDecidedError,
)
from harness_orchestrator.state_machine import AgentState

# House clean diff shape (mirrors test_done_signal.CLEAN_DIFF) so the
# deterministic reviewer passes and the done-gate walk reaches DONE.
_CLEAN_DIFF = "--- a/app.py\n+++ b/app.py\n@@\n print('hi')\n+print('bye')\n"


async def _drain_publish_chains(*services) -> None:
    """Await every replica's pending serialized publish chains (service.
    _publish_chains) so fire-and-forget bus persistence lands in the shared
    log BEFORE durable assertions read it. M21-CI repair for the flaky
    exactly-once assertions under CI load: start_execution() returns after the
    durable election but BEFORE its EXECUTION_STARTED publish task commits, so
    an immediate event-log read can race the writer and see zero rows.
    """
    for svc in services:
        chains = list(getattr(svc, "_publish_chains", {}).values())
        if chains:
            await asyncio.gather(*chains, return_exceptions=True)


class RecordingBus:
    """Duck-typed EventBus recording publishes per instance."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        self.published.append(f"{run_id}:{name}")

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        return None


_EVENTS_DDL = (
    "CREATE TABLE agent_events ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " project_id VARCHAR(36),"
    " run_id VARCHAR(36),"
    " type VARCHAR(80) NOT NULL,"
    " payload TEXT NOT NULL DEFAULT '{}',"
    " ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)

# Post-0013 shape (0001 frozen columns + version INTEGER NOT NULL DEFAULT 0).
_PLANS_DDL = (
    "CREATE TABLE agent_plans ("
    "id VARCHAR(36) PRIMARY KEY,"
    " run_id VARCHAR(36) NOT NULL,"
    " goal TEXT NOT NULL,"
    " files_likely_to_change JSON NOT NULL DEFAULT '[]',"
    " steps JSON NOT NULL DEFAULT '[]',"
    " tests JSON NOT NULL DEFAULT '[]',"
    " risks JSON NOT NULL DEFAULT '[]',"
    " estimated_cost_usd REAL NOT NULL DEFAULT 0.05,"
    " forbidden_paths JSON NOT NULL DEFAULT '[]',"
    " expected_result TEXT NOT NULL DEFAULT '',"
    " status VARCHAR(20) NOT NULL,"
    " version INTEGER NOT NULL DEFAULT 0,"
    " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)

_RUN_CLAIMS_DDL = (
    "CREATE TABLE run_claims ("
    "run_id VARCHAR(36) PRIMARY KEY,"
    " project_id VARCHAR(36) NOT NULL,"
    " state VARCHAR(16) NOT NULL,"
    " claimed_at TEXT,"
    " heartbeat_at TEXT,"
    " worker_id TEXT)"
)

_RUNS_DDL = (
    "CREATE TABLE agent_runs ("
    "id VARCHAR(36) PRIMARY KEY,"
    " project_id VARCHAR(36))"
)

# Enrichment + emit-gating companions (service reads these best-effort; the
# scratch mirror provides them so aggregate reads don't log every probe).
_PROJECTS_DDL = (
    "CREATE TABLE projects ("
    "id VARCHAR(36) PRIMARY KEY,"
    " team_id VARCHAR(36))"
)

_AUDIT_DDL = (
    "CREATE TABLE audit_log ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " actor_member_id VARCHAR(36),"
    " actor_kind VARCHAR(16) NOT NULL,"
    " action VARCHAR(80) NOT NULL,"
    " target_type VARCHAR(60) NOT NULL,"
    " target_id VARCHAR(80) NOT NULL,"
    " detail JSON NOT NULL DEFAULT '{}',"
    " ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)

_TEAMS_DDL = (
    "CREATE TABLE teams ("
    "id VARCHAR(36) PRIMARY KEY,"
    " name VARCHAR(200) NOT NULL DEFAULT '',"
    " budget_usd REAL NULL)"
)

# Session 7 schema companions now read by the current orchestrator: daily
# reservations are an idempotent run admission gate, and participants are the
# recipient source for run-scoped notifications. Keep this scratch schema
# aligned with the migrated service even when the test has no roster rows.
_DAILY_RESERVATIONS_DDL = (
    "CREATE TABLE team_daily_run_reservations ("
    "team_id VARCHAR(36) NOT NULL, run_id VARCHAR(36) NOT NULL UNIQUE,"
    " reserved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    " PRIMARY KEY (team_id, run_id))"
)
_RUN_PARTICIPANTS_DDL = (
    "CREATE TABLE run_participants ("
    "run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36) NOT NULL, role VARCHAR(20) NOT NULL,"
    " joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
    " PRIMARY KEY (run_id, user_id))"
)

# signal_done/reviewer companions (best-effort reads in prod schemas).
_PROJECT_RULES_DDL = (
    "CREATE TABLE project_rules ("
    "project_id VARCHAR(36) PRIMARY KEY,"
    " rules_text TEXT NOT NULL DEFAULT '')"
)

_COST_LEDGER_DDL = (
    "CREATE TABLE cost_ledger ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36),"
    " agent_role VARCHAR(20) NOT NULL DEFAULT 'primary',"
    " model VARCHAR(120) NOT NULL DEFAULT 'stub',"
    " input_tokens INTEGER NOT NULL DEFAULT 0,"
    " output_tokens INTEGER NOT NULL DEFAULT 0,"
    " cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,"
    " latency_ms INTEGER NOT NULL DEFAULT 0,"
    " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)


async def _create_schema(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE high_impact_approvals (id TEXT, run_id TEXT, "
            "requester_user_id TEXT, state TEXT)"
        ))
        await conn.execute(text(_EVENTS_DDL))
        await conn.execute(text(_PLANS_DDL))
        await conn.execute(text(_RUN_CLAIMS_DDL))
        await conn.execute(text(_RUNS_DDL))
        await conn.execute(text(_PROJECTS_DDL))
        await conn.execute(text(_AUDIT_DDL))
        await conn.execute(text(_TEAMS_DDL))
        await conn.execute(text(_DAILY_RESERVATIONS_DDL))
        await conn.execute(text(_RUN_PARTICIPANTS_DDL))
        await conn.execute(text(_PROJECT_RULES_DDL))
        await conn.execute(text(_COST_LEDGER_DDL))


@pytest.fixture()
def replica_pair(tmp_path):
    """TWO independent services over ONE sqlite file — the two-replica sim.

    Each replica gets its own engine/bus/worker identity; only the STORE is
    shared, exactly like two orchestrator containers behind one Postgres.
    Buses persist into agent_events (S1 delivery contract: publish persists
    BEFORE fan-out), so lifecycle announcements reach the shared log.
    """
    path = tmp_path / "n1-shared.db"

    async def _build():
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        await _create_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        # The claim SQL JOINs projects; seed the team/project pair the tests use.
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO projects (id, team_id)"
                    " VALUES ('proj-race', 'team-race')"
                )
            )
            await db.commit()
        replicas = [
            OrchestratorService(session_factory=factory, bus=_PersistingBus(factory))
            for _ in range(2)
        ]
        return factory, path, replicas

    factory, db_path, replicas = asyncio.run(_build())
    yield {
        "factory": factory,
        "path": str(db_path),
        "replicas": replicas,
    }
    asyncio.run(_dispose(factory))


async def _dispose(factory) -> None:
    await factory.kw["bind"].dispose()


async def _plan_state(factory, plan_id: str):
    async with factory() as db:
        row = (
            await db.execute(
                text("SELECT status, version FROM agent_plans WHERE id = :p"),
                {"p": plan_id},
            )
        ).first()
    return {"status": row.status if row else None, "version": row.version if row else None}


async def _event_types(factory, run_id: str) -> list[str]:
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT type FROM agent_events WHERE run_id = :r ORDER BY seq ASC"
                ),
                {"r": run_id},
            )
        ).fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# 1. CAS approve race — two concurrent approvals, exactly one wins durably
#    AND exactly one PLAN_APPROVED lands in the shared log.
# ---------------------------------------------------------------------------


async def test_cas_approve_race_exactly_one_winner_one_event(replica_pair):
    factory = replica_pair["factory"]
    replica_a, replica_b = replica_pair["replicas"]

    # Replica A proposes (project-bound so lifecycle events ride the bus);
    # BOTH replicas learn of the plan only through the store.
    plan_id = await replica_a.propose_plan(
        "race-run", goal="n1 cas race", project_id="proj-race"
    )
    state = await _plan_state(factory, plan_id)
    assert state == {"status": "proposed", "version": 0}

    # Both replicas decide CONCURRENTLY from the same undecided version.
    results = await asyncio.gather(
        replica_a.approve_plan("race-run", plan_id, "user-a"),
        replica_b.approve_plan("race-run", plan_id, "user-b"),
        return_exceptions=True,
    )
    winners = [r for r in results if not isinstance(r, BaseException)]
    losers = [r for r in results if isinstance(r, BaseException)]
    assert len(winners) == 1, f"exactly one winner expected, got {results!r}"
    assert all(isinstance(r, PlanAlreadyDecidedError) for r in losers), results

    # The durable decision moved EXACTLY once.
    assert await _plan_state(factory, plan_id) == {
        "status": "approved",
        "version": 1,
    }
    # Exactly one PLAN_APPROVED announcement exists across BOTH replicas'
    # buses (the loser emitted nothing — it never touched its machine).
    approvals = [
        entry
        for entry in replica_a.bus.published + replica_b.bus.published
        if entry.endswith(":plan_approved")
    ]
    assert approvals == ["race-run:plan_approved"]


async def test_mixed_decision_race_reject_wins_late_approve_conflicts(replica_pair):
    """Reverse direction: reject first; a concurrent approve loses cleanly."""
    factory = replica_pair["factory"]
    replica_a, replica_b = replica_pair["replicas"]

    plan_id = await replica_a.propose_plan(
        "mixed-run", goal="n1 mixed race", project_id="proj-race"
    )
    results = await asyncio.gather(
        replica_a.reject_plan("mixed-run", plan_id, "user-a"),
        replica_b.approve_plan("mixed-run", plan_id, "user-b"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, PlanAlreadyDecidedError) for r in results) >= 1
    assert (await _plan_state(factory, plan_id))["version"] == 1
    # The approve loser never flipped its own cache either.
    assert replica_b.planner.is_approved(plan_id) is False


async def test_durable_first_read_beats_stale_cache(replica_pair):
    """A stale/poisoned cache loses to the store in BOTH directions:
    an approval that never happened reads False, and a foreign replica's
    real approval reads True through the mirror row alone."""
    replica_a, replica_b = replica_pair["replicas"]

    plan_id = await replica_a.propose_plan("stale-run", goal="n1 stale cache")

    # Phase 1 — cache claims approved, store disagrees → the STORE wins.
    replica_b.planner.plan_approvals[plan_id] = True
    assert await replica_b._plan_is_approved(plan_id) is False

    # Phase 2 — replica A approves durably; replica B (empty cache) now
    # sees it WITHOUT any cross-process notification.
    await replica_a.approve_plan("stale-run", plan_id, "user-a")
    assert replica_b.planner.is_approved(plan_id) is False  # still blind…
    assert await replica_b._plan_is_approved(plan_id) is True  # …but reads truth


async def test_start_after_foreign_approval_succeeds_on_other_replica(replica_pair):
    """The F1 split-brain scenario: A approves, B (never seeing it in RAM)
    answers the start call — durable-first reads make this work."""
    replica_a, replica_b = replica_pair["replicas"]

    plan_id = await replica_a.propose_plan(
        "handoff-run", goal="n1 handoff", project_id="proj-race"
    )
    await replica_a.approve_plan("handoff-run", plan_id, "user-a")

    class StubEngine:
        def execute_goal(self, goal=None, session_id=None):
            return {
                "sessionId": session_id,
                "engine": "stub",
                "status": "completed",
                "finalResponse": "done",
            }

    replica_b.engine = StubEngine()
    await replica_b.start_execution("handoff-run")  # must NOT raise
    status = await replica_b.get_status("handoff-run")
    assert status["planApproved"] is True


# ---------------------------------------------------------------------------
# 2. Eviction correctness — terminal runs leave RAM only when the event log
#    can reproduce them.
# ---------------------------------------------------------------------------


class _PersistingBus(RecordingBus):
    """Bus double honoring the S1 delivery contract: ``ensure_run`` seeds the
    ``agent_runs`` FK anchor (as section1's EventBus does), ``publish``
    persists each envelope into agent_events BEFORE fan-out."""

    def __init__(self, factory) -> None:
        super().__init__()
        self._factory = factory

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

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        self.published.append(f"{run_id}:{name}")
        async with self._factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_events (project_id, run_id, type, payload)"
                    " VALUES (:p, :r, :t, :payload)"
                ),
                {
                    "p": project_id,
                    "r": run_id,
                    "t": name,
                    # S1-R05c: persist the REAL envelope (was hardcoded "{}") —
                    # payload-blind replay cannot witness reasons (stop
                    # tombstone) or summaries, unlike section1's real bus.
                    "payload": json.dumps(payload or {}, default=str),
                },
            )
            await db.commit()


async def _eviction_replica(tmp_path):
    path = tmp_path / "n1-evict.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    await _create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    bus = _PersistingBus(factory)
    svc = OrchestratorService(session_factory=factory, bus=bus)

    class StubEngine:
        def __init__(self) -> None:
            self.calls = 0

        def execute_goal(self, goal=None, session_id=None):
            self.calls += 1
            return {
                "sessionId": session_id,
                "engine": "stub",
                "status": "completed",
                "finalResponse": "all done",
            }

    svc.engine = StubEngine()
    svc.run_queue_factory = None  # bare-mode spawn path; events persist via bus
    # Project-bound runs so lifecycle events ride the bus (emit gating).
    async with factory() as db:
        await db.execute(
            text("INSERT INTO projects (id, team_id) VALUES ('proj-evict', 'team-x')")
        )
        await db.commit()
    return factory, svc


async def test_terminal_run_with_persisted_log_is_evicted_and_replays(tmp_path):
    factory, svc = await _eviction_replica(tmp_path)
    plan_id = await svc.propose_plan(
        "evict-done", goal="n1 eviction", project_id="proj-evict"
    )
    await svc.approve_plan("evict-done", plan_id, "user-a")
    await svc.start_execution("evict-done")  # stub completes immediately…
    await asyncio.wait_for(svc.runs["evict-done"]["task"], timeout=5)
    await svc.signal_done("evict-done", diff_text=_CLEAN_DIFF)

    # The verdict sealed DONE *and* the persisted log replays it → evicted.
    assert "evict-done" not in svc.runs

    # Every later read replays from the log instead of resurrecting RAM.
    status = await svc.get_status("evict-done")
    assert status["state"] == AgentState.DONE
    result = await svc.get_engine_result("evict-done")
    assert result["status"] == "completed"
    assert "evict-done" not in svc.runs  # reads do NOT resurrect the cache


async def test_terminal_run_without_durable_log_keeps_ram_entry(tmp_path):
    factory, svc = await _eviction_replica(tmp_path)
    svc.bus = RecordingBus()  # non-persisting double: nothing durable lands
    plan_id = await svc.propose_plan(
        "keep-done", goal="n1 no-log keep", project_id="proj-evict"
    )
    await svc.approve_plan("keep-done", plan_id, "user-a")
    await svc.start_execution("keep-done")
    await asyncio.wait_for(svc.runs["keep-done"]["task"], timeout=5)
    await svc.signal_done("keep-done", diff_text=_CLEAN_DIFF)
    assert svc.runs["keep-done"]["state_machine"].state is AgentState.DONE

    # No durable witness → the RAM dict is the only copy; eviction refused.
    assert "keep-done" in svc.runs


async def test_evicted_run_is_not_404_no_oracle_hole(tmp_path):
    """M14-W4 F3a guard: eviction must not turn a real finished run into a
    NOT_FOUND answer just because its agent_runs anchor is absent."""
    factory, svc = await _eviction_replica(tmp_path)
    plan_id = await svc.propose_plan(
        "evict-real", goal="n1 real run", project_id="proj-evict"
    )
    await svc.approve_plan("evict-real", plan_id, "user-a")
    await svc.start_execution("evict-real")
    await asyncio.wait_for(svc.runs["evict-real"]["task"], timeout=5)
    await svc.signal_done("evict-real", diff_text=_CLEAN_DIFF)
    assert "evict-real" not in svc.runs  # evicted…

    status = await svc.get_status("evict-real")  # …yet still a real run
    assert status["state"] is AgentState.DONE


async def test_stop_only_seals_when_the_store_agrees(replica_pair):
    """stop_agent reaches DONE silently (no ENGINE_* event), so with a
    persisting bus the PAUSED envelope + earlier lifecycle DO replay DONE?
    No — they don't: stop-sealed runs are NOT evicted unless a full
    done-gate→REVIEW_PASSED chain exists in the log. This pins the
    conservative direction of the eviction predicate."""
    replica_a, _ = replica_pair["replicas"]
    plan_id = await replica_a.propose_plan("stop-run", goal="n1 stop seal")
    await replica_a.approve_plan("stop-run", plan_id, "user-a")
    # No queue table probe: propose/approve wrote events? Not here — this
    # fixture's bus records but does not persist, so the RAM entry survives.
    await replica_a.stop_agent("stop-run")
    status = await replica_a.get_status("stop-run")
    assert status["state"] == AgentState.DONE
    assert "stop-run" in replica_a.runs  # conservative: kept while unproven


# ---------------------------------------------------------------------------
# 3. Two-replica simulation — same propose→approve→start through BOTH
#    instances against one DB; exactly-one-winner at every step.
# ---------------------------------------------------------------------------


async def test_two_replica_full_sequence_exactly_once_everywhere(replica_pair):
    """Same propose→approve→start sequence driven through BOTH replicas
    against ONE store. Each REQUEST may land on either replica (LB reality);
    every shared-lifecycle step must produce exactly one durable winner."""
    factory = replica_pair["factory"]
    replica_a, replica_b = replica_pair["replicas"]

    # STEP 1 — propose: the request lands on replica A (one caller owns
    # proposing; racing proposes mints two plans by design).
    plan_id = await replica_a.propose_plan(
        "sim-run", goal="n1 dual-replica sim", project_id="proj-race"
    )

    # STEP 2 — approve: BOTH replicas answer concurrently from the same
    # undecided mirror row.
    results = await asyncio.gather(
        replica_a.approve_plan("sim-run", plan_id, "user-a"),
        replica_b.approve_plan("sim-run", plan_id, "user-b"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, PlanAlreadyDecidedError) for r in results) == 1, results
    assert await _plan_state(factory, plan_id) == {
        "status": "approved",
        "version": 1,
    }  # ONE durable winner
    approvals = [
        entry
        for entry in replica_a.bus.published + replica_b.bus.published
        if entry.endswith(":plan_approved")
    ]
    # Same drain rationale as STEP 3 below: bus.publish appends from a
    # fire-and-forget chain task, so settle it before counting emissions.
    await _drain_publish_chains(replica_a, replica_b)
    assert approvals.count("sim-run:plan_approved") == 1  # ONE emission

    # STEP 3 — start: BOTH replicas answer concurrently; the INSERT election
    # admits exactly one EXECUTION_STARTED into the shared log.
    class StubEngine:
        def execute_goal(self, goal=None, session_id=None):
            return {
                "sessionId": session_id,
                "engine": "stub",
                "status": "completed",
                "finalResponse": "sim done",
            }

    replica_a.engine = StubEngine()
    replica_b.engine = StubEngine()
    starts = await asyncio.gather(
        replica_a.start_execution("sim-run"),
        replica_b.start_execution("sim-run"),
        return_exceptions=True,
    )
    assert all(not isinstance(r, BaseException) for r in starts), starts

    # M21-CI repair: the durable election is synchronous but its
    # EXECUTION_STARTED publish is a fire-and-forget chain task — drain both
    # replicas so the shared log is settled before it is asserted on.
    await _drain_publish_chains(replica_a, replica_b)

    types = await _event_types(factory, "sim-run")
    assert types.count("execution_started") == 1  # exactly-one-winner at start

    # The durable queue holds EXACTLY ONE lifecycle for this run.
    snapshot = await get_queue_snapshot(factory)
    sim_rows = [row for row in snapshot if row["run_id"] == "sim-run"]
    assert len(sim_rows) == 1

    # Whichever replica's worker polls first owns the whole execution.
    claims = [await replica_a.poll_once(), await replica_b.poll_once()]
    assert claims.count("sim-run") == 1
    driver = replica_a if claims[0] else replica_b
    rd = driver.runs.get("sim-run")
    if rd is not None and rd.get("task") is not None:
        await asyncio.wait_for(rd["task"], timeout=5)


async def test_two_replica_worker_poll_claims_exactly_once(replica_pair):
    """The queue half of exactly-once: start announces once, the INSERT
    election queues once, and polling BOTH replicas hands execution to
    exactly one of them."""
    factory = replica_pair["factory"]
    replica_a, replica_b = replica_pair["replicas"]

    plan_id = (
        await replica_a.propose_plan(
            "poll-run", goal="n1 poll claim", project_id="proj-race"
        )
    )
    # Approve durably through A; B must see it store-side before starting.
    await replica_a.approve_plan("poll-run", plan_id, "user-a")
    assert await replica_b._plan_is_approved(plan_id) is True

    class StubEngine:
        def execute_goal(self, goal=None, session_id=None):
            return {
                "sessionId": session_id,
                "engine": "stub",
                "status": "completed",
                "finalResponse": "claimed once",
            }

    replica_a.engine = StubEngine()
    replica_b.engine = StubEngine()

    # Start lands on replica B: B announces + enqueues durably.
    await replica_b.start_execution("poll-run")
    snapshot = await get_queue_snapshot(factory)
    rows = [row for row in snapshot if row["run_id"] == "poll-run"]
    assert len(rows) == 1

    # Poll BOTH replicas' workers: exactly one claim, exactly one engine leg.
    claims = [await replica_a.poll_once(), await replica_b.poll_once()]
    assert claims.count("poll-run") == 1

    driver = replica_a if claims[0] else replica_b
    rd = driver.runs.get("poll-run")
    if rd is not None and rd.get("task") is not None:
        await asyncio.wait_for(rd["task"], timeout=5)
    result = await driver.get_engine_result("poll-run")
    assert result["status"] == "completed"


async def test_status_on_the_starting_replica_follows_the_replica_that_ran_it(replica_pair):
    """Part 54 (session 12), found by the two-replica load run: the replica that took the start
    request cached the run as EXECUTING with no local task. When the OTHER replica's worker ran
    it to the done-gate, the starting replica kept answering EXECUTING forever (its cache was
    only fenced after losing a claim it had held), so the client never sent the done-signal."""
    replica_a, replica_b = replica_pair["replicas"]
    plan_id = await replica_b.propose_plan("follow-run", goal="n1 follow", project_id="proj-race")
    await replica_b.approve_plan("follow-run", plan_id, "user-b")

    class StubEngine:
        def execute_goal(self, goal=None, session_id=None):
            return {"sessionId": session_id, "engine": "stub", "status": "completed",
                    "finalResponse": "ran on A"}

    replica_a.engine = StubEngine()
    replica_b.engine = StubEngine()
    await replica_b.start_execution("follow-run")  # B announces + enqueues, keeps a cached copy
    await _drain_publish_chains(replica_b)
    assert (await replica_b.get_status("follow-run"))["state"] == "executing"

    assert await replica_a.poll_once() == "follow-run"  # only A's worker claims it
    await asyncio.wait_for(replica_a.runs["follow-run"]["task"], timeout=5)
    await _drain_publish_chains(replica_a)
    assert (await replica_a.get_status("follow-run"))["state"] == "awaiting_done_signal"
    assert (await replica_b.get_status("follow-run"))["state"] == "awaiting_done_signal"


async def test_a_claim_taken_before_the_start_is_durable_is_requeued_not_lost(replica_pair):
    """Part 54 (session 12), found by the two-replica load run (21 of ~990 runs lost): the
    starting replica inserts the claim (the election) before its EXECUTION_STARTED commits. A
    worker on the other replica that claimed inside that gap replayed "approved, not started",
    judged the claim bogus and deleted it, so the run never ran. It is now requeued and runs."""
    replica_a, replica_b = replica_pair["replicas"]
    plan_id = await replica_b.propose_plan("gap-run", goal="n1 gap", project_id="proj-race")
    await replica_b.approve_plan("gap-run", plan_id, "user-b")
    await _drain_publish_chains(replica_b)

    class StubEngine:
        def execute_goal(self, goal=None, session_id=None):
            return {"sessionId": session_id, "engine": "stub", "status": "completed",
                    "finalResponse": "ran after requeue"}

    replica_a.engine = StubEngine()
    release = asyncio.Event()
    real_publish = replica_b.bus.publish

    async def slow_publish(event_type, project_id, payload=None, run_id=None):
        if getattr(event_type, "value", event_type) == "execution_started":
            await release.wait()  # hold B's announcement back
        await real_publish(event_type, project_id, payload, run_id)

    replica_b.bus.publish = slow_publish
    await replica_b.start_execution("gap-run")  # claim visible, announcement not yet durable
    assert await replica_a.poll_once() is None  # claimed inside the gap: requeued, not deleted
    rows = [r for r in await get_queue_snapshot(replica_pair["factory"]) if r["run_id"] == "gap-run"]
    assert len(rows) == 1 and rows[0]["state"] == "queued"

    release.set()
    await _drain_publish_chains(replica_b)
    assert await replica_a.poll_once() == "gap-run"
    await asyncio.wait_for(replica_a.runs["gap-run"]["task"], timeout=5)
    assert (await replica_a.get_engine_result("gap-run"))["status"] == "completed"


# ---------------------------------------------------------------------------
# S1-R05c — stop is durable: replay honors the stop tombstone
# ---------------------------------------------------------------------------


async def test_stopped_run_reads_done_after_restart(tmp_path):
    """A stopped run must replay as terminal DONE on a fresh replica.

    Live path: stop_agent reaches DONE in RAM (finish() is silent) and emits
    PAUSED(reason=user_requested_stop). Before S1-R05c, replay mapped that
    envelope back to PAUSED — the run looked resumable after a restart and a
    later done-signal could drive it forward.
    """
    factory, svc = await _eviction_replica(tmp_path)
    plan_id = await svc.propose_plan(
        "stop-durable", goal="n1 stop durable", project_id="proj-evict"
    )
    await svc.approve_plan("stop-durable", plan_id, "user-a")
    await svc.start_execution("stop-durable")
    await asyncio.wait_for(svc.runs["stop-durable"]["task"], timeout=5)
    await svc.stop_agent("stop-durable")

    replica_b = OrchestratorService(session_factory=factory, bus=_PersistingBus(factory))
    status = await replica_b.get_status("stop-durable")
    assert status["state"] is AgentState.DONE  # tombstone honored on replay


async def test_replayed_stop_rejects_later_done_signal(tmp_path):
    """Nothing may walk a restarted stopped run forward (DONE is sealed)."""
    from harness_orchestrator.state_machine import InvalidStateTransitionError

    factory, svc = await _eviction_replica(tmp_path)
    plan_id = await svc.propose_plan(
        "stop-sealed", goal="n1 stop sealed", project_id="proj-evict"
    )
    await svc.approve_plan("stop-sealed", plan_id, "user-a")
    await svc.start_execution("stop-sealed")
    await asyncio.wait_for(svc.runs["stop-sealed"]["task"], timeout=5)
    await svc.stop_agent("stop-sealed")

    replica_b = OrchestratorService(session_factory=factory, bus=_PersistingBus(factory))
    with pytest.raises(InvalidStateTransitionError):
        await replica_b.signal_done("stop-sealed", diff_text=_CLEAN_DIFF)
