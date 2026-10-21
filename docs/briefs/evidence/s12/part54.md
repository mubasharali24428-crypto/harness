# Part 54 (session 12): fresh load runs, two replicas, WebSocket capacity

Date: 2026-10-20 (UTC 2026-10-21 03:2x to 04:0x). Tree: `master` after lane A/B merge plus this
session's fixes (`16cf2eb` for the final runs). Host: Apple Silicon MacBook Pro, Docker Desktop
29.5.3 (linux/arm64), the isolated `loadtest` compose profile (own PostgreSQL 18 volume,
orchestrator 1.0 CPU / 768 MiB per replica, identity 0.5 CPU / 384 MiB), dry echo engine
(`ENGINE_ECHO_WORK_MS=50`, no provider calls, zero spend).

Commands: `scripts/loadtest/run_probe.sh run <vus> 60 <label>` (locust closed loop: login, project,
plan, approve, start, wait for the done-gate, done-signal, wait for terminal), with
`PROBE_ORCH_REPLICAS=2` for two replicas; `scripts/loadtest/ws_capacity.py` for sockets. Raw CSVs:
`scripts/loadtest/results/s12_*` (gitignored).

## 1. Journeys

| Run | Replicas | VUs | Journeys | Failures | Journeys/s | control-plane p50 / p95 / p99 | engine wait p50 / p95 | journey p50 / p95 |
|---|---|---|---|---|---|---|---|---|
| s12_r1_8 | 1 | 8 | 723 | 0 | 12.06 | 49 / 100 / 120 ms | 560 / 620 ms | 630 / 730 ms |
| s12_r1_32 | 1 | 32 | 1052 | 0 | 17.55 | 240 / 470 / 660 ms | 1.3 / 2.0 s | 1.6 / 2.2 s |
| s12_r2_32 | 2 | 32 | 1319 | 0 | 21.96 | 380 / 560 / 650 ms | 940 ms / 1.1 s | 1.3 / 1.6 s |

Against the August baseline (`docs/benchmarks/BASELINE.md`, 1.7 to 1.9 journeys/s at 8 to 64 VUs,
one claim per worker poll) one replica now does 12 journeys/s at 8 VUs and 17.6 at 32 (the M20-T1
batch claim). A second replica on the same queue adds 25% at 32 VUs and cuts engine wait p95 from
2.0 s to 1.1 s. Control-plane latency rises with two replicas because locust sends every HTTP call
to replica 1 while both replicas' workers load the database.

Invariants, checked in the database after each two-replica run:

- both replicas claimed work (running-claim samples every 0.2 s: 254 and 313);
- no run has more than one `execution_started` or `engine_completed`;
- every run that completed its engine reached `review_passed` (1340 of 1340 in the final run).

## 2. Defects the two-replica run found (fixed, with regression tests)

1. **Stale status on the starting replica** (`2b1a257`). Replica 1 took the start, cached the run as
   EXECUTING with no local task; replica 2's worker ran it to the done-gate; replica 1 answered
   EXECUTING forever, so 32 of 32 users never sent the done-signal. `_ensure_run` now replays the log
   when this replica is not driving the run and the log moved past its copy.
   Test: `test_status_on_the_starting_replica_follows_the_replica_that_ran_it`.
2. **Claim lost in the start gap** (`16cf2eb`). The start inserts the claim (the exactly-once
   election) before EXECUTION_STARTED commits; the other replica's worker claimed in that gap,
   replayed "approved, not started" and deleted the claim: 21 of about 990 runs never ran. Such a
   claim is now requeued (approved, not started, newest event under 30 s old).
   Test: `test_a_claim_taken_before_the_start_is_durable_is_requeued_not_lost`.
3. **Ticketed sockets refused behind a trusted proxy** (`bb4f4c9`, found by the socket probe). The
   mint bound the ticket to the X-Forwarded-For client, the upgrade checked the raw peer, so every
   socket behind a proxy adding XFF (the deploy Caddyfile) got 4403. Test:
   `test_ticket_minted_and_redeemed_through_a_trusted_proxy`.

The first run also measured the Part 57 free-tier cap instead of the platform (547 of 707 journeys
refused at 20 runs per team per day); the loadtest profile now sets `HARNESS_TIER_RUNS_PER_DAY`.

## 3. WebSocket capacity (one identity replica)

Each simulated user has its own team, project and source address; every socket gets its own
single-use, fingerprint-bound ticket. With every socket open, one plan per project is proposed at
the same instant and the `plan_proposed` envelope is timed to every socket of that project.

| Users x sockets | Opened | Refused | Ramp | Delivered | p50 / p95 / p99 / max |
|---|---|---|---|---|---|
| 20 x 10 = 200 | 200 | 0 | 2.9 s | 200 / 200 | 297 / 375 / 375 / 375 ms |
| 50 x 20 = 1000 | 1000 | 0 | 18.7 s | 1000 / 1000 | 387 / 517 / 556 / 567 ms |
| 100 x 10 = 1000 | 1000 | 0 | 22.5 s | 1000 / 1000 | 802 / 1252 / 1328 / 1334 ms |

At 100 users the latency includes 100 simultaneous plan proposals on one orchestrator, so it is
an upper bound on fan-out delay, not fan-out alone. After the runs: identity 176 MiB of 384 MiB,
PostgreSQL 264 MiB. The per-user cap (20 sockets, `WS_MAX_CONNECTIONS_PER_USER`) and the ticket
mint limit (60 per minute per client address) held: the first probe, with every user behind one
address, was refused with 429 after 60 tickets, as designed.

## Limits

- One machine, dry engine, synthetic users: no provider latency, no real browsers, no network
  between services. Numbers describe this setup only and set no production user or team cap.
- One identity replica for sockets; socket fan-out across identity replicas (PG NOTIFY) was not
  load-tested here.
- Artifact and export files are on each replica's local disk (`HARNESS_ARTIFACT_ROOT`,
  `HARNESS_EXPORT_DIR`): a multi-replica deployment needs a shared volume for them.
- Cost per active run: zero by construction (dry engine); provider-backed cost is not measured.
