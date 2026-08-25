# MISSION 18 — 10X PLAN HORIZON NOW — TASK GRAPH

> ALEX-ELITE dual teams. Baseline: Phase C plan @ 70b217c. Source:
> docs/briefs/mission17-10x-plan.md §HORIZON NOW (C1–C6). Migration slots:
> next free 0027+ (check ls). Exit criteria per plan §NOW.

| Lane | Mission | Owned paths | Team |
|---|---|---|---|
| C1 | Authz fail-closed sweep — kill triple fail-open in authz.py:50-66 (unwired factory ⇒ deny in prod wiring, allow only behind explicit test flag; missing projectId ⇒ deny + audit_emit; dangling row ⇒ deny); same posture pass over plan_gate scoping | section4/src/harness_orchestrator/authz.py · plan_gate.py posture pass · tests | 1 |
| C2 | One budget truth — unify cap resolution teams.budget_usd > billing_plans.monthly_budget_usd > env > unlimited; S4 _budget_gate routes through working reserve() advisory-lock path; single fail-CLOSED posture + explicit degrade flag; one month-window definition (DB clock date_trunc) | section3 budget_guard.py · section4 budget gate path · section1 settings if needed · tests | 1 |
| C3 | Cost-gate latency kill — migration 0027: CREATE INDEX cost_ledger(created_at) + (team_id, created_at); optional daily rollup table fed by outbox pattern; gate reads rollup | section1/migrations/0027_cost_ledger_indexes.sql · section3 ledger read path · tests | 1 |
| C4 | Throughput truth probe — k6/locust profile driving propose→approve→run against compose PG; publish measured concurrent-runs ceiling, run-start p50/p95, events/day capacity to docs/benchmarks/ (NEW) | scripts/loadtest/ (NEW) · docs/benchmarks/ (NEW) · compose probe profile | 2 |
| C5 | Invite + ticket hygiene — invite redemption binds invited email (or one-time claim-link); drop ?token= long-lived WS fallback; require X-WS-Fingerprint when mint carried one; rate_buckets GC (ws_tickets sweep pattern) + migration 0028 if columns needed | section1 invites.py · ephemeral/app.py ws paths · ratelimit GC · migrations/0028* · tests | 2 |
| C6 | Rollback story v0 — image version tags in compose; rollback runbook (down/up procedure, schema-drift refusal at deploy: applied ≠ declared migrations ⇒ refuse start); docs/runbooks/rollback.md (NEW) | deploy/docker-compose.yml tags · deploy/runners/ migrate drift gate · docs/runbooks/rollback.md · tests where feasible | 2 |

GATE LAW: same as M17-B. Exit criteria: authz cannot silently allow; budget
caps agree under race probes; cost gate O(1) at any ledger size; throughput
ceiling is a published number.
