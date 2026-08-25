# MISSION 19 — BASELINE SWEEP + HORIZON NEXT (first half) — TASK GRAPH

> ALEX-ELITE dual teams. Baseline: 81b3fbb. Source: mission17-10x-plan.md
> §HORIZON NEXT + carried-over C4 BASELINE sweep. Migration slots: next free
> is 0029+ (0027 cost indexes, 0028 taken by C5 if landed — check ls).

| Lane | Mission | Owned paths | Team |
|---|---|---|---|
| B1 | BASELINE sweep — run the C4 harness full locust sweep; publish docs/benchmarks/BASELINE.md numbers (concurrent ceiling, run-start p50/p95, events/day, $/run) | scripts/loadtest/ · docs/benchmarks/BASELINE.md | 2 |
| N1 | Stateless orchestrator phase 1: run lifecycle state → durable reads (rehydration SQL exists); approvals as CAS on migration 0013 plan-version column; evict terminal runs from RAM; N-replica safe | section4/src/harness_orchestrator/service.py state paths · tests incl. two-replica simulation | 1 |
| N2 | Slim the bus — NOTIFY carries signal only (seq, project_id, type); payload fetch table-side; enforce <8KB at publish; DONE_SIGNAL diffs ship references not text | section1/src/harness_identity/eventbus.py publish path · section4 emit sites · tests | 1 |
| N3 | Partitioned retention — monthly range partitions on agent_events(ts); attach/detach replaces DELETE churn; extend retention to notification_outbox + audit_log; fresh-install migrate() probe included | section1/migrations/00XX_partitioning.sql · deploy/runners/retention* · tests | 1 |
| N4 | Canned demo — POST /demo/seed materializes recorded-event replay: project + repo + one COMPLETED run with real diff/review/PR, zero provider keys; two-click stranger demo | section4 or S1 demo route (NEW) · seed fixtures · tests | 2 |
| N5 | Client SDK v0 — generate Python client from the 7 OpenAPI specs; extract ws-ticket/replay/dedup runtime from section7 lib into generator runtime; publish internally; TUTORIAL.md section | scripts/sdk-gen/ (NEW) · clients/python/ (NEW) · TUTORIAL.md | 2 |

Pair note: N1 and N2 touch eventbus/orchestrator seams — N2 lands first,
N1 rebases on it. B1 runs continuously; its numbers gate Horizon NEXT exit.
