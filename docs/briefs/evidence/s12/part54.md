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
- One identity replica for sockets in section 3. Closed 2026-10-21: two replicas, see "Sockets across two
  identity replicas" below.
- Artifact and export files were on each replica's local disk (`HARNESS_ARTIFACT_ROOT`,
  `HARNESS_EXPORT_DIR`). Closed 2026-10-21 with a shared volume (see "Shared store" below).
- Cost per active run: zero by construction (dry engine); provider-backed cost is not measured.

## Shared store across orchestrator replicas (2026-10-21)

The artifact, export and diff stores were each replica's own `/tmp`. Now every orchestrator replica in
`deploy/docker-compose.yml` mounts one named volume at `/var/lib/harness-store`: `harness_store` for
`orchestrator`, `harness_store_loadtest` for `orchestrator-loadtest`. `HARNESS_ARTIFACT_ROOT`, `HARNESS_EXPORT_DIR` and
`HARNESS_DIFFSTORE_ROOT` point inside it. `deploy/backend.Dockerfile` creates the mountpoint owned by `harness`,
because a fresh named volume takes the ownership of the image directory and the services run as uid 999. No code
changed: the stores are content-addressed (artifacts, diffs) or keyed by job id (exports), and they publish with an
atomic rename.

Check: the loadtest stack ran with `--scale orchestrator-loadtest=2` (ports 18004 and 18005). The script logged in,
made a project and a run, uploaded an artifact through replica A and downloaded it through replica B, then created
a `run_history` export on A and downloaded it on B. Then it ran again with A and B swapped.

| direction | artifact upload / download | bytes equal | export create / download | sha256 = `X-Export-Sha256` |
|---|---|---|---|---|
| 18004 to 18005 | 201 / 200 | yes | 201 / 200 (1384 B) | yes |
| 18005 to 18004 | 201 / 200 | yes | 201 / 200 (1384 B) | yes |

The live `orchestrator` was recreated on `harness_store`: healthy, `/ready` 200, mount owned by `harness`.
Section 1 deploy-config tests 115 passed, 1 skipped; section 4 `test_live_mode.py` 53 passed.

Limits: diffs share the same mount but were not round-tripped through the API. Bytes written to the old per-container
`/tmp` before the switch are not migrated; they were already lost on every container recreate. The volume is a
local Docker volume (one host); several hosts need NFS or an object store behind `artifact_store.put/get/remove`.
The volume is not in the backup scripts.

## Sockets across two identity replicas (2026-10-21)

`run_probe.sh` takes `PROBE_IDENTITY_REPLICAS=2` (`--scale identity-loadtest=2`, host ports 18001-18002; 18003 is
held by a New Relic agent on this machine). `ws_capacity.py --identity a,b` mints socket k's ticket on replica
k mod 2 and opens the socket on the other replica, so every socket redeems a ticket the other process minted, and
every `plan_proposed` envelope reaches half its sockets through PostgreSQL NOTIFY. Same stack as above:
identity 0.5 CPU / 384 MiB per replica, one orchestrator.

| Replicas | Users x sockets | Opened | Ramp | Delivered | p50 / p95 / p99 / max |
|---|---|---|---|---|---|
| 1 | 100 x 10 | 999 / 1000 | 15.2 s | 999 / 999 | 824 / 1217 / 1365 / 1365 ms |
| 2 | 100 x 10 | 1000 / 1000 | 12.8 s | 1000 / 1000 (500 + 500) | 839 / 1218 / 1306 / 1328 ms |
| 2 | 100 x 10 | 999 / 1000 | 12.7 s | 999 / 999 (499 + 500) | 816 / 1209 / 1308 / 1308 ms |
| 2 | 50 x 20 | 999 / 1000 | 11.8 s | 999 / 999 (499 + 500) | 504 / 731 / 746 / 746 ms |

Cross-replica delivery never lost an envelope: delivered equals opened on every run here and on the eight earlier
diagnostic runs (893 to 1000 sockets each). After the runs: identity 186 and 148 MiB of 384 MiB.

The missing socket in three runs is one ticket POST that failed with "Server disconnected without sending a
response" right after it was sent, with no error in the identity log: the client reused a keep-alive connection
the server was closing. httpx does not retry a POST; a browser reconnect would mint a fresh ticket.

**Probe defect found and fixed.** The first two-replica runs ramped in 37 s (one replica: 22 s) and one refused
107 sockets with HTTP 403. Server side was idle: each identity replica averaged 10 to 12% CPU against a 50% limit,
PostgreSQL 12%, and its client backends waited on the app (`ClientRead`), not on locks. The probe was the bottleneck:
33 s of client CPU in 41 s, and cProfile put 109 of 116 s in httpcore's `_assign_requests_to_connections`, which
rescans its whole request queue on every connection event. With all 1000 ticket POSTs queued behind 200
connections, and a free connection usable by only one of two hosts, the scan grew with the queue. The probe now
admits at most `max_connections` ticket POSTs at once (`mint_gate`), and its CPU fell from 34 s to 8 s. The 403s
came only from runs where the client ramp passed the 30 s ticket lifetime (177 of them in the profiled run);
an expired ticket closes before accept, which the client sees as 403 (`test_ws_ticket_expired_rejected`). The
server logs no close reason, so expiry is inferred from timing, not read from the logs. Section 3's ramps were
measured with the old probe and include this client overhead.

Limits: one host and one Docker port proxy for both replicas; a single client process; latency includes 100 (or
50) simultaneous plan proposals on one orchestrator. The per-user socket cap (20) is per process, so behind a
load balancer a user can hold up to 20 sockets per replica.
