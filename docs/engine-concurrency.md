# Engine Concurrency & the Hung-Provider Circuit Breaker (M17-B5)

> Fixes PBC1 F2 (M17 Phase A findings): engine concurrency used to be bounded
> invisibly by CPython's default thread pool (~16–36 slots depending on CPU
> count) while the run queue advertised per-team caps that could oversubscribe
> it ~2.5×, and a single hung provider call occupied a slot FOREVER — N hangs
> froze all engine runs platform-wide.

## The numbers

| Knob | Default | Where it lives |
|---|---|---|
| Per-team running cap (`HARNESS_RUN_CONCURRENCY`) | 4 | `deploy/runners/services.py` → `OrchestratorService(max_per_team=…)`; enforced by the claim SQL in `run_queue.py::_CLAIM_SQL` |
| **Engine executor bound** (`ENGINE_MAX_CONCURRENCY`) | **8** | `section4/src/harness_orchestrator/engine_runtime.py::EngineRuntime` |
| Per-run deadline (`ENGINE_RUN_TIMEOUT_SECONDS`) | 600 s | same module, applied via `asyncio.wait_for` around the SDK call |
| Breaker open threshold (`ENGINE_BREAKER_THRESHOLD`) | 3 consecutive timeouts per `provider\|model` | same module, `ProviderCircuitBreaker` |
| Breaker cool-down (`ENGINE_BREAKER_COOLDOWN_SECONDS`) | 300 s | same module |

**The equation this page pins:**

```
max concurrent engine runs (real)
    = ENGINE_MAX_CONCURRENCY                      # dedicated executor, 8 shipped
    ≥ max claims the queue can hold 'running'     # HARNESS_RUN_CONCURRENCY × teams
```

The default 8 is chosen from the queue math, not CPU count: with the shipped
per-team cap of 4 and the two seeded demo teams, at most **8 claims can be
`running` simultaneously**, so the executor is never the hidden bottleneck
below what admission already allowed. If you raise the per-team cap or add
teams, raise `ENGINE_MAX_CONCURRENCY` to at least `cap × team_count` — or
leave it lower DELIBERATELY as an admission bound, but then say so here.
What is NOT acceptable is the old behavior: an inherited, undocumented
CPU-derived bound silently overriding the advertised policy.

Runs beyond the executor bound do not vanish: their queue claims wait
(`run_claims.state='queued'`, heartbeated by nothing until claimed), so the
zombie reaper cannot misread them and users see QUEUED, not a fake EXECUTING.

## Run lifecycle under the runtime

1. Worker claims a queued run → `_execute` calls `EngineRuntime.run(...)`.
2. The provider's circuit is checked FIRST: open ⇒ the run fails fast with
   `engine_failed {reason: "provider circuit open (…)"}` and releases its
   claim — no slot is consumed behind a dead provider.
3. Otherwise the synchronous SDK call executes on the dedicated executor
   (threads named `engine-run_N`) under `wait_for(deadline)`.
4. Completed inside the deadline ⇒ normal completion path; breaker streak
   for that provider clears.
5. Deadline expires ⇒ `engine_failed {reason: "timeout: …"}` (state machine
   BLOCKED for human re-drive), the timeout counts toward the breaker, and
   `finish_run` releases the claim in the normal `finally`.

**Honest limit:** the wall-clock deadline stops the RUN, not the OS thread —
Python threads cannot be killed mid-call. A timed-out call keeps its thread
until the underlying socket/SDK gives up. That is exactly why the circuit
breaker exists: after `ENGINE_BREAKER_THRESHOLD` consecutive timeouts on one
`provider|model`, new runs targeting it are refused at step 2 instead of
piling more threads onto a provider that already stopped answering. Keys
isolate providers/models — one hung endpoint never blocks another.

The breaker is in-process state by design: the orchestrator is currently a
single process (PBC1 F1). If F1's multi-replica fix lands, breaker state must
move to shared storage in the same change.

## Operator overrides

All five knobs are env-tunable (see `.env.example`). Defaults are resolved at
service construction; a restart re-reads them. Non-numeric / below-minimum
values are ignored with a warning (same law as `HEARTBEAT_TIMEOUT_SECONDS`,
M13-H1b).

## Admission cadence (M20-T1 — claim batching)

The BASELINE ruler (`docs/benchmarks/BASELINE.md`) measured the platform wall:
the queue worker admitted at most ONE run per poll cycle (0.5 s shipped), so
throughput was bounded near 1/poll_interval ≈ 2 journeys/s regardless of the
executor width. M20-T1 removes that bound:

| Knob | Default | Meaning |
|---|---|---|
| Claim batch (`WORKER_CLAIM_BATCH`) | **`ENGINE_MAX_CONCURRENCY`** (8 shipped) | Runs claimable per poll cycle (`RunQueueWorker`); each claimed run still dispatches individually through every pre-spawn gate |
| Poll floor (`WORKER_POLL_MIN_SECONDS`) | 0.01 | Cadence while saturated (previous cycle claimed a full batch) |
| Backoff factor (`WORKER_POLL_BACKOFF_FACTOR`) | 2.0 | Multiplier applied to the inter-cycle sleep per drained cycle |

Adaptive rule (one place: `RunQueueWorker._next_interval`, unit-pinned): full
batch → floor; partial batch → hold; drained → multiply toward — but never
past — the configured idle cadence `HARNESS_WORKER_POLL_SECONDS`, which stays
the backoff ceiling. Zombie-reaper and orphan sweeps still run EVERY cycle.
The M19-N2 slim bus may substitute for polling as a pure timing hint:
`RunQueueWorker.wake()` ends the current sleep early, so a bus listener can
signal arrivals (a missed signal is harmless — the next cycle polls anyway).

## Related

- Finding + evidence: `docs/briefs/m17-findings-pbc1.md` §F2
- Connection pool math (DB side): `docs/pool-math.md`
- Zombie reaper / heartbeat law: `.env.example` (`HEARTBEAT_TIMEOUT_SECONDS`)
