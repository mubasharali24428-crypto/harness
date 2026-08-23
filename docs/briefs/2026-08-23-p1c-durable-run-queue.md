# TASK: Durable run-execution queue (`run_claims`) — Phase 1 (EXPANSION_PLAN.md)

You are working in the AI Engineering Harness monorepo at the current directory
(`/Users/user/untitled folder 2/harness`). Read `AGENTS.md` first: `contracts/`
is FROZEN (you will NOT touch it, and you will NOT add any new EventType);
schema changes need a NEW numbered migration. Scope: `section4/**`,
`section1/migrations/0006_run_claims.sql`, and minimal wiring in
`deploy/runners/services.py`. Do NOT touch any other section, CI, or docs.
Do NOT commit.

## Problem (verified against current code)

Run execution is fire-and-forget inside one asyncio process:
`OrchestratorService.start_execution()` → `asyncio.create_task(self._execute(...))`.
If the container dies mid-run, the run is lost: on restart the event-log
rehydration leaves the state machine EXECUTING over a dead task, nothing
re-spawns anything, and nobody knows the run died except the missing
terminal event. There is also no concurrency control (a team can start
unbounded parallel runs).

## Established repo patterns you MUST follow

- **Timestamps are UTC ISO-8601 TEXT** in new tables (see
  `section1/migrations/0005_ephemeral_state.sql` and
  `section1/src/harness_identity/ephemeral.py`). Never bind raw datetime
  objects; never use SQL `now()` — SQLite has neither. Copy the `_now_iso()`
  helper pattern.
- Raw SQL via SQLAlchemy `text()` only; must run identically on PostgreSQL
  (prod) and SQLite/aiosqlite (tests). `RETURNING` is available on both.
- Section 4 venv: `cd section4 && ./.venv/bin/python -m pytest tests/ -q`,
  `./.venv/bin/ruff check src tests`. Ruff rules E,F,I,UP,B, line-length 100.
- Bare unit-test mode: `OrchestratorService()` has `session_factory=None`;
  every DB-backed feature MUST degrade to today's behavior in that mode (this
  is how all existing S4 tests keep passing untouched).
- State machine facts you need: `sm.execution_failed(reason)` transitions
  EXECUTING→BLOCKED (guarded no-op otherwise); `resume_agent()` re-spawns the
  engine with the SAME session_id (checkpoint continuation) and requires an
  approved plan.

## What to build

### 1. Migration `section1/migrations/0006_run_claims.sql`

```sql
-- Phase 1: durable run queue (crash-resumable execution, per-team caps).
-- Timestamps are UTC ISO-8601 TEXT (same discipline as 0005).
-- Deliberately NO FK to agent_runs: a claim may exist before the first
-- persisted event creates the agent_runs row (bus.ensure_run is lazy).
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,          -- 'queued' | 'running' | 'orphaned'
    claimed_at TEXT NOT NULL,            -- last (re)claim time
    heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL
);
CREATE INDEX idx_run_claims_state ON run_claims (state);
CREATE INDEX idx_run_claims_heartbeat ON run_claims (heartbeat_at);
```

### 2. Store module `section4/src/harness_orchestrator/run_queue.py`

Async functions, all taking `factory` (async_sessionmaker) first:

- `enqueue_run(factory, run_id, project_id) -> bool` — INSERT with
  `ON CONFLICT (run_id) DO NOTHING`; True iff this call queued it. If an
  existing row is in state `'orphaned'`, flip it back to `'queued'`
  (fresh timestamps) and return True. A `'running'` or already-`'queued'`
  row returns False.
- `claim_next_run(factory, worker_id, max_per_team) -> dict | None` — ONE
  statement (portable): select the oldest `'queued'` claim whose team's
  currently-`'running'` count (join `projects` on project_id for team_id) is
  below `max_per_team`, flip it to `'running'` with fresh
  claimed_at/heartbeat_at/worker_id, `RETURNING run_id, project_id`.
  Return `None` when nothing claimable. Correlated-subquery + `LIMIT 1`
  inside the UPDATE works on PG and SQLite.
- `heartbeat_run(factory, run_id, worker_id) -> bool` — update heartbeat_at
  only if state='running' AND worker_id matches; True iff matched (a worker
  that lost its claim learns it here).
- `finish_run(factory, run_id)` — DELETE the row (terminal: done/blocked/
  cancelled/paused all leave no claim behind).
- `reclaim_orphans(factory, stale_seconds=90) -> list[str]` — flip
  `'running'` rows with heartbeat older than cutoff to `'orphaned'`;
  return their run_ids. This is the crash detector: a live worker
  heartbeats every ~10s, so 90s of silence means the process died.
- `get_queue_snapshot(factory) -> list[dict]` — rows ordered by claimed_at
  (for tests + future ops endpoint).

### 3. Service integration (`section4/src/harness_orchestrator/service.py`)

- New optional ctor args: `run_queue_factory=None` (defaults to
  `session_factory` when None — one knob less), `max_per_team=4`,
  `worker_id=None` (auto: `f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"`),
  `stale_seconds=90`.
- `start_execution()` and `resume_agent()` change ONLY in deployed mode
  (factory present): instead of `self._spawn_execution(...)` they call
  `enqueue_run(...)` (project_id from `run_data["projectId"]`). The run stays
  EXECUTING in the SM exactly as today; the WORKER performs the actual spawn
  moments later. If `enqueue_run` returns False because a live claim exists,
  log and return (idempotent double-start guard — bonus fix).
- The engine task wrapper `_execute` gains queue lifecycle when factory
  present: heartbeat task (every 10s while the engine thread runs;
  `asyncio.create_task` + finally-cancel), and `finish_run` in the same
  places the run settles (completed, failed, cancelled-with-stop,
  paused/human-edit — pause KEEPS the run resumable, so finish there too;
  the human Resume re-enqueues).
- New method `poll_once()` on the service (also usable standalone): claim one
  run (`claim_next_run`), and if claimed, load its plan from
  `agent_plans`/planner state (reuse `_ensure_run(run_id)` rehydration +
  `self.planner.get_plan(plan_id)` — the claim gives you project_id; put it
  into run_data before rehydration if absent) then `_spawn_execution`. Return
  the run_id or None. Guard: only spawn if SM state is EXECUTING after
  rehydration AND no live task exists (crashed-process restart case: rehydrated
  EXECUTING with dead task is exactly what we're resurrecting; but a PAUSED
  rehydrated run whose claim somehow survived must NOT be executed — finish
  its claim instead).
- New method `recover_orphans()` calling `reclaim_orphans` — invoked once at
  worker start.

### 4. Worker loop + deploy wiring

- Small `RunQueueWorker` class (same module or `worker.py`): constructor takes
  the service + poll_interval (default 0.5s); `run_forever()` async loop:
  recover_orphans once, then poll_once in a try/except (log + continue — the
  worker NEVER dies from one bad iteration), `await asyncio.sleep(poll_interval)`
  between polls; `stop()` cancels cleanly.
- `deploy/runners/services.py::app_orchestrator`: after building the service,
  attach a startup hook that creates ONE worker task when
  `service.session_factory is not None`, and a shutdown hook that stops it.
  Read `HARNESS_RUN_CONCURRENCY` (int, default 4) and `HARNESS_WORKER_POLL_SECONDS`
  (float, default 0.5) from env for max_per_team/poll interval. Keep the diff
  minimal — plain FastAPI event handlers, no new deps.

### 5. Tests (`section4/tests/unit/test_run_queue.py`, sqlite via aiosqlite)

Follow the fixture style of `test_authz.py` (create tables with raw DDL in a
tmp_path sqlite file). Cover at minimum:
- enqueue → claim roundtrip returns correct ids; second enqueue while queued
  returns False.
- FIFO: two queued runs claim oldest-first.
- Team cap: seed `projects` rows for teams A/B like test_authz does; with
  max_per_team=2 and two A-runs already 'running', A's third stays put while
  B's queued run claims fine.
- heartbeat updates only matching worker; wrong worker_id → False.
- finish_run deletes the row.
- reclaim_orphans: running+stale → orphaned and returned; running+fresh →
  untouched; queued → untouched.
- Crash-resume story end-to-end at store level: enqueue → claim → simulate
  death (no finish) → advance clock (insert stale heartbeat manually) →
  reclaim_orphans → enqueue again returns True (orphan requeue) → claim
  succeeds again.
- Service-level: with a sqlite factory, `start_execution` enqueues (row
  visible, no immediate task spawned), `poll_once()` spawns it (status
  executing), completion drains the claim (snapshot empty). Use the fake
  engine pattern from existing S4 tests (check test_engine.py for how they
  stub DeepSeekHarnessWrapper).
- Bare mode regression: `OrchestratorService()` with no factory still spawns
  directly (existing conformance/stress suites must pass untouched — run them).

### 6. Also run before hand-off

- Full section4 suite + ruff.
- `python3 contracts/ts/generate.py --check` from repo root (must be untouched).

## Constraints

- NO new EventTypes, NO contract changes, NO HTTP route changes (queue is
  internal; ops endpoints come later).
- NO Redis/Celery/background daemons beyond the one in-process worker task.
- Do not refactor unrelated service code; do not reorder imports elsewhere.
- Honest-limitation note for your report (include verbatim understanding):
  this makes the queue durable + runs crash-resumable + caps per-team
  concurrency; execution itself remains in-process per orchestrator replica,
  which is safe because claims are exclusive across replicas.

## Deliverable

No commit. Report: files changed, full pytest/ruff output summaries for
section4, the exact SQL of your claim statement, and anything intentionally
not done.
