# M23-Z2 — BET HANDOFF: live two-orchestrator-replica proof (bet B)

> Lane Z2 (M23 finale) executed **bet A** (AST-aware .py chunking,
> section11/src/harness_context/chunking.py — see lane report). This file is
> the Next-owner brief for **bet B**, documented per the decision gate
> ("evaluate both, execute the cheaper-to-prove one").

## Decision record (why B was not executed in M23-Z2)

| | Bet A: AST chunking | Bet B: live replica proof |
|---|---|---|
| Proof surface | Pure function + existing SQLite suite | Docker compose surgery + live PG + HTTP driver |
| New infrastructure | none | second orchestrator service (currently only ONE exists per stack) |
| External deps | stdlib `ast` only | healthy compose stack, PG up, image builds |
| Failure modes | syntax fallback (covered) | port conflicts, build drift, timing flakes, hardware variance |
| Cost at execution | ~half a lane, evidence offline & reproducible | ~1 day + hardware-sensitive reruns |

Bet A was strictly cheaper to prove and is now DONE with evidence. Bet B
remains valuable: N1's two-replica guarantees are proven against **SQLite
file doubles** (`section4/tests/test_stateless_orchestrator_n1.py`), not two
real containers on real Postgres.

## Goal

Prove, against a LIVE compose stack with TWO orchestrator replicas sharing ONE
Postgres, that the propose→approve→start sequence driven through BOTH
replicas yields exactly-once durable semantics — asserted from the DATABASE,
not from any replica's memory:

1. `agent_plans.version = 1` and exactly one `plan_approved` row in
   `agent_events` even when BOTH replicas receive approve requests
   concurrently (CAS winner/loser).
2. Exactly one `execution_started` row for the run even when BOTH replicas
   receive start requests concurrently (spawner election via `enqueue_run`
   INSERT race; loser returns BEFORE emitting).
3. Exactly one `run_claims` row with one `worker_id` (worker-poll
   exactly-one-claim), engine completes once under the dry/echo backend.

## Scope & approach (reuses M18-C4 loadtest patterns)

1. **Compose**: add an `orchestrator-b` service to the `loadtest` profile in
   `deploy/docker-compose.yml` — clone of `orchestrator-loadtest` minus the
   host port (or `127.0.0.1:18014:8004` if the driver prefers HTTP for both;
   same `HARNESS_DATABASE_URL: ...@db-loadtest:5432/harness`,
   `ENGINE_LIVE_MODE: dry`, `ENGINE_BACKEND_ECHO: 1`,
   `HARNESS_REQUIRE_OWNERSHIP: "false"`). NO schema changes, NO new migration.
2. **Driver**: extend `scripts/loadtest/harness_flow.py` /
   `smoke_probe.py` patterns with an `--orchestrator-b URL` mode that drives
   the SAME journey's steps split across the two endpoints:
   propose@A → concurrent approve@A+approve@B (thread barrier) →
   concurrent start@A+start@B → wait terminal.
3. **Assertions** straight off `db-loadtest` (psql at 127.0.0.1:15499,
   creds harness/harness): count queries per the Goal above; assert
   `run_claims.worker_id IS NOT NULL` exactly once; dump raw rows into the
   report (paste RAW evidence — self-reported greens don't count).
4. Repeat the journey N≥20 times (fresh run ids) to shake out election races;
   record pass/fail counts.

## Hardware context (required by the mission graph)

Record in the report: machine model, CPU/RAM, Docker Desktop VM settings,
`docker stats` snapshots during the run, and journey wall-times. Election
races are timing-sensitive; numbers without hardware context are not
reproducible evidence.

## Pitfalls (all previously hit in this repo — read before driving)

- **Publish-chain drain**: `start_execution()` returns after the durable
  election but BEFORE its EXECUTION_STARTED publish task commits (N1's
  `_drain_publish_chains` fix). Over HTTP this becomes "assert too early" —
  poll the DB with a bounded retry instead of asserting immediately.
- **Master key gotcha**: `x-backend-env` exports `HARNESS_MASTER_KEY=""` and
  pydantic-settings then overrides services.py's dev-key fallback → give the
  isolated services `PROBE_MASTER_KEY` like `identity-loadtest` does.
- **asyncpg rejects string params for TIMESTAMPTZ**; if the driver reads
  timestamps, let asyncpg return objects (never bind strings).
- **Env leakage between suite runs**: unset `HARNESS_TEST_PG` before trusting
  red section4 runs; phantom PG-mode failures are usually a sibling's export.
- **ATLAS merge traffic may stash/pop mid-flight**; re-check
  `git status --short deploy/ scripts/loadtest/` before verifying.
- Engine must stay DRY/echo (`ENGINE_BACKEND_ECHO=1`) so runs complete
  deterministically with zero provider spend.

## Estimate

~0.5–1 day for an owner with the stack already building: ~2h compose+driver,
~2h assertion loop, remainder for flake-chasing and report writing.

## Acceptance criteria

- [ ] Two orchestrator replicas, one shared PG, live journey green ×N.
- [ ] Raw SQL outputs pasted in the lane report (not summarized).
- [ ] Concurrent double-approve AND double-start each produce exactly-once
      durable rows across ≥20 repetitions.
- [ ] Hardware context block included.
