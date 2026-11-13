# Decision: scale-out-ready by design, proven on demand; tier A (5–20 per team) first

**Date:** 2026-11-06
**Status:** Direction ACCEPTED by the decision owner (Mubashar Ali, in session). The tier boundaries and
time estimates below are the reviewer's estimates, not measurements; the tier A test is not yet run.
**Related:** [`engine-concurrency.md`](../engine-concurrency.md), [`pool-math.md`](../pool-math.md),
[`roadmap-3.0.md`](../roadmap-3.0.md), [`2026-11-05-w28-child-run-approval.md`](2026-11-05-w28-child-run-approval.md)

## Question
How many simultaneous runs per team must Harness support, and how much engineering do we spend on it now?

## Decision
1. **Typical customer: 5–20 concurrent runs per team.** This is the case we make solid first.
2. **A rare customer may want 1,000 per team**, with cost and compute not a concern for them. We do not
   build for that now. We make sure it is *reachable by adding machines*, and we prove it when a customer
   asks, on that customer's hardware.
3. **Claim rule:** we say "designed for 1,000, tested to N", where N is the largest number actually run
   and recorded. We never state a number that was not run.
4. **Design rules that apply from now on** (cheap now, expensive to retrofit):
   * no new global lock on the run-start path;
   * no assumption of one worker host;
   * shared state lives in the database, not in one process (circuit breaker included);
   * event writes are coalescable; heartbeats are consolidable;
   * approval must not hard-code one human per run (a pre-approved envelope may cover many runs; that
     is a later product decision and weakens nothing until it is taken).
5. **Cost optimisation is out of scope for the 1,000 tier.** Provider rate limits are not: that customer
   needs multiple keys or negotiated limits.

## Tiers

| Tier | Target | Work | Estimate | When |
|---|---|---|---|---|
| A | 5–20 per team | Raise defaults, a second worker host, real-model test at 20 | 1–2 weeks | Now |
| B | about 100 | Design fixes below, and the ramp test to 100 | 4–6 weeks | Before a large pilot |
| C | 1,000 | Worker fleet, soak and chaos tests, approval at scale | 4–6 more weeks | When a customer asks |

Tier B design fixes: claim path without the global advisory lock (`run_queue.py`, `pg_advisory_xact_lock(7270001)`);
a worker fleet in place of the single daemon; breaker state in shared storage; PgBouncer in front of
Postgres; coalesced events; consolidated heartbeats; the audit hash chain off the per-event hot path.

## Known ceilings today (from code and docs, not measured under load)
* Per-team running cap 4 (`HARNESS_RUN_CONCURRENCY`), engine executor 8 (`ENGINE_MAX_CONCURRENCY`),
  per-workspace cap schema 1–50 (`max_concurrent_runs`).
* One worker daemon on one Docker host; each run is a container (1 GiB, 1 CPU, 512 pids).
* Postgres `max_connections` 120, 84 budgeted across 7 services.
* One replica 17.6 journeys/s, two replicas 22.0 (dry engine): the second added about 25%, cause unknown.
* Not measured: RAM per run, container start rate, event rate, real-model concurrency above a handful.

## Tier A test plan (not run)

Goal: find the first wall between 8 and 20 concurrent real runs per team, and make 20 a supported number.

Preconditions: a scratch stack (never the live database on port 5499); an agreed provider budget before any
paid call (roadmap rule); every failure and skip retained in the evidence.

| Step | What | Pass condition |
|---|---|---|
| A1 | Dry engine, ramp 8, 12, 16, 20, 32 concurrent runs for one team | Every run reaches a terminal state; no claim stuck `queued` beyond the poll bound; p95 queue wait recorded |
| A2 | Same ramp with `HARNESS_RUN_CONCURRENCY` and `ENGINE_MAX_CONCURRENCY` raised to match | Zero connection-pool errors; DB connections peak below `max_connections`; no orphaned container |
| A3 | Real model (cheap), 20 concurrent runs, container mode | At least 95% of runs complete; ledger total matches provider counter; no 429 left unhandled |
| A4 | Add a second worker host (or second daemon) and repeat A3 | Both hosts take runs; killing one daemon mid-run does not lose a run |
| A5 | Record per run: RAM, CPU, container start time, events written, DB connections | Numbers written to `docs/briefs/evidence/scale/` with the commit and image tags |

Exit: either 20 per team passes A3 and A4 and the defaults are raised and documented, or the first wall is
named with evidence and becomes the next work item. Stop at the first red step; do not average failures away.

## Consequences
* `STATUS.md` and the capability matrix must not claim any concurrency above the largest tested N.
* Tier B is not scheduled until a pilot needs it or tier A finds a wall that requires it.
