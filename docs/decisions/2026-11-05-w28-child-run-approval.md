# Decision: a delegated child run waits for its own plan approval

**Date:** 2026-11-05
**Status:** ACCEPTED by the decision owner (Mubashar Ali, in session), scope W28 S1b.
**Related:** [`roadmap-3.0.md`](../roadmap-3.0.md) (stage 6, W27 approval binding),
[`2026-11-04-w31-scheduler.md`](2026-11-04-w31-scheduler.md)

## Question
When `POST /runs/{runId}/delegations` mints a child, does the child's run start on the
parent's approval, or does its own plan need a human approval?

## Decision
Its own plan needs approval. Delegation creates the child as a real run with its own run id,
proposes its plan, and leaves it in `awaiting_plan_approval`. From there it takes the ordinary
lifecycle (approve, start, `enqueue_run`, claim, `_dispatch_claim` gates), so every gate that
guards a normal run guards a child too. Nothing is auto-approved.

Rejected alternative: parent approval covers children (approved by delegation). It is faster but
a human never sees the child's plan, which weakens the W27 claim that approval is bound to what
executes. It may come back as an opt-in once W27 defines delegated approval.

## Consequences
* Migration 0075 adds `agent_assignments.child_run_id`. Cancelling an assignment stops its run
  and the runs of its descendants (`agent_tree._stop_child_runs`), also on a repeated cancel,
  so a failed stop is retried rather than orphaning a run.
* If the child plan cannot be proposed, the assignment is cancelled and its reservation is
  released (`CHILD_RUN_FAILED`, 502): a failed delegation leaves nothing live.
* Workspace isolation comes from the engine creating one workspace per run id; a child run has
  its own id. No fixed workspace path is written by delegation.
* Not built yet (S1b-2): collecting child results into a parent synthesis, and a live-stack
  two-agent drill. Still inert unless `HARNESS_SUBAGENTS=1`.
