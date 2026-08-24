# MISSION 14 — WAVE 4 (product features) — CAMPAIGN GRAPH

> ATLAS-authored. Baseline: Wave-3 close HEAD (CI 32694440516 SUCCESS, then
> 9141e39 docs). Consumes part4 §1 WAVE-4 sketch + part2 clusters + part3
> roadmap NOW items not yet shipped.
>
> TOPOLOGY: same as Waves 2–3 — 5 squads × 10 = 50 builders capacity, 5
> verifier teams staggered per vertical. Platform batches ≤10 concurrent.

## W3 CARRYOVERS into W4

1. E2E suite's `GATE_VIA_UI=false` baseline: PlanGateCard is NOT yet mounted on
   any routed page (VT-3 note). Wave 4 mounts it — then flips the flag.
2. NUMERIC(12,6) budget cap ($999,999.999999): add operator-facing note to
   billing UI + .env.example comment.
3. `harness-backend-1` Docker DNS crash-loop: still open → P2 lane.

## WAVE 4 LANES

| Lane | Mission | Owned paths | Squad |
|---|---|---|---|
| F1a | Cost metering live end-to-end: S4 engine completion → S3 record_usage via internal API; cost page shows real run costs | section4/src/harness_orchestrator/service.py completion path · section3 ledger read API · section7/app/dashboard/cost/ | ALPHA |
| F1b | Keyboard support + focus management: full tab-order audit page-by-page, Enter/Space on all interactive cells, focus rings visible, skip-link | section7/src/components/AppShell.tsx · app/runs/page.tsx · app/settings/** · lib/focus.ts (NEW) | ALPHA |
| F2a | Invite lifecycle admin UI: mint/resend/revoke with status badges (draft/sent/redeemed/expired/revoked), audit-visible actions | section7/app/settings/team/page.tsx · teamApi.ts · section1 invites routes if needed | BETA |
| F2b | Responsive layout: sidebar collapses <900px, cards stack, tables scroll-x; zero fixed-px layout breaks at 375/768/1280 | section7/src/components/AppShell.tsx · globals.css · affected pages | BETA |
| F3a | Aggregate run read endpoint: GET /api/v1/orchestrator/runs/{id} enriched (state+prId+cost+steps summary) + runs index pagination cursor | section4/src/harness_orchestrator/api.py · service.py aggregate · tests | ALPHA |
| F3b | Runs detail page: consumes F3a aggregate — real state machine view, cost line, PR link when present, step timeline from events | section7/app/projects/[projectId]/runs/[runId]/page.tsx · runsApi.ts | ALPHA |
| F4 | Optimistic UI: plan approve/reject + roster remove/role-change update instantly, reconcile on response, rollback w/ toast on error | section7/app/dashboard/planGate.tsx · settings/team/page.tsx · hooks/useOptimistic.ts (NEW) | BETA |
| P2 | Docker DNS crash-loop fix: diagnose harness-backend-1 restart loop (network dns), add healthcheck + restart policy + compose network hardening; document in runbook | deploy/docker-compose.yml backend service block · docs/runbooks/ops.md (NEW) | BETA |
| DS | Design-system lane: extract shared primitives (Pill, Button, Card, Table, Modal) into components/ui/** with tokens; migrate 3 heaviest pages as proof | section7/src/components/ui/** (NEW exclusive) · dashboard/cost + settings/team migration · tests | ALPHA |

## PAIR CONTRACTS

W4-C1 (F1a↔cost page): GET /api/v1/models/spend?sinceDays=N returns {total,
byDay:[{day,costUsd}],unwired:false} once metering wired; cost page drops the
"unwired" explainer when unwired=false and renders real numbers.
W4-C2 (F3a↔F3b): aggregate shape {runId,state,costUsd?,prId?,url?,steps?}
— nullability explicit; detail page degrades honestly per missing field.
DS owns components/ui exclusively; feature lanes consume but never edit it.

## VERIFIER TEAMS

W4-VT1 → METERING (F1a): live-PG probe inserts usage → spend endpoint reflects
it exactly (NUMERIC-exact); unwired flag flips truthfully.
W4-VT2 → A11Y/RESPONSIVE (F1b+F2b): keyboard-only walk of hero journey;
viewport matrix 375/768/1280 screenshot diff sanity.
W4-VT3 → INVITES/OPTIMISTIC (F2a+F4): invite lifecycle state machine probe;
optimistic rollback on forced error.
W4-VT4 → AGGREGATE (F3a+F3b, W4-C2): field parity byte-compare; honest
degradation per null field.
W4-VT5 → DESIGN SYSTEM (DS): token coverage, no page-local one-offs remain in
migrated pages, primitive a11y (aria, focus).

## MERGE LAW

Same as W2/W3: ATLAS commits per gated group, verifiers staggered, single
mission push at close. Sole-owner: AppShell split — F1b owns focus regions,
F2b owns responsive CSS regions; coordinate via report notes, ATLAS diffs at
gate. Migration slots: next free 0021 (check ls first).
