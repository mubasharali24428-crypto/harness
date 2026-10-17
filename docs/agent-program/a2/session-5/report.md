# A2-S5 — Per-agent policies, synthesis and fair scheduling

Baseline `31bf4077`, worktree `harness-a2` (uncommitted). Verification: packages/agent-runtime: 10 passed; ruff clean; section4 598/7 skipped.

## Features

### F076 — Per-subagent model selection
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Explicit model per specialist validated against the team's allowed set; never substituted. Allowed set must be fed from A1 model policy.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/specialists.py`
- `packages/agent-runtime/tests/test_runtime.py::test_specialist_model_and_tool_policy`

### F077 — Per-subagent tool permissions
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Default-deny allowlist checked against a tool catalog. Not yet the S5 execution authority.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/specialists.py (authorize_tool)`
- `packages/agent-runtime/tests/test_runtime.py::test_specialist_model_and_tool_policy`

### F078 — Subagent result synthesis
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Conflicts and unsupported claims surfaced, needs_human flag; no winner is picked.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/synthesis.py`
- `packages/agent-runtime/tests/test_runtime.py::test_synthesis_surfaces_conflicts_not_winners`

### F079 — Human assignment and escalation queue
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Assign to user:<id>, idempotent escalation, per-team human queue, resolve.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/board.py`
- `packages/agent-runtime/tests/test_runtime.py::test_board_transitions_cas_scope_and_escalation`

### F080 — Agent workload, queue, and concurrency controls
**State:** VERIFIED (preserved) + library fairness

Existing guarantees untouched. Added team-fair scheduler with quotas/weights; not yet replacing run_queue ordering.

Evidence:
- `section4 EngineRuntime/run_queue unchanged and green (598 pass)`
- `packages/agent-runtime/src/harness_agent_runtime/scheduler.py`
- `packages/agent-runtime/tests/test_runtime.py::test_scheduler_fair_no_starvation_and_quotas`
- `packages/agent-runtime/tests/test_runtime.py::test_scheduler_weights`

### F024 — Parallel agent jobs
**State:** VERIFIED (preserved); credential/workspace caveat CLOSED on live path

Concurrent runs now isolated by process; thread path remains for stubs/dry.

Evidence:
- `section4/tests/unit/test_process_engine.py::test_concurrent_runs_get_distinct_keys_and_workspaces`

## Limits
- API routes, persistence tables (A1 migrations) and wiring to PlanService/S5 are not done; package is standalone and in-memory
