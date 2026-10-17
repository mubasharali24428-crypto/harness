# A2-S4 — Task decomposition and independent specialist agents

Baseline `31bf4077`, worktree `harness-a2` (uncommitted). Verification: packages/agent-runtime: 10 passed; ruff clean.

## Features

### F030 — Task decomposition planner
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Validated DAG (cycle/dep/id/size checks), deterministic keyword decomposition. Not yet fed by PlanService.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/graph.py`
- `packages/agent-runtime/tests/test_runtime.py::test_graph_layers_and_validation`
- `packages/agent-runtime/tests/test_runtime.py::test_decompose_routes_roles_and_orders`

### F071 — Specialized subagents
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Team-scoped user-defined specialists; no API/persistence.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/specialists.py`
- `packages/agent-runtime/tests/test_runtime.py::test_specialist_model_and_tool_policy`

### F072 — Agent-to-agent delegation
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Non-escalating tool scope, conserved budget, depth/fan-out caps, cycle refusal.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/delegation.py`
- `packages/agent-runtime/tests/test_runtime.py::test_delegation_contracts`

### F073 — Shared task board
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Team-scoped cards, transition table, version CAS, no foreign-card oracle. In-memory; no table/route.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/board.py`
- `packages/agent-runtime/tests/test_runtime.py::test_board_transitions_cas_scope_and_escalation`

### F074 — Agent team or swarm mode
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Layers run concurrently, failures skip dependents and escalate. Executor is injected; no live-model swarm run.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/swarm.py`
- `packages/agent-runtime/tests/test_runtime.py::test_swarm_runs_parallel_layers_isolated_and_skips_on_failure`

### F075 — Independent context windows per subagent
**State:** IMPLEMENTED (library); NEEDS INTEGRATION

Windows seeded only from contract-named keys; token budget eviction. Token count is a len/4 estimate.

Evidence:
- `packages/agent-runtime/src/harness_agent_runtime/context.py`
- `packages/agent-runtime/tests/test_runtime.py::test_context_isolation_and_eviction`

## Limits
- API routes, persistence tables (A1 migrations) and wiring to PlanService/S5 are not done; package is standalone and in-memory
