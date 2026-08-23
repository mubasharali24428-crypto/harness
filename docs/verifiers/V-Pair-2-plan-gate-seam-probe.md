# V-Pair-2 — PLAN-GATE seam probe (early), M11-W1

Date: 2026-08-23 · Verifier: V-Pair-2 pre-work · Scope: W1-C1↔W1-C2 plan-gate pair
Repo state probed: HEAD `c86a03e`; pair files byte-identical to HEAD (`git diff` empty on all five pair files).
Method: static field-by-field DTO/enum comparison + runtime scratch-app walk (probe script in `/tmp/plan_gate_probe/probe.py`, zero repo modifications) + full-suite/lint baseline.

## VERDICT: READY

Pair contract verified end-to-end. 21/21 runtime probe checks passed; 16/16 backend
pair tests, 3/3 conformance goldens, 27/27 vitest green. No synthesis found.
Defect list below is dispatch material (1 pre-merge lint fix, 1 design decision,
3 advisories); none blocks the wave merge on contract-parity grounds.

## Probe results

### P1 DTO parity — PASS (exact, with 2 observations)
`PlanOut` (section4/src/harness_orchestrator/plan_gate.py:78-89) vs `AgentPlan`
(contracts/ts/src/plan.ts:8-17) vs frozen source (contracts/src/harness_contracts/plan.py:4-14):
goal, filesLikelyToChange, steps, tests, risks, estimatedCostUsd, forbiddenPaths,
expectedResult — same names, same order, same types. Nullability equivalent
(TS optionality ≈ Python default_factory defaults; neither side ever emits null).
Response wrappers match: `{planId, plan}` / `{runId}` / reject typed `unknown`
(planGateApi.ts:40 — honest, since the pair contract defines no reject body;
the server does return a body, see A2).
Wire envelope errors parse via parseApiError handling both direct and
FastAPI-wrapped `{detail:{error:{code,message}}}` (src/lib/api.ts:37-52) —
matches error_body() usage at plan_gate.py:64/178/229/etc.

Observations (not defects):
- O1: TS marks AgentPlan's six non-required fields optional while the wire
  ALWAYS carries them (server defaults). Client already tolerates absence
  (planGateApi.test.ts:61 "tolerates optional fields absent") — safe both ways.
- O2: `StartRunResponse.status: string` (planGateApi.ts:106-109) documents the
  legacy handler shape `{runId,status}`, but the deployed route currently answers
  with plan-gate's `{runId,state}` (see D2). Extra key is harmless today; will
  become correct if D2 resolves toward plan-gate ownership.

### P2 Scratch-app walk — PASS
Happy walk (plan-gate router alone, SQLite w/ seeded session per test_plan_gate.py
fixture + run_claims from migration 0006):
propose 201 {planId,plan} → agent_plans row status=proposed → approve 200 {runId}
→ startRun 200 {state:"executing"} → SM EXECUTING. Idle-start confirmed: fresh
service run begins IDLE, propose walks IDLE→PLANNING→AWAITING_PLAN_APPROVAL.
Reject path (run #2): reject 202 {status:"rejected",audited:true,...};
audit_log row (action=plan.rejected, target_type=plan, actor=u-probe) present;
agent_plans.status=rejected; SM back to IDLE; new OrchestratorService over the
same DB still sees the plan un-approved and status rejected (restart persistence).
Roles on the wire: viewer 403 ×3 (propose/approve/reject, ROLE_DENIED);
engineer 201; admin full walk. Idempotent re-approve ×3 → same runId each time.
Combined-app topology (orchestrator router + plan-gate router on ONE app, deploy
order per deploy/runners/services.py:263): all three contracted steps behave;
unapproved start answers 403 via the legacy handler (first-match wins, see D2).

### P3 Enum equality — PASS
planGateApi.ts classifyGatePhase handles exactly the 10 AgentState values of
section4/src/harness_orchestrator/state_machine.py:30-40 (idle, planning,
awaiting_plan_approval, executing, paused, awaiting_done_signal, reviewing,
blocked, pr_opened, done), mirrored identically in contracts/ts/src/api.ts:20-33
(RunState) and consumed via RunStatus.state (camelCase fields, api.ts:35-41).
Unknown states degrade honestly to "in-flight" (planGateApi.ts:167-170).
Note: classifyGatePhase maps "planning"→"no-plan" — correct-by-design here
since /plans proposes synchronously (PLANNING is never observable on the wire
for plan-gate runs).

### P4 Anti-synthesis — CLEAN
grep over all six plan-gate files for mock/stub/fake/skip/xfail/todo/placeholder/
hardcode: zero production markers. Only hits are legit vitest fetch-stubbing in
the two frontend test files (vi.stubGlobal/stubOk/stubError). Tests assert real
behavior: DB rows, SM states, HTTP codes + error codes (test_plan_gate.py),
OpenAPI golden shapes (tests/conformance/test_plan_gate_conformance.py), exact
fetch URL/method/body/bearer (planGateApi.test.ts), rendered DOM per phase
(planGate.test.tsx). No hardcoded returns or skipped paths in plan_gate.py.

## Dispatch list (fix/decide at wave merge)

### D1 — Lint violation in plan-gate stream's own committed file (fix before merge)
- section4/tests/conformance/test_plan_gate_conformance.py:19 — ruff I001
  (unsorted import block; `from fixtures.golden_fixtures ...` placement).
  Violates AGENTS.md "lint must be clean before any PR". Mechanical fix:
  `./.venv/bin/ruff check --fix tests/conformance/test_plan_gate_conformance.py`.
  (The other current ruff error, live_mode.py:50 UP042, belongs to B-lane
  uncommitted work — not this pair.)

### D2 — Duplicate POST /api/v1/orchestrator/runs/{runId}/start registration (design decision)
- Registered by BOTH section4/src/harness_orchestrator/api.py:377
  (orchestrator.start_execution) and section4/src/harness_orchestrator/
  plan_gate.py:288 (plan_gate.start_run); both routers mounted on the single
  deployed app at deploy/runners/services.py:263. Starlette serves FIRST match,
  so in deploy include-order the legacy handler wins and plan_gate.start_run
  (lines 288-312) is dead code in production — its idempotent-restart branch and
  richer error semantics never execute.
- Divergent semantics shipped to clients depending on which handler answers:
  unapproved start → 403 (api.py:385) vs 409 PLAN_NOT_APPROVED
  (plan_gate.py:303-306, what test_plan_gate.py:236 pins and planGateApi
  consumers see from the pair's own tests); success body {runId,status} vs
  {runId,state}. Empirically verified: deploy order → 403/{status};
  reversed include order → the plan-gate handler answers.
- Note: flat app.routes scans show NO duplicates (FastAPI 0.141.1 wraps each
  include_router in an `_IncludedRouter`); enumerate through `.routes` of the
  wrapper to see both registrations.
- Recommendation: pick ONE owner. Either drop plan_gate.start_run (and align
  test_plan_gate.py:228-230/298-300 + planGateApi.ts StartRunResponse to the
  legacy 403/{status} behavior), or remove/rename the legacy route for this
  path and keep 409/{state}. Do not leave both mounted silently.

### A1 (advisory, governance) — Rejected plans can be resurrected
Reject sets planner.plan_approvals[pid]=False and persists "rejected", but
nothing consults that persisted verdict later:
- Same session: reject → approve → 200, is_approved=True → start 200
  (planner.py:55-57 is a silent no-op for unknown/untracked states; approving
  simply flips True again; service.approve_plan service.py:391-397 has no
  rejected-state guard; _persist_plan_status overwrites "rejected"→"approved").
- After restart: a NEW admin session can approve the rejected plan (200) and
  start it (200); the durable "rejected" row is overwritten on approve.
If reject-is-final is intended (human-governance platform), add a rejected
guard to approve/start (409 PLAN_REJECTED terminal) + a regression test.
If re-proposal after rejection is intended, document that reject is advisory.

### A2 (advisory, observability) — Reject leaves NO event-trail; restart state relies on absence-of-persisted-propose
- sm.reject_plan (state_machine.py:113-123) emits nothing; _EVENT_STATE_MAP
  (service.py:46-59) has no rejection entry; EventType.PLAN_REJECTED exists in
  contracts/events.py but is never emitted anywhere.
- Additionally /plans mints runs without a projectId (plan_gate.py:200-201),
  so service._emit short-circuits (service.py:112-114): even PLAN_PROPOSED /
  PLAN_APPROVED from plan-gate runs are NOT persisted to agent_events.
- Consequence observed live: after reject + process restart, get_status(run2)
  reports state="idle" — correct-looking, but reached vacuously (rehydrate
  found no events at all; had a projectId been bound, replay would land the
  run at awaiting_plan_approval since the rejection was never journaled).
  Today's correctness hangs on "no project ⇒ no events"; any future wiring of
  projectId into /plans flips restarted rejected runs back to awaiting
  approval. Suggest emitting PLAN_REJECTED (+ persisting propose/approve) when
  projectId becomes available, and/or mapping a persisted "rejected" plan
  status onto SM state during _rehydrate (service.py:259-265 reads the status
  but ignores "rejected").

### A3 (advisory, environment completeness) — start_run hard-depends on run_claims existing
service.__init__ aliases run_queue_factory=session_factory (service.py:91), so
ANY wired DB makes start_run enqueue into run_claims; enqueue_run (run_queue.py:
21-36) has no try/except → a stack lacking migration 0006 turns the contract's
200 into a raw 500 (sqlite OperationalError reproduced in probe). Prod compose
runs migrate.py so the table exists; the pair's own DB-backed tests dodge it by
walking on a bare service (fixture comment "run_claims stays ABSENT on purpose",
test_plan_gate.py:88-92), so the DB-backed walk is uncovered by their suite.
Consider guarding enqueue failures to 503/error_body, and/or adding one
DB-backed walk test including run_claims.

## Files created by this verification
- docs/verifiers/V-Pair-2-plan-gate-seam-probe.md (this report)
- /tmp/plan_gate_probe/probe.py + routes_check.py (scratch, outside repo)
No repository file was modified.
