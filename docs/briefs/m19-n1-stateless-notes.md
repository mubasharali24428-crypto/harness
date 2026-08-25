# M19-N1 — stateless orchestrator phase 1: lane notes

> Lane N1, Team 1. Landed against HEAD after N2's bus slimming (diffstore /
> save_diff / get_done_diff) merged mid-flight; full section4 suite green
> (399 passed, 5 skipped) + ruff clean at handoff. NO git writes (lane law).

## What landed (all M19-N1-tagged)

- **Durable-first lifecycle reads** (`service.py`): `_plan_decision_row` /
  `_plan_is_approved` / `_refresh_plan_cache` read migration 0013's
  `agent_plans.status`+`version` FIRST; the planner dict is a converged cache.
  `_durable_lifecycle_head` / `_execution_already_started` fence start/resume
  off the event log.
- **CAS-first decisions**: `OrchestratorService.approve/reject_plan` →
  `_decide_plan`: read seen version → CAS `_persist_plan_status(expected_version)`
  → only the winner mutates memory/emits. Losers raise `PlanAlreadyDecidedError`
  (api.py maps BOTH decision routes to 409 PLAN_ALREADY_DECIDED now) and
  converge their cache to the winner's verdict.
- **Terminal eviction**: `_evict_if_terminal` drops DONE runs from RAM **only**
  when the durable log replays the same verdict (`_replayed_durable_state`
  emulates done-gate PATH semantics) AND this process's publish chain has
  drained (`await self._publish_chains[run_id]`). Seams: `_ensure_run`,
  `stop_agent`, `signal_done`, `poll_once`. Evicted runs stay readable:
  engine results replay from ENGINE_COMPLETED/FAILED payloads
  (`_load_engine_result`), and `_run_aggregate` consults
  `_has_durable_events` before answering the no-oracle 404.
- **Exactly-once spawner election** (`start_execution`): queue-backed stores
  elect THE spawner via the `enqueue_run` INSERT race; losers return BEFORE
  emitting EXECUTION_STARTED (log carries exactly one announcement).
  Bare/no-queue stores keep legacy direct-spawn. `poll_once` re-syncs a stale
  cache from `_replayed_durable_state` instead of discarding a legitimate claim.
- **Rehydration fix found by the new tests**: replaying REVIEW_PASSED used the
  flat `_EVENT_STATE_MAP` → resurrected finished runs as eternal "executing"
  (H-008 never reached rehydration). Rehydration now replays the done-gate
  path (ENGINE_COMPLETED opens gate from EXECUTING; REVIEW_PASSED behind an
  open gate seals DONE).
- Tests: `section4/tests/test_stateless_orchestrator_n1.py` (10 tests):
  CAS approve race (one winner/one emission), mixed-race reverse direction,
  durable-first reads vs poisoned cache, cross-replica approve→start handoff,
  eviction+replay correctness, conservative keep-when-unproven pin, no-404
  guard, stop-seal conservatism, two-replica propose→approve→start sim,
  two-replica worker-poll exactly-one-claim.

## Pin updates (attribution: M19-N1)

- `test_plan_concurrency.py::test_legacy_service_approve_survives_lost_race_without_raising`
  → renamed `test_service_approve_on_already_decided_plan_conflicts_and_converges`.
  The legacy best-effort absorb is GONE deliberately: the durable CAS is now
  the service-level gate, so a late decision loses loudly (typed conflict)
  and its cache converges. Everything else in that suite passes unchanged
  (sequential re-approve idempotency rides the pre-CAS short-circuit).

## Known limits (phase 2 material)

- Concurrent duplicate proposes still mint two mirror rows (client error;
  needs an idempotency key or run-scoped unique index — schema work).
- Engine RESULTS remain RAM-only for LIVE runs (no schema change allowed);
  terminal runs replay theirs from event payloads.
- `plan_gate._locate_plan`'s in-memory scan precedes the mirror lookup; fine
  at current scale, worth an index-friendly rewrite if run counts grow.
- Sibling-lane observation for ATLAS: `tests/unit/test_demo_seed.py` (N4,
  untracked during my run) had rotating failures unrelated to N1 (fixture
  payload JSON-string handling + reviewer-vs-fixture mismatch). Not touched.
