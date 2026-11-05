# W32 execution seam — feasibility brief (read-only; no dispatch, no code)

**Date:** 2026-11-05
**Status:** FEASIBILITY BRIEF — options for review; authorises nothing. Merging
or acting on any option requires a decision record and, for options B/C, the W28
delegation gate plus owner approval for a contract change.
**Recorded by:** the coding agent (post PR #19 merge, after the owner ratified
the W31 pilot ADR).
**Related:** [`roadmap-3.0.md`](../roadmap-3.0.md) stage 9,
[`m2-continuation-plan.md`](../m2-continuation-plan.md) (W32),
[`hx-execution-architecture.md`](../hx-execution-architecture.md),
[`agents.md`](../agents.md),
[`decisions/2026-11-04-w31-scheduler.md`](../decisions/2026-11-04-w31-scheduler.md),
`section4/src/harness_orchestrator/workflow_runs.py`,
`section4/src/harness_orchestrator/service.py` (`run_workflow`),
`contracts/src/harness_contracts/workflow.py`,
`packages/telemetry/`

---

## What W32 is

Connect an **approved scheduling proposal** (a set of allocation assignments, as
produced by the W31 allocation contract) to **approved Hx/Section 4 execution**,
evaluate OpenTelemetry for run/worker/tool correlation, and audit telemetry for
secrets and tenant isolation. Exit evidence: end-to-end approved task, policy
denial, cancellation, recovery and review on a verified deployment; trace
redaction and cross-team isolation tests; no unapproved dispatch, no automatic
merge.

This brief records what exists today, what is missing, the options and the gate
order. It authorises nothing.

## What exists (verified in this repository, 2026-11-05, at `1dd3ebc`)

I read the code and docs rather than relying on claims:

| Surface | State | Evidence |
|---|---|---|
| Hx driver + Section 4 runner | Runs an approved workflow end-to-end as a single-workflow, single-process-synchronized pipeline: check → manifest → digest bind → authorise → run; events published by Section 4, persisted by Section 1; evidence says `approved: false`, `autoMerge: false` | `packages/language` M1–M8; `workflow_runs.py` takes a `WorkflowRunner`; `contracts/src/harness_contracts/workflow.py` (`WorkflowRequest`, `WorkflowResult`, `WorkflowRunner` Protocol — synchronous, never from an event loop) |
| Human approval surface | **None** — "Human approval / review surface: out of scope by design — the evidence says approved:false and stops" | `docs/hx-execution-architecture.md` §4.4 |
| Agent registry + assignment trees | Server-side registry, quotas and ledger exist (`section1/src/harness_identity/agents.py`, `0044_agents.sql`; `section4/src/harness_orchestrator/agent_tree.py`) but **the engine never calls spawn**; scope is recorded and subset-checked, not enforced inside the sandbox; cost is caller-reported; spawn/pause/cancel change rows and audit rows, not processes | `docs/agents.md` ("What is NOT wired") |
| Hx effects model | Effects are declared, capability tables closed, budgets host-enforced, revocation per-capability; `model.invoke` routed by the host; nothing maps tasks to agent capacity | `packages/language` M4/M6; `docs/hx-execution-architecture.md` |
| Section 5 confinement | One real op each proven (`repo.read`, `verify.tests`) via the live `LocalSandboxRunner`; everything else is as safe as the bridge wiring | §4.3, §3, M3 |
| OTel | `packages/telemetry/` (F112): frozen events → OTel spans, deterministic ids from `runId`, W3C `traceparent` in/out, OTLP/HTTP export; W31/W32-relevant OTel substrate exists and is tested (`test_otel.py`) | `packages/telemetry/src/harness_telemetry/otel.py` |
| Frozen contracts | `contracts/` unchanged by W31; `WorkflowRequest` carries no field for a proposal reference — pinning a multi-file program already wants one (M8 limit #12: `run_source` takes `expected_program_digest`, but the frozen `WorkflowRequest` has no field carrying it) | `contracts/src/harness_contracts/workflow.py`; `docs/hx-execution-architecture.md` §6.12 |
| Scheduler side | Neutral allocation contract + interval model merged to `5b726cb` (split commits `f23aec6`/`bf17bd0`/`3e52aa3`); proposals only; no LICENSE; imported by path, never copied | `docs/decisions/2026-11-04-w31-scheduler.md` (ACCEPTED 2026-11-05, pilot scope) |

## What is missing (the seam)

1. **No request type carries a scheduling proposal.** `WorkflowRequest` holds a
   program, grants, a profile, inputs and principal permissions. A proposalRef
   field (or a sibling request) does not exist; today a caller would smuggle a
   proposal through `inputs` or write a bespoke endpoint.
2. **No approver for proposals.** There is no human approval object any host
   crossing can check before the first call. `run_workflow()` in
   `service.py:1366` carries a host decision; nothing approves or re-reads it.
3. **No proposal → tasks mapping.** Nothing turns `{task_id, resource_id,
   start_slot, end_slot}` into Hx programs, agent-spawn calls or tool invocations.
   That mapping is the product question — it is where "scheduling" meets "agents".
4. **No per-crossing budget for a schedule step.** Hx budgets price tool calls,
   not wall-clock scheduling. Cost control for W32 needs a host price table
   (already supported since M6) and a decision of what a "task crossing" costs.
5. **W28 (delegation gate) unmet.** Engine-driven spawning/cancellation of
   subagents behind the W8 quota gate does not exist; the BYO-runner leg (W13)
   is not built. W32's acceptance text starts only after this gate.
6. **W30 records open.** No candidate-audit decision records exist yet; the
   Scheduler itself is unresolvable for distribution (no LICENSE), which limits
   how far a proposal can travel before a legal ruling.
7. **OTel attribution for worker/tool correlation untested.** `packages/telemetry`
   maps events to spans; mapping an agent hierarchy (parent/child spans) onto
   `agent_tree` assignments has not been verified, and telemetry tenant-isolation
   and secret-redaction properties for this flow are unaudited.

## Options

**A — do not build yet (recommended end-state for now).** Keep the W31 pilot at
proposal + validator level; W32 implementation is blocked on W28/W30. Revisit at
the stage-9 gate with those closed. No code; this brief is the record.

**B — read-only seam spike in `packages/language`.** A single Hx fixture program
whose inputs are a proposal-shaped array, validated by the same independent
validator (imported as a test dependency, or mirrored), plus negative fixtures
(stale proposal, digest mismatch). No Section 4 wiring, no frozen-contract
change, no approval surface added. Proves the shape of "approved proposal
in", not execution. ~1 small commit + fixtures; still leaves 5–7 above open.

**C — full bridge.** Frozen-contract change (`WorkflowRequest` gains a
`proposalRef` or similar — version bump + TS mirror + mocks in one commit), a
Section 4 approval-before-run hook, a proposal → agent-assignment mapping, a
W28 delegation runtime, OTel span wiring with a tenancy audit. This is W28 +
W32 together, i.e. it needs its own plan, its own owner approvals (contract
change, dispatch authorisation, presumably W28 ratification) and is not a
single session.

**D — bricklayer stop.** Merge remains the end; W32 stays unstarted; W13/W28
proceed per the roadmap.

## Gate order

```
W31 ACCEPTED (2026-11-05)  →  W30 records or explicit out-of-tree ruling
                              → W28 delegation gate (or owner waiver, recorded)
                              → W32: contract change + approval hook + OTel audit
                              → stage 9 acceptance review (no dispatch until then)
```

## Unknowns requiring the owner

1. Ratify or schedule stage 6 (W28)? It is the gating dependency for B-to-C and
   for W29/W13.
2. Keep the Scheduler out-of-tree forever (import by path, CI stays?) or port it
   in once `LICENSE` resolves? This changes what W32 may bundle.
3. OTel target and redaction bar for W32 — is `packages/telemetry` the sink, and
   is team-scoped archive access sufficient isolation, or does W32 require a
   separate trace pipeline?
4. Approval UX — where should a human approve a proposal (existing review UI?
   new surface)? No approval surface exists in this repo today.

## What this brief is not

Not a plan to dispatch anything; not an approval to modify contracts; not a W28
waiver; not a claim that execution is safe today. It is the map the owner asked
for, to decide the next move.
