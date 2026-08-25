# MISSION 17 PHASE B — FIX LANES — TASK GRAPH

> ALEX-ELITE dual teams. Baseline: Phase A findings @ 5107567. Each lane
> fixes one CRITICAL/HIGH cluster from the seven findings files, with a
> regression test proving the fix. Migration slots: next free 0027+ (check ls).

| Lane | Fixes | Owned paths | Team |
|---|---|---|---|
| B1 | M-ALPHA CRITICALs: budget_guard asyncpg named-bind translation + INSERT..SELECT serialization on PG (advisory lock or serializable txn); NUMERIC overflow guard | section1 budget_guard.py · cost_ledger.py · tests | 1 |
| B2 | S-GAMMA OPS-01/15: live-db PITR wiring (archive_mode on real cluster), migrate.py into CI against PG, backup cron target fix | deploy compose db block · .github ci · deploy/runners/* · docs/runbooks | 1 |
| B3 | P-BETA C-1/C-2: isolation-guard upgrade (catch package-imports + sys.path tricks), dedupe envelope middleware to single shared install, delete dead forks | section*/envelope_middleware.py · scripts/ci_route_module_guard.sh · tests | 1 |
| B4 | MAC2 F2/F8: plan_gate team-scoping (deny cross-team), rate-limit keying fix (hash IP /64 + proxy header policy) | section4 plan_gate.py · section1 ratelimit.py · tests | 2 |
| B5 | PBC1 F2: engine-run concurrency — dedicated bounded pool w/ timeouts, hung-provider circuit breaker; advertise honest caps | section4 service/engine wiring · tests | 2 |
| B6 | MAC1 F-1/F-4: contributor bootstrap (make venvs target), Quick-start fix so first run succeeds; README accuracy pass | Makefile · README.md · scripts/bootstrap* · docs | 2 |

Gate law: same as waves — footprint audit, suite rerun, probe re-execution
(the original finding's probe must FAIL pre-fix and PASS post-fix).
