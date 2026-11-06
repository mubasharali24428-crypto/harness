# W28 — engine-driven subagent delegation (work order)

**Date:** 2026-11-05
**Status:** AUTHORISED by the owner (2026-11-05, plan approved: re-verify W22 →
design → implement → local verify → drill → all gates). This document is the
scope contract. Anything beyond it is out of scope and needs an amendment.
**Scope guards:** no W29 (BYO-runner/third-party MCP); no widened permissions
anywhere; parent's delegated scope stays a subset of the delegating user's
effective permissions; child ceiling ≤ parent; no user-visible surface change
without review; no dispatch of unapproved programs.
**Related:** [`m2-continuation-plan.md`](../m2-continuation-plan.md) (W28),
[`roadmap-3.0.md`](../roadmap-3.0.md) stage 6,
[`hx-execution-architecture.md`](../hx-execution-architecture.md),
[`agents.md`](../agents.md),
[`execution.md`](../execution.md),
[`2026-11-04-w31-scheduler.md`](../decisions/2026-11-04-w31-scheduler.md),
`section4/src/harness_orchestrator/agent_tree.py`, `run_queue.py`, `execution.py`,
`engine_worker.py`, `workspace_gate.py`, `engine_runtime.py`; tests in
`section4/tests/unit/`.

---

## Why this exists

W32 (roadmap stage 9) needs an **approved scheduling proposal** to execute. W31's
merged pilot proposes assignments; nothing turns a proposal into work. The engine
has no spawn-and-track subagent path: the agent registry (`agent_definitions` →
`agent_assignments`, migration 0044) is a ledger of approvals and a scope gate
with no engine call site. W28 builds the engine-driven spawn, the isolation,
quotas, ownership and the reviewed-merge proof for it.

**The one delegated task that must exist by the end of W28** (roadmap stage 6 exit
evidence): *one task, two subagents, one cancelled mid-run with no orphan
processes, and a reviewed synthesis.* Everything in this work order exists to make
that demonstration possible and audited.

## What exists today (verified, so the work hugs it)

- **Run queue + claim** (`run_queue.py`, M20-T1 claim batching, adaptive backoff,
  durable; gates re-read on claim: N1 CAS, Wave 9 B budget pre-spawn, live fence,
  B5 executor width). Every claimed run runs the engine in `engine_worker.py` as
  an OS-isolated process with scrubbed environment.
- **Worker** (`worker.py`) drains the queue; the W22 recovery implementation
  includes a fence loop per claim (`_holds_live_fence`) and the durable orphan
  sweep (`run_limit_sweeper`, `workspace_gate`'s inactive sweep) that reaps runs
  whose claim dies.
- **Agent registry/trees** (`agent_tree.py`, migration 0044): approved-definition
  check, depth/live-children/run budgets via CAS on the parent row (or run pool),
  scope subset on three dimensions (definition ∩ parent ∩ delegating user, pure
  policy), approver != reporting agent, fallback recorded, tool calls metered,
  every step audited. **Honest limit:** only the ledger changes; nothing starts or
  stops a process.
- **Execution limits** (`execution.py`, `docs/execution.md`): seconds / model
  calls / tool calls / cost — enforced at run start, claim and by
  `sweep_running_limits`; atomic `expectedVersion` edits; abandon/retry cannot
  duplicate side effects; retry resumes the SAME engine session (never a fresh one).
- **Workspace gate** (`workspace_gate.py`): non-active workspaces accept no new
  runs; the archived-workspace sweep stops still-executing runs.
- **Contracts**: `WorkflowRequest`/`WorkflowResult`, events vocabulary, policy
  engine. No field carries a proposal reference (W32 seam gap — expected; this
  work order does not fix that).

## What W28 adds, in four slices

### S1 — Delegate verb on the durable path (spawn half)

- New request to `agent_tree.py`: `POST /runs/{runId}/delegate` — body: approved
  `agentDefId`, `task` (title/desc), `quantity` (≤ `max_children` of the parent or
  pool). Server mints one child assignment per child, linked `parent_id =`
  caller's assignment on this run, each with its OWN child **run** queued into the
  durable run queue. W8 quotas gate every mint (same CAS paths, same env caps).
  Parent keeps its own run untouched; `runId` of the child is fresh.
- Child run carries a declared `parent_run_id` (nullable, additive migration only
  if not present — check migration 0049+ before minting one) and a
  `subagent_root` path: an isolated directory under the workspace root (e.g.
  `<workspace>/.harness/subagents/<child_runId>`) — created by the parent's claim
  task, idempotent, and owned by the child.
- **Isolation contract:** the child's engine worker sees ONLY its subagent_root
  as `workspace_root` (same knobs as today's per-run worker), its own
  `session_root`, the child run's `session_id = child_runId` (so the engine's
  native continuation still works per child).
- Dispatch: the child run claims through the SAME gates as a normal run
  (budget pre-spawn, workspace active, fence). Any parent-granted material the
  child needs crosses via the WAL/evidence path, never via shared mutable state.

### S2 — Ownership and conflict handling

- Per-parent file claim ledger (path → claim set), gated at both write-crossing
  time: a child crossing the harness workspace-gate on behalf of its run for a
  pooled path that another child of the same parent claimed is refused
  (409 `SUBAGENT_CLAIM_CONFLICT` — new code, additive, audited).
- Reads are never claimed. Claims release on terminal child outcome; a dead
  child's claims release on the W22 orphan sweep before the next claim of the
  parent is admitted — that is the "conflict handling" story and it is the same
  sweep the W22 drills already exercise.
- Synthesis rule: child outputs are collected by a deterministic order
  (assignment id order), not completion order; the reviewer's identity is the
  parent's reviewer (never the child), by the same CAN_APPROVE_PLANS check.

### S3 — Cancel, reap, retry

- Parent cancellation (existing fence path) cascades to live children: child
  claims are fenced (same tick the parent gets it); children get `stop_agent`
  with reason `parent_cancelled` (existing vocabulary; audited).
- Orphan reaping: reuse `run_limit_sweeper`/W22 sweep — a child whose parent is
  DONE/BLOCKED with no outstanding claim is swept back by the WAL reconciler, not
  a new sweeper. **Exit test: kill a child OS process mid-run; the child is
  reaped, its claim released, the parent's synthesis offers the conflict
  resolution, no orphan process remains.**
- Retry of a child is a retry of the COHERENT parent attempt, never a per-child
  fresh retry (same law as today): same engine session per child is *allowed*, or
  the whole parent attempt may restart with the same child run ids held in a
  requested-state reservation. Decision recorded in the work-order PR review.
- No new retries beyond the existing safe-retry law.

### S4 — Proof drill (roadmap stage 6 gate)

- A W22-style scripted drill: one delegated task, two subagents, one is killed
  mid-run. Evidence (JSONs under `docs/briefs/evidence/w28/`) proves: two child
  claims, one stop with `parent_cancelled`, reap by the existing sweep, parent
  synthesis reconciles on the next claim, no orphan processes (`ps` assertion),
  reviewed synthesis validates.
- Drill runs only on a throwaway workspace; never touches a real repo.
- Sections stay green: `make test-section4`, plus the new suites; ruff;
  `make static-gates`; full local suite on the merge candidate.

## What is explicitly not in this build

- No BYO-runner phase (W29); no third-party MCP federation.
- No external subagent process beyond the existing isolated engine worker.
- No change to Section 5 SandboxHostBridge scope beyond using it.
- No change to the contracts package except an additive, mirror-synced field if
  S1 finds one is unavoidable (contract bump with TS mirror in the same commit).
- No UI; the synthesis surfaces through the existing review record.

## Artifacts and semantics

- Migration: only if an additive field/table is needed (preference: reuse 0044 and
  `execution` — no new table unless the design review forces it).
- Audit: every mint/cancel/reap is an `agent.spawned | agent.cancelled | …`
  event with the parent run id, already audited paths.
- Rollback: single revert commit — the feature is additive behind a marker;
  `HARNESS_SUBAGENTS=off` (new knob) disables the delegate verb entirely.

## Risk register

| Risk | Mitigation |
|---|---|
| Subagent workspaces leak | Parent-owned cleanup in claim epilogue; sweep owns dead-parent case; drill proves both. |
| Budget overshoot by children | Parent pool reservation: children reserve from the parent assignment row (existing 0044 machinery) — no new money path. |
| Child outlives parent | Parent terminal-state gate blocks new claim admission for children (S3 fence). |
| Conflict storm on shared paths | Claim ledger blocks the second claimant with 409, not a merge. |
| "Reviewed" synthesis silently differs from child output | Digest pins synthesis input: parent carries child digest list; reviewer compares. |

## Verification plan (per slice)

| Slice | Verify |
|---|---|
| S1 | section4 delegate tests; env FF default off; spec-drift gate green |
| S2 | claim-conflict tests; cancellation preserves audit trail |
| S3 | parent→child cancel tests; orphan-kill drill (marked, runs in the W22 suite) |
| S4 | drill JSONs in `docs/briefs/evidence/w28/`; full local suites green |
