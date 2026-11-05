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
* The live two-agent drill passed on 2026-11-05 (`evidence/w28/two-agent-drill.json`). Delegation returns
  `childPlanId` so a human can approve a child.
* S1b-2 (2026-11-05): `GET /runs/{runId}/synthesis` is a read-only review view: per delegated child its
  owner, scope, state, spend, run status and final response, plus `conflicts` where live sibling path
  scopes overlap. `reviewRequired` is always true and `autoMerge` false; nothing is merged. A cancelled
  child cannot conflict. Overlap is checked on declared scope only, not on files actually written.
  Still inert unless `HARNESS_SUBAGENTS=1` (the endpoint itself is a read and is not gated).
* Scope binding (2026-11-05): the child run's intent v1 is written from the assignment's `paths`, and a
  delegation whose scope has no paths is refused (`SCOPE_UNBINDABLE`), because an intent without paths is
  unconfined. Consequence found live: a child whose scope paths do not exist in its workspace gets a
  fully read-only workspace (fail closed), so delegation assumes the project workspace already holds the
  paths. Only `paths` is bound; `tools`, `permissions` and `network` are recorded but not enforced.
