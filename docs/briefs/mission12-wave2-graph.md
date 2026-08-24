# MISSION 12 — WAVE 2 (core UX completion) — MULTI-TEAM CAMPAIGN GRAPH

> ATLAS-authored. Baseline: `b255ce2` (Wave 1 closed). Consumes part4 §1
> WAVE-2 sketch + part2 clusters + V-Pair advisories.
>
> **CAMPAIGN TOPOLOGY (user-directed):** 5 ALEX-TEAM squads × 10 agents = 50
> builders, assisted by 5 VERIFIER-TEAMS × 6 = 30 verifier agents. Platform
> caps concurrent children at 10, so the campaign runs as SEQUENTIAL WAVES of
> ≤10 dispatches; every squad gets multiple lanes across waves. Verifier
> pairs deploy the moment their vertical's lanes pass ATLAS gate (R8
> mitigation — never wave-end batched).
>
> Skills/MCP: lanes may use any installed skill (harness-monorepo,
> systematic-debugging, test-driven-development, github/*, etc.). If no skill
> covers a need, author one in-place under ~/.hermes/skills/ and reference it
> in the report (real-time skill creation is sanctioned).

## WAVE 2.1 LANES (first 10 dispatches)

| Lane | Mission | Owned paths | Squad |
|---|---|---|---|
| E1 | @ai real LLM wiring: S6 invoke_ai routes through S3 call_model with team BYO keys (KeyVault decrypt); regex engine becomes fallback when no key; kill client's duplicate invokeAi call | section6/src/harness_meeting/{ai_participant.py,service.py} · section7/app/meeting/page.tsx · tests both sides | ALPHA |
| E2 | apiFetch 401 interceptor → clearSession + /login?expired=1; sign-out wipes projectId/runId/prId/goal; friendly error map | section7/src/lib/api.ts · lib/auth.ts · app/login/page.tsx | ALPHA |
| E3a | Roster API: member removal + role-change endpoints (admin-only, audited via audit_emit) | section1/src/harness_identity/teams_api.py (NEW or teams.py extend) · tests | BETA |
| E3b | Runs click-through + roster UI: runs rows Link to detail; team page remove/role-change controls consuming E3a | section7/app/runs/page.tsx · app/settings/team/page.tsx · teamApi.ts | BETA |
| E4a | prId producer: S8/S2 write prId to run on PR open (event + persistence) | section2/src/harness_github/* (PR-open path) · section8 consumer hook · tests | ALPHA |
| E4b | Review page consumer: derive prId from run events/localStorage set by E4a; dead-end removed | section7/app/review/page.tsx · lib/auth.ts setter usage | ALPHA |
| F1 | Error envelope unification: global RequestValidationError handler returning error_body() platform-wide + Idempotency-Key middleware on mutating POSTs (S3 router-level) | contracts-adjacent handlers in each section's app factory call site in deploy/runners/services.py (ATLAS merge) · per-section middleware file NEW · tests | BETA |
| F2 | Optimistic concurrency for approve/reject: version column migration 0013 + last-write-wins eliminated | section1/migrations/0013_plan_version.sql · section4 plan paths · tests | BETA |

## WAVE 2.2 LANES (next 10, after 2.1 gates)

E5 WS-truthful live pill + reconnect backoff + guarded JSON.parse · E6 meeting
persistence across refresh · G1 agent_events retention sweep job · G2 rate-limit
rebalance onto expensive ops · G3 request-size caps platform-wide · G4 docs
truth batch · G5 DX batch from raw-14 top items · V-lanes interleaved.

## PAIR CONTRACTS

W2-C1 (E3a↔E3b): DELETE /api/v1/teams/{id}/members/{userId} → 204;
PATCH .../members/{userId} {role} → 200 {userId, role}; errors error_body();
audited via audit_emit (member.removed/member.role_changed).
W2-C2 (E4a↔E4b): PR-open path emits event type pr_opened WITH payload {runId,
prId, url}; review page subscribes and calls setPrId; deep-link /review?pr=.

## VERIFIER ASSIGNMENT (5 teams)

VT-1 → AUTH+ENVELOPE vertical (E2+F1) after 2.1 · VT-2 → MEETING/@ai vertical
(E1) anti-synthesis-critical · VT-3 → ROSTER/RUNS pair (E3a+E3b, W2-C1) ·
VT-4 → REVIEW pair (E4a+E4b, W2-C2) · VT-5 → CONCURRENCY (F2 migration +
approve/reject race). Each = PRIME+SUB pair protocol; standing seam probes
apply (DTO parity, route census, enum equality, audit walk, authz matrix,
anti-synthesis, restart persistence).

## MERGE LAW

ATLAS commits per gated lane group; single mission push at wave close.
Sole-owner: services.py include blocks pre-assigned; state_machine.py stays
with whoever holds D1 semantics this wave (currently clean post-W1).
