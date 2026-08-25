# MISSION 20 — HORIZON NEXT part 2 + FIRST 10X LEVER — TASK GRAPH

> ALEX-ELITE dual teams. Baseline: 9c71c0c. Source: mission17-10x-plan.md
> §NEXT remainder + M19 BASELINE wall. Migration slots: next free 0030+
> (0027/0028/0029 taken — check ls).

| Lane | Mission | Owned paths | Team |
|---|---|---|---|
| T1 | **Claim batching — the first 10x lever.** RunQueueWorker._loop claims up to N runs per cycle (env WORKER_CLAIM_BATCH, default = executor width); poll interval adaptive (fast when queue non-empty, backoff when drained). Must survive N1 CAS + N2 slim bus. Re-run B1's ladder post-fix — ceiling must move | section4/src/harness_orchestrator/worker.py · service.py claim path · tests incl. re-run of loadtest ladder at 32 VU | 1 |
| T2 | Observability minimum — configure_logging + RequestContextMiddleware on ALL services (currently orchestrator-only); one Prometheus scrape target + 5 alert rules (queue depth, event-bus lag, DB pool saturation, backup freshness, archiver failed_count) | deploy/runners/services.py logging wiring · deploy compose metrics endpoint · docs/runbooks/alerting.md (NEW) · tests | 2 |
| T3 | Resource limits + healthchecks — container mem/cpu/pids limits on all services; log rotation caps; readiness probes (not just liveness) per S-GAMMA OPS-03/06 | deploy/docker-compose.yml limits/probes · tests where feasible | 2 |
| T4 | Docs truth pass — fix AGENTS.md stale harness_fresh command (MAC1 F-7); document the 3 real extension seams (engine backend_runner injection, reviewer thresholds settings mapping gap, project rules loader); glossary day-one nouns completion | AGENTS.md · README.md · docs/GLOSSARY.md · docs/extensions.md (NEW) | 2 |
| T5 | TS SDK v0 — second client from the same sdk-gen pipeline (TS emit mode); publish internally; mirror the Python README structure. Unlocks "both SDKs" ecosystem claim | scripts/sdk-gen/generate.py TS emitter · clients/ts/ (NEW) · tests | 1 |

Gate law: same as M18/M19. Exit criteria: published ladder shows ceiling
moved materially (>5 journeys/s at 32 VU or documented executor-bound wall);
metrics scraped and alert rules loaded; all containers bounded; TS SDK
smoke-passes.
