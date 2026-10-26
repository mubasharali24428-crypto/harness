# Week 2 plan — context and recovery

Follows [Week 1](week-1-plan.md) and is governed by
[the marathon execution plan](marathon-execution-plan.md), which sets the rule
this week inherits: *"An unchecked gate stays open when a dependent week begins."*

## Inherited open, not silently deferred

- **M01/M03 — approved-scope enforcement.** W1's spike measured it and the answer
  was no: writes inside the workspace but outside the approved scope succeed,
  because `python3` is the only file-writing binary the policy leaves and
  `scope_violation` skips code strings. M03 needs mount-level enforcement, not a
  better parser. W2 does not depend on it, and nothing in this week widens write
  access while it stays open.
- **M07 — core live acceptance.** Blocked on topology: the orchestrator container
  has no Docker socket, so the confined worker that the stack is supposed to run
  cannot be created there. Needs a decision (worker daemon vs socket mount vs
  shipping `off`), not code.

## Packages

| ID | Deliverable | Completion evidence | State |
|---|---|---|---|
| N01 | A looping or burning run is caught from the event stream and stopped with a durable reason, not left to the wall clock | `tests/unit/test_loop_guard.py`, `tests/unit/test_loop_guard_wiring.py`; mutation-checked (wiring disabled → 3 of 4 wiring tests fail) | **done** |
| N02 | Baseline repo context reaches the prompt (tree, README, git status, dependency manifests) | a run in a large fixture repo finds the right files without being told | open |
| N03 | `project_rules` reaches the executing agent, not only the planner | rules text present in the engine call on both execution paths | open |
| N04 | Retry-with-changes as a visible affordance | the UI offers it and the audit records it; no silent model ladder | open |

## N01 — what was actually wrong

`maxToolCalls` existed, and `remaining_tool_calls` computed it honestly. The
defect was *when*: the limit was a snapshot taken **before** the run, from
`COUNT(*) FROM agent_tool_calls`, and that table is populated by
`record_engine_tool_calls` only **after** the engine call returns. During the run
the count is always zero. Native `tool-fs` calls are not bash calls, so the shell
plugin's own counter never saw them either. Measured consequence: two acceptance
attempts made 14 and 37 tool calls against a one-sentence goal and were stopped by
nothing but `ENGINE_RUN_TIMEOUT_SECONDS`.

The guard therefore sits on the W1 transcript forwarder — the one place every
call passes on both execution paths — and it is pure and DB-free, so the answer
cannot depend on a table arriving late. Two independent stops: `loop_detected`
(same tool, same normalised arguments, four times) and `tool_budget_exhausted`
(more calls than allowed, counting distinct calls rather than distinct shapes).

Three things worth stating plainly:

1. **The worker is not killed.** There is no cancel channel into the SDK session;
   that is W3's spike. So a stopped run keeps burning until it ends by itself.
   What N01 guarantees is that the stop is durable and visible immediately, that
   the shell budget is written to zero so further bash calls refuse, and that a
   late `completed` cannot overwrite the failure.
2. **The post-run drain counts too.** `finally` used to emit records without
   consulting the guard; a loop starting in the last second would have been
   invisible. Both paths now go through one consumer.
3. **`tool_budget_exhausted` is untested at the service seam** — the fixture has
   no `run_limits` row, so the budget arrives as `None` and only the repeat guard
   can fire there. The arithmetic is covered in `test_loop_guard.py`.

## Gate

A deliberately looping fixture is caught and stopped with a message, not a budget
burn; and a representative task in a 500-file repo finds the relevant files
without being told. The first half is met. The second needs N02.
