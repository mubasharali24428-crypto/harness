# M17-A — PBC1 findings: scale/10x lens

> Seat: PBC1 (Elite cross-discipline, programming+systems; scale specialist).
> Repo: `/Users/user/untitled folder 2/harness` @ HEAD `3c794b2`. Read-only audit;
> no code changed, no git writes. Every finding cites file:line evidence and a
> quantified breaking point ("breaks at ~X because Y") + fix-complexity tag.
> Numbers marked [M] are measured from the code/config as shipped; numbers
> marked [E] are engineering estimates derived from those measurements.

---

## Executive summary

The architecture is event-sourced around one Postgres table (`agent_events`)
with per-row LISTEN/NOTIFY fan-out, a **single in-process orchestrator** whose
entire run state lives in a Python dict, and a WS layer that fans out to
per-connection asyncio queues with no connection cap. Each of these is fine at
demo scale and becomes the binding wall at different points between ~50 and
~1,000 concurrent users. The single hardest wall is not throughput of the DB —
Postgres can absorb 1M inserts/day easily — but **(a) the orchestrator being a
stateful singleton**, **(b) NOTIFY payload fan-out that scales O(instances ×
events) regardless of who subscribed to what**, and **(c) an unbounded
`asyncio.to_thread` engine pool that lets concurrent runs silently cap at
~16–40 on default Python, far below what the queue's `max_per_team=4`
advertising implies**. Cost model: metering exists but budget checks do full
ledger SUMs per gate, which converts cost accounting itself into a scaling
cost. Multi-tenant fairness has caps but they are per-team only; there is no
platform-wide admission control or noisy-neighbor isolation on the event bus.

---

## Findings

### F1 [CRITICAL] Orchestrator is a stateful in-process singleton — hard ceiling at ONE instance, all runs' live state in RAM

Evidence:
- `section4/src/harness_orchestrator/service.py:186-190` — `self.runs: dict[str,
  dict[str, Any]] = {}` holds every run's state machine, plan id, goal,
  engine result, and live asyncio task, keyed by run_id, forever (no eviction
  anywhere: `grep "self.runs.pop|del self.runs"` → zero hits).
- `service.py:267-273` — `PlanService.plans: dict[str, AgentPlan]`, also never
  evicted; approval lives only here (`is_approved` reads this dict).
- `deploy/runners/services.py:191-207` — exactly one `OrchestratorService` +
  one `RunQueueWorker` per process; entrypoint runs a single uvicorn process
  (`deploy/entrypoint.sh:81`: plain `exec uvicorn …`, no `--workers`).
- `run_queue.py:170-202` — `claim_next_run` DOES use `FOR UPDATE SKIP LOCKED`,
  so *claims* are multi-worker safe; but `poll_once` (service.py:765-794)
  immediately requires the run in `self.runs` / rehydrates locally and spawns
  the engine task **inside the claiming process**. A second orchestrator
  replica would double-execute anything it could see and split-brain approvals
  (plan approved on replica A is invisible to replica B's `plans` dict).

Breaking point: **~0 horizontal scale.** The moment you run 2 orchestrator
replicas for HA or throughput you get duplicate execution and lost approvals.
Vertically, memory grows monotonically with total historical runs (~KB-scale
per run dict + SM ⇒ ~GBs after ~10⁵–10⁶ runs [E]), so a long-lived instance
also degrades even at modest concurrency.

Fix tag: **[LARGE]** — extract run lifecycle state to durable storage (the
event log already carries it; rehydration exists via `_REHYDRATE_SQL`,
service.py:88-92), make plan approval a DB CAS (the version column from
migration 0013 already exists), keep workers stateless claimers. This is an
architecture change, not a patch — Phase B/C material.

---

### F2 [CRITICAL] Engine concurrency is bounded by the default thread-pool size, invisibly — runs queue behind ~16–40 slots, not the configured cap

Evidence:
- `section4/src/harness_orchestrator/service.py:545-547` — every claimed run
  executes via `await asyncio.to_thread(self.engine.execute_goal, …)`; dsh SDK
  calls are synchronous and blocking (engine.py:225-226 `harness.run(...)`).
- `asyncio.to_thread` uses the stdlib `ThreadPoolExecutor` default
  `max_workers = min(32, cpu_count+4)` — measured 16 on the reference dev box
  [M]; container CPUs typically give 8–32 ⇒ **16–36 concurrent engine runs
  max, process-wide** [E].
- Meanwhile the *advertised* concurrency policy is `max_per_team=4`
  (`services.py:196`, env `HARNESS_RUN_CONCURRENCY`) — i.e. 10 teams × 4 = 40
  advertised slots vs ~16 actual threads: over-subscription by ~2.5× before
  counting team count growth. Queued-but-thread-starved runs still hold a
  running `run_claims` row and emit heartbeats, so the zombie reaper will NOT
  reclaim them — they just sit in EXECUTING with zero progress.
- No timeout wraps `harness.run` (engine.py:192-247): one hung provider call
  occupies a pool slot indefinitely; N hung calls = platform-wide engine
  freeze while everything else looks healthy.

Breaking point: **~16–36 simultaneous live runs** (not users — runs). At
1000 users × even 5% run-start rate that's breached within minutes of a
morning ramp [E].

Fix tag: **[SMALL/MEDIUM]** — dedicated `ThreadPoolExecutor(max_workers=N)`
with explicit sizing + per-call `asyncio.wait_for` timeouts + queue-depth
metric. The LARGE version (process-per-run sandboxing, F6r-style confinement)
belongs to the security lane.

---

### F3 [HIGH] Per-row pg_notify fan-out: NOTIFY volume and listener CPU scale O(events × instances); payloads up to ~50KB ride every notification

Evidence:
- `section1/migrations/0003_pg_notify.sql` — AFTER INSERT trigger fires
  `pg_notify('agent_events', <full JSON envelope incl. payload>)` FOR EACH ROW.
- Postgres queues one notification copy PER listening session
  (`asyncpg.connect` in every EventBus instance, eventbus.py:129-156). With S
  service processes each holding a listener (S1+S4+S5+S6+S8 ≈ 5 listeners
  today; each additional replica adds another), every one of the day's events
  is delivered to every process even though most consume nothing.
- Payload rides in the channel: `payload JSONB` is embedded in the notify text.
  Postgres NOTIFY payload limit is 8000 bytes — larger envelopes fail the
  insert outright today [M, from the trigger shape]; at 2000-byte average
  payloads, 1M events/day = ~2GB/day pushed through the notify queue across
  listeners, parsed by every process's `_on_notify` (json.loads per event per
  instance, eventbus.py:136-149).

Breaking point: **~1M events/day × ~5–10 listener processes ≈ 5–10M json.loads
+ queue copies/day** — measurable but survivable CPU-wise [E]; the harder
walls are (a) any payload >8KB breaking inserts *today*, and (b) notify
queue buildup when any listener stalls (pg_notify has no backpressure; a slow
listener's queue grows until the session is dropped, then reconnect-loops).
At 10M+ events/day this becomes the dominant DB-side overhead.

Fix tag: **[MEDIUM]** — notify a minimal signal (seq, project_id, type) and
have listeners fetch/fan-out from the table (or move cross-instance delivery
to Redis Streams/a broker). Keep the direct-publish path; the seam already
exists (duplicates dedup'd client-side by seq).

---

### F4 [HIGH] No partitioning on agent_events; retention DELETE churn fights replay indexes; seq is a global BIGSERIAL

Evidence:
- Frozen schema `0001_initial.sql:158-167`: `agent_events(seq BIGSERIAL PK,
  ts TIMESTAMPTZ, payload JSONB)` — no PARTITION BY; contracts freeze makes
  adding one later a migration-only affair (possible: new numbered migration,
  but moving a frozen table to declarative partitioning is a table rebuild).
- Retention sweep `deploy/runners/events_retention.py:99-114`: batched
  `DELETE WHERE seq IN (SELECT … WHERE ts < cutoff ORDER BY seq LIMIT 5000)`.
  The probe IS indexed (`idx_agent_events_ts`, migration 0018) so deletion
  finds rows efficiently — but each batch leaves dead tuples scattered across
  the heap/indexes; at steady state (30-day horizon, 1M ev/day ⇒ ~30M live
  rows + ~30M deletions/day-equivalent churn [E]) autovacuum must continuously
  recycle, and index bloat on `(project_id, seq)` (0002) and the covering
  `(run_id, type, ts)` (0026) degrades exactly the two hot paths retention
  protects (replay + runs-index markers).
- Replay honesty contract already tolerates gaps (retention docstring lines
  18-23; app closes 1013 on overflow), so time-partition DROP would be
  semantics-preserving and free: `DROP TABLE agent_events_2026_07` replaces
  30M row-by-row deletes with one metadata operation.

Breaking point: **~10–30M live rows** the vacuum/debt curve starts winning
[E]: p99 replay latency climbs first (index bloat), then autovacuum falls
behind under continuous delete pressure, then backup/restore windows balloon.
That's ~10–30 days at the mission's own 1M/day target.

Fix tag: **[MEDIUM/LARGE]** — monthly range partitions on `ts` (or seq ranges)
+ attach/detach retention. Needs the fresh-install migrate() probe (see
harness-monorepo pitfalls: pytest suites don't exercise migrate()).

---

### F5 [HIGH] WS layer: unbounded connections per process, O(subscribers) fan-out per event, replay burst amplification on reconnect storms

Evidence:
- `section1/src/harness_identity/app.py:621-673` — each WS connection owns one
  `asyncio.Queue(maxsize=1000)` subscription + a watcher task; no per-user,
  per-project, or per-process connection limit exists (`grep MAX_WS|connection
  limit` → none). One browser tab = 1 socket; 1000 users × several tabs +
  refreshes ⇒ thousands of live sockets in ONE uvicorn process (single worker
  by design, entrypoint.sh:81).
- Fan-out cost: `EventBus.publish` iterates ALL subscriber queues for the
  project (eventbus.py:238-247) AND `_fanout_local` does it again from the
  NOTIFY path (eventbus.py:170-186) — duplicates are deliberate (dedup by
  seq) but mean **2× queue puts per subscriber per event** plus a json.loads
  on the notify side. With E events/s on a busy project and K subscribers,
  work is O(2·E·K) puts/s in the identity process, sharing its loop with all
  HTTP auth traffic.
- Reconnect storm: clients back off with jitter (useEventStream.ts/backoff.ts,
  1s→30s cap — good), BUT every reconnect replays up to
  `REPLAY_MAX_EVENTS=5000` envelopes synchronously on the accept loop path
  (app.py:637-651) before the first live frame. After a shared outage, N
  reconnecting sockets × 5000-frame replays serialize inside the same
  process; at ~1ms/frame serialization cost that's 5s of pure replay work per
  socket [E] — 200 simultaneous reconnects ≈ 15+ minutes of saturated loop
  before steady state, during which live events queue toward overflow
  (1013 close ⇒ more reconnects).
- Sticky sessions: none required today because there is exactly one process
  per role; the moment identity scales out, WS + ticket redemption
  (`ws_tickets` table, migration 0005) works cross-instance, but the
  in-memory `_subscribers` dict means a client must land on the instance that
  serves its project's publishes — i.e. load balancers need least-connections
  + the NOTIFY bridge (F3) to deliver everywhere, multiplying fan-out cost
  again.

Breaking point: **~2–5K concurrent sockets/process** (uvicorn/websockets
practical FD+memory envelope with 2 tasks+1 queue each) [E], reached well
before 1000 users if tabs multiply; reconnect storms break it at ~200
simultaneous.

Fix tag: **[MEDIUM]** — connection caps + per-project subscriber limits
(SMALL), replay pagination during handshake (SMALL), dedicated WS gateway
process or pub/sub backbone (LARGE if truly needed).

---

### F6 [HIGH] Budget/cost checks do unindexed full-ledger aggregations on hot paths

Evidence:
- `section4/src/harness_orchestrator/budget.py:28-35` — `_SPEND_SQL` =
  `SUM(cost_ledger.cost_usd)` joined through agent_runs→projects→teams,
  **no date bound, no index on any cost_ledger column except PK seq**
  (grep migrations for cost_ledger indexes → zero). Executed on EVERY run
  start (`_budget_gate`, service.py:245-273) and every reviewer call
  (signal_done, service.py:845-857).
- `section3/src/harness_router/budget_guard.py:57-76` — RESERVE_SQL does the
  same month-bounded SUM per model call; better bounded, still a scan of the
  month's rows joined 3 deep.
- Spend API aggregates GROUP BY model,day over the whole ledger
  (ledger.py SPEND_SQL) — dashboard-shaped queries on the same bare table.
- Ledger grows per model call: a live run emits many calls; 1000 runs/day ×
  ~20–100 calls/run [E] ⇒ 20K–100K rows/day ⇒ ~10–30M rows/year. Every budget
  gate linearly scans a growing prefix of that (PG may use the FK-less join
  order poorly since only seq PK exists).

Breaking point: **~1–5M ledger rows**, SUM latency crosses tens of ms and
starts stacking into run-start latency; at ~10M rows every run-start pays a
multi-hundred-ms aggregate tax [E] — and the gates sit on the synchronous
API path.

Fix tag: **[SMALL]** — `CREATE INDEX ON cost_ledger (created_at)` + team_id
denormalized (or a spend rollups table maintained by the existing outbox/
sidecar pattern: daily per-team totals, gate reads one row). Rollup version
is MEDIUM.

---

### F7 [HIGH] Run-start rate limiting keys per-IP only — no per-team quota on the expensive paths that actually cost money

Evidence:
- `section1/src/harness_identity/ratelimit.py:108-131` — RUN_START_LIMIT=30/min
  and MODEL_CALL_LIMIT=60/min are keyed `client_ip|route_template`. Legit
  teams sit behind NAT/shared egress: one office IP exhausts 30 run-starts/min
  across ALL its users (false positive at ~30-person orgs), while a malicious
  tenant rotates IPs (cloud egress) to get unlimited budgets. There is no
  per-team counter on run-start/model-call anywhere (budget caps exist but
  only as monthly $ ceilings, not burst fairness).
- Budget enforcement degrades OPEN twice: ratelimit fails open on store error
  (ratelimit.py:306-333, documented choice) AND budget check degrades to
  allowed on DB failure (budget.py:144-145) — a PG brownout disables ALL
  spend protection simultaneously [M].
- Queue admission is FIFO by `claimed_at` (run_queue.py:_CLAIM_SQL ORDER BY
  claimed_at ASC LIMIT 1): one team enqueueing 400 runs legitimately delays
  every other team's first slot only up to `max_per_team` running per team…
  but the *queued* backlog is unbounded and unpoliced per team, so head-of-
  line position is bought by submission rate.

Breaking point: **multi-tenant day one.** With ≤1 team/IP (current reality)
it's invisible; the first 2 customers behind the same corporate NAT hit it,
and the first abusive tenant finds burst-unlimited expensive ops.

Fix tag: **[MEDIUM]** — add team-scoped buckets alongside IP (bucket key gets
team_id dimension; auth principal is already resolved post-middleware, so
needs a small reorder), per-team queued-depth cap at enqueue_run, and a
fail-closed toggle for the budget gate specifically (keep auth edges
fail-open).

---

### F8 [MEDIUM] Notification outbox drain scans unbounded 'sent' history; no retention for outbox/audit_log/ws_tickets

Evidence:
- `notification_outbox` (0009) has status index (good for drain) but NO
  retention job deletes sent/failed rows — the notifier worker polls
  `WHERE status='queued' ORDER BY created_at` (notifier.py:397-406); the
  index keeps that fast, but the table grows forever, bloating backups
  (same argument GAP raw-05 made for agent_events).
- `audit_log` (0001:89-100) has only `idx_audit_log_ts` (0002) for reads; no
  retention runner exists for it at all (`grep -l audit.*retention deploy/` →
  none). Compliance-grade append-only is fine, but at 1000 users every auth
  decision, invite, and admin action appends; ~50K–500K rows/day [E] with zero
  pruning path.
- `ws_tickets`/`oauth_states` (0005) expire opportunistically on insert — OK.

Breaking point: **months, not days** — outbox/audit reach backup-window pain
around 10⁷–10⁸ rows [E]. Not urgent alone; bundle with F4's partitioning
work.

Fix tag: **[SMALL]** — extend events_retention.py pattern (it's explicitly
the template) with per-table sweeps + ts indexes where missing.

---

### F9 [MEDIUM] Event payload duplication: full payloads stored once but shipped ≥2× per subscriber; done-signal diffs ride the bus

Evidence:
- DONE_SIGNAL/review flows carry diff text up to `DONE_DIFF_TEXT_MAX =
  200_000` chars (_limits.py) through `signal_done` → REVIEW_PASSED/FAILED
  events whose `findings` dict goes into agent_events.payload AND every notify
  copy AND every subscriber queue (app.py:646 send_json of full envelopes).
  200KB payloads exceed the 8KB notify limit (insert-time failure risk) and,
  when they fit, multiply WS egress by subscribers.
- Client coalescing exists (backoff.ts refetch debounce 2s — good), but the
  wire cost remains per-event.

Breaking point: **single large-diff run against a project with many open
viewers** — a 150KB finding payload × 50 viewers × 2 (dup fan-out) ≈ 15MB
egress burst per review event [E]. Rare today, structural at scale.

Fix tag: **[SMALL]** — cap bus payloads (ship references/URLs, fetch bodies
via REST), enforce payload size < 8KB at publish (aligns with F3's minimal-
notify fix).

---

### F10 [COST MODEL] Per-run infra economics at 1000 concurrent users

Measured inputs [M]:
- Metering exists end-to-end: real token usage folded from dsh usage chunks
  (engine.py:96-126), priced via S3 PricingEngine NUMERIC(12,6)
  (run_metering.py:87-148), attributed per team (cost_ledger.py). Honest
  estimates when usage absent (~4 chars/token).
- Engine: `max_tokens=4096` per dsh attempt (engine.py:209); typical coding
  run = multi-step tool loops, so realistic spend per completed run is
  ~50K–300K input + 10K–80K output tokens [E]. At DeepSeek-class pricing
  (~$0.27/$1.10 per Mtok) that's **$0.03–$0.25 per run** [E]; frontier-model
  catalogs price the same run $0.5–$5 [E].
- Compute: 1 run ≈ 1 thread-slot occupied for minutes-to-hours (F2); 1000
  active runs need either ~64 vCPU of orchestration hosts (thread-bound) or
  process-per-run isolation [E].

Infra cost per 1000 runs [E]: model spend dominates by 10–100× ($30–$250
DeepSeek-class vs ~$1–5 compute share). Biggest cost MULTIPLIERS, ranked:
1. **Retry/resume behavior**: failed runs re-drive from human gate (BLOCKED),
   each resume re-spawns the engine with session continuity — good — but
   zombie-reaped runs lose mid-flight work entirely and re-spend from scratch
   (reaper marks ENGINE_FAILED, service.py:713-751).
2. **Reviewer model call per done-signal** (service.py:845+) — unbounded
   number of reviews per run via REVIEW_PASSED→EXECUTING loop (state map
   service.py:95-109 allows repeated cycles).
3. **No per-model price ceilings / catalog gating on expensive providers**
   beyond monthly budget; a single team can burn their whole month in minutes
   (by design), and platform absorbs provider-side variance.
4. Ledger/budget aggregation cost itself (F6) — small $ but real latency.

Missing for commercial operation: no chargeback/showback UI beyond GET
/spend aggregates; no alerting on burn-rate anomalies (only hard monthly
caps); no per-run cost preview BEFORE approval (plan.estimatedCostUsd exists
and feeds the gate, but the approver sees no projected-vs-actual comparison).

Fix tag: **[MEDIUM]** — mostly product surface + rollups (pairs with F6).

---

### F11 [MEDIUM] Single Postgres instance is every subsystem's database — blast radius and vertical ceiling

Evidence:
- compose: one `db` service, `max_connections=120` (docker-compose.yml:86);
  pool math comment: 7 services × (8+4) = 84 conns worst case, 36 headroom
  [M]. Adding replicas of ANY service eats headroom linearly; PgBouncer is
  absent.
- The SAME instance serves: auth/session reads (every request), agent_events
  inserts+NOTIFY (every event), run_claims UPDATE…RETURNING contention
  (every poll cycle at 0.5s interval per worker — cheap but locky under SKIP
  LOCKED churn), cost_ledger SUMs (F6), audit_log, outbox.
- No read replica story, no per-service DB, no table-level resource
  isolation: a heavy replay stream (yield_per cursor, eventbus.py:316-342)
  shares I/O and buffers with login OTP writes.

Breaking point: **~120 connections** is the first literal wall (≈9–10
service replicas or one leaky deploy) [M]; CPU/IO walls arrive earlier under
mixed replay+write load [E]. Vertical scaling buys ~2–4× before NVMe/NUMERA
limits; beyond that needs the partitioning (F4) + pooling + read-replica
work.

Fix tag: **[MEDIUM]** — PgBouncer transaction pooling + connection-budget
docs now; read replicas for replay/spend reads later; per-service DBs last.

---

### F12 [LOW→HIGH trajectory] Zombie reaper vs long-running legitimate engines: 90s heartbeat threshold assumes sub-90s progress signals

Evidence:
- Heartbeat interval 10s, timeout 90s (run_queue.py:43, _HEARTBEAT_INTERVAL_
  SECONDS=10 in service.py:87). Heartbeats tick independently of engine
  activity (task-level, service.py:533-553), so this is fine UNLESS the event
  loop is saturated (GIL/thread starvation under F2's pool exhaustion) —
  late heartbeats ⇒ live runs reaped as zombies ⇒ ENGINE_FAILED emitted ⇒
  user-visible false failures + re-execution cost (F10 multiplier #1).
- Under thread-pool saturation, heartbeat coroutines still schedule (they're
  async DB updates) but compete with thousands of ready callbacks; at >90s
  loop lag the reaper starts killing healthy runs [E].

Breaking point: **loop lag >90s** — plausible exactly at F2's breaking point,
making F2 failures look like reliability incidents rather than capacity ones.

Fix tag: **[SMALL]** — decouple heartbeat scheduling priority (own executor),
lag metric, and reap threshold scaled by measured loop health.

---

## What already holds up (credit where due)

These survive the 10x lens without fixes:
- Replay is capped/streamed (REPLAY_MAX_EVENTS=5000, yield_per chunks) —
  memory-safe by construction (eventbus.py:280-352).
- Claim atomicity via `FOR UPDATE SKIP LOCKED` inside the claim subquery
  (run_queue.py:153-202) — correct multi-worker primitive already shipped.
- Retention probe indexed (0018) and batched in short transactions — right
  shape; only partitioning supersedes it (F4).
- Client backoff with full jitter + refetch coalescing (backoff.ts) —
  reconnect-storm damping exists client-side.
- Money math exact (NUMERIC(12,6), text binds, H-023) — no float drift at
  any scale.
- Cross-process ephemeral state (ws_tickets/oauth_states, 0005) — scale-out
  blocker already cleared for the auth edge.

## Confidence ledger

Verified [M]: file/line evidence above; pool sizes, limits, thresholds,
indexes, absence of eviction/partitioning/team-scoped limits, uvicorn
single-process exec. Estimated [E]: latency/row-count projections, thread-pool
behavior under container CPU counts, per-run token distributions, reconnect
arithmetic, cost bands. Not probed: no live load test was run against any
system (audit charter: read-only); PG-version-specific NOTIFY internals taken
from documented behavior, not benchmarked here.

## Suggested Phase-B ordering (input to ATLAS gate, not a fix plan)

1. F2 thread-pool + timeouts [S/M] — smallest diff, removes the earliest wall.
2. F6 ledger index/rollups [S] — removes hot-path tax before tenants grow.
3. F7 team-scoped limits [M] — required before second customer.
4. F3+F9 slim notify/payloads [M] — pre-req for any identity scale-out.
5. F4 partitioned retention [M/L] — schedule before the 10–30M row window.
6. F1 stateless orchestrator [L] — the Phase-C architecture program.
