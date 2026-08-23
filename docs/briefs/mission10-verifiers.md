# MISSION 10 — VERIFIER TEAM BRIEF (THE VERIFIERS serving ALEX_TEAM)

Coordinator: ATLAS session. Baseline for review: current HEAD at dispatch
(wip e4f7536 + ladder commits; verifiers judge WORKING TREE state, git-read-only).

Three PRIME+SUB pairs, one interface-contract vertical each. Each pair runs
as ONE delegated worker executing TWO PASSES IN ORDER:
- PASS 1 (SUB, mechanical): footprint audit vs owned paths · full suite rerun
  with pasted output · lint · grep skip/xfail/TODO/FIXME/stub patterns ·
  endpoint-existence probes. NO reading of builder self-reports first.
- PASS 2 (PRIME, judgment): on that evidence bundle — contract conformance
  field-by-field, security review, quality review, integration probe ACROSS
  THE SEAM (server via TestClient/curl-style harness, page side via
  tsc/vitest), issue classification, verdict.

## Verdicts (exactly two)
`READY-TO-MERGE` | `FIX-LOOP:<lane-id>` + specific blocker list.
Writes allowed ONLY inside the vertical's owned paths, ONLY for MAJOR/MINOR
findings (then rerun suite). BLOCKERs are report-only → coordinator routes
to lane owners. NO git commits, NO installs, NO conftest/shared edits.

## Anti-synthesis scan (every pair)
hardcoded returns standing in for logic · newly skipped/xfail'd tests ·
mocks left in production paths · TODO/FIXME stubs reported done · invented
endpoints/APIs/config names absent from the repo.

## PAIR-1 — KEYS vertical (contract M10-C1)
Owned paths (verbatim):
- A1: section1/src/harness_identity/api_keys.py · section1/tests/test_api_keys_routes.py
- A2: section7/app/settings/keys/page.tsx · section7/app/settings/keys/keysApi.ts
Contract M10-C1 (bearer auth): POST /api/v1/keys {label,provider,key} → 201
{id,label,provider,keyHint,createdAt} (raw key never returned again); GET
/api/v1/keys?teamId= → {items:[{id,label,provider,keyHint,createdAt,revokedAt}]};
DELETE /api/v1/keys/{id} → 204 soft-revoke idempotent; error_body() envelope;
admin/engineer write, viewer read-only. Security lens: ciphertext-at-rest,
hint ≤8 chars …+last4, no existence oracle cross-team, no raw-key logging.
Suites to rerun: cd section1 && ./.venv/bin/python -m pytest
tests/test_api_keys_routes.py -q && ./.venv/bin/ruff check
src/harness_identity/api_keys.py tests/test_api_keys_routes.py ;
cd ../section7 && npx tsc --noEmit && npx vitest run.
Seam probe: mount api_keys router on a scratch FastAPI app in-section (like
its own tests do) OR assert route table post-include; match keysApi.ts DTO
fields against handler response models field-by-field.

## PAIR-2 — RUNS vertical (contract M10-C2)
Owned paths (verbatim):
- A4: section4/src/harness_orchestrator/runs_index.py ·
  section4/tests/unit/test_runs_index.py · deploy/runners/services.py
  app_orchestrator include block ONLY
- A5: section7/app/runs/page.tsx · section7/app/runs/runsApi.ts
Contract M10-C2 (bearer auth): GET /api/v1/orchestrator/runs
?projectId=&status=&limit=&offset= → {items:[{id,projectId,status,createdAt,
startedAt,finishedAt}],total,limit,offset}; limit≤100 default 25; offset≥0;
team-scoped join filter (foreign rows invisible); parameterized SQL only.
Known-good context: statuses now = real AgentState vocabulary (post-fix);
startedAt/finishedAt derived from agent_events MIN(ts) — check resume-safety
reasoning; migration 0012 (ATLAS-authored) covers the perf index.
Suites to rerun: cd section4 && ./.venv/bin/python -m pytest
tests/unit/test_runs_index.py -q && ./.venv/bin/ruff check
src/harness_orchestrator/runs_index.py tests/unit/test_runs_index.py ;
cd ../section7 && npx tsc --noEmit && npx vitest run.
Seam probe: client query-builder output vs handler param names; status enum
in runsApi.ts vs state_machine.py vocabulary (must match 1:1 now).

## PAIR-3 — BILLING vertical (contract M10-C3)
Owned paths (verbatim):
- B1: section1/src/harness_identity/billing.py ·
  section1/migrations/0010_billing_plans.sql · section1/tests/test_billing.py
- B2: section7/app/settings/billing/page.tsx · billingApi.ts · billingApi.test.ts
Contract M10-C3 (bearer auth): GET /api/v1/billing/state → flag-off shape
{enabled:false, plan:null, quota:null, source:"flag-off"}; enabled local →
{enabled:true, plan:<row|"free">, quota:{monthlyBudgetUsd,usedUsd},
source:"local"}; PUT /api/v1/billing/plan {plan} admin-only; plans enum
free/pro/team BOTH sides; quota typed nullable in client; flag-off renders
upsell card ONLY (null-quota crash fixed — re-verify the guard).
Suites to rerun: cd section1 && ./.venv/bin/python -m pytest
tests/test_billing.py -q && ./.venv/bin/ruff check
src/harness_identity/billing.py tests/test_billing.py ;
cd ../section7 && npx tsc --noEmit && npx vitest run.
Seam probe: client BillingStateDto fields vs handler models field-by-field
incl. nullability; plan enum equality both sides; month-scoping test really
excludes last-month rows.

## Out of scope this round (already verified elsewhere)
A3 cost-dash, B3 onboarding, B5 rate-limit: ATLAS gate + lead APPROVED.
B4 walg: fix-loop in flight — ATLAS personal gate after close. Do NOT touch.

## Verdict table (coordinator fills after gating reports)

| Pair | Vertical | PASS-1 evidence | PASS-2 verdict | Spot-check by ATLAS |
|---|---|---|---|---|
| 1 | KEYS | ✅ footprint exact (4 paths), 9/9 + ruff clean, tsc+vitest green, anti-synthesis clean | `READY-TO-MERGE` — no blockers/majors; probe A mint→list→revoke flow 24/25 (1 self-inflicted introspection artifact, re-proven via openapi), probe B DTO parity 10/10, no-oracle byte-identical 404s, zero raw-key surface | ✅ ATLAS: 9/9 rerun + module surface spot-check pass; 422-shape note logged as platform-wide awareness item |
| 2 | RUNS | ✅ footprint exact, S4 11/11, ruff clean, anti-synthesis clean, OpenAPI probe | `READY-TO-MERGE` (1 MINOR fixed in-lane: pill map remapped to all 10 AgentStates; resume-safety + pagination walk + enum equality proven live) | ✅ ATLAS: tsc+vitest rerun green (73/73), pill spot-check pass; host node repaired via libada.3 compat symlink |
| 3 | BILLING | | | |
