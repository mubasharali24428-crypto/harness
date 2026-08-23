# WAVE 8 — TASK DEPENDENCY GRAPH (orchestrated by ox-alpha)

Method: subagents execute tasks in parallel; ox-alpha manages, verifies every
delivery independently, and merges at graph completion. Status updates go in
this file (the graph is the tracking artifact).

```
                    ┌─────────────────────────────────────────────┐
                    │ BASELINE a1f7f68 (all suites green, CI ✅)  │
                    └──────────────┬──────────────────────────────┘
                                   │ fan-out (no interdependencies)
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌───────────────────┐   ┌────────────────────┐   ┌────────────────────────┐
│ A. OBSERVABILITY  │   │ B. TLS PROXY       │   │ C. BACKUP RUNBOOK      │
│ structured JSON   │   │ Caddy reverse      │   │ pg_dump PITR script +  │
│ logs + request IDs│   │ proxy + /metrics   │   │ runbook + restore test │
│ + /metrics on S4  │   │ compose profile    │   │                        │
│ files: section4/  │   │ files: deploy/**,  │   │ files: deploy/backup/, │
│ deploy/services.py│   │ section3/src only  │   │ docs/runbooks/         │
└─────────┬─────────┘   └─────────┬──────────┘   └───────────┬────────────┘
          │                       │                          │
          └───────────────────────┼──────────────────────────┘
                                  ▼
                    ┌─────────────────────────────────┐
                    │ MERGE GATE (ox-alpha)           │
                    │ 1. independent re-verification  │
                    │    per branch (tests+ruff+boot) │
                    │ 2. conflict resolution          │
                    │ 3. ledger + STATUS + commit     │
                    │ 4. push + CI green              │
                    └─────────────────────────────────┘

FILE-OWNERSHIP RULES (conflict avoidance — agents must not cross these):
  A: section4/src/harness_orchestrator/{logging_utils.py,api.py},
     deploy/runners/services.py::app_orchestrator ONLY
  B: deploy/{Caddyfile,docker-compose.yml,runners/services.py::app_models},
     section3/src/harness_router/api.py (metrics route ONLY)
  C: deploy/backup/**, docs/runbooks/** (new dirs)
  SHARED-HOTSPOT: deploy/runners/services.py → ONLY A touches
  app_orchestrator; ONLY B touches app_models; both APPEND separate blocks;
  merge order at gate: A then B then C (C has no code conflicts possible).
```

## Node status (updated by ox-alpha)

| Node | Agent | State | Verification | Merged |
|------|-------|-------|--------------|--------|
| A observability | wave8-a (sa-0-86825afc) | DONE | ✅ S4 102/102 + ruff clean; verified through real deploy app: JSON lines spec-exact, X-Request-ID echo/mint, route-template labels, zero raw-id leak | ✅ this commit |
| B tls-proxy | wave8-b (sa-0-8e94ad33) | DONE | ✅ independently re-verified: compose config OK both modes, caddy validate "Valid configuration", S3 47/47 + ruff clean, /metrics counters live | ✅ this commit |
| C backup-pitr | wave8-c (sa-0-7092c2fd) | DONE | ✅ independently re-verified: own backup+verify_restore runs PASS (row counts identical users/teams/projects/agent_events), retention prunes, footprint = deploy/backup/ + docs/runbooks/ only, +x bits present | ✅ this commit |

## Merge checklist (gate exit criteria)

- [x] A: S4 suite green (102) + JSON log format proven in captured output + /metrics scrapes
- [x] B: compose config valid both modes + caddy validate + :8003/metrics 200
- [x] C: backup produced + restore into scratch PG verified row-for-row
- [x] Full 8-section sweep green after merge (461 passed)
- [x] GAP Part 10 ledger rows + STATUS.md wave entry
- [x] Single wave commit pushed; CI all jobs green
