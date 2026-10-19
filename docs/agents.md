# Agent management and orchestration (master prompt part 45)

Code: registry `section1/src/harness_identity/agents.py` (`/api/v1/agents`), spawn and tracking
`section4/src/harness_orchestrator/agent_tree.py` (`/api/v1/orchestrator/runs/{runId}/agents/...`),
migration `section1/migrations/0044_agents.sql`. Tests: `section1/tests/test_agents.py`,
`section4/tests/unit/test_agent_tree.py`, `test_agent_tree_races.py`.

## What is NOT wired (read this first)
- **The external engine does not call the spawn endpoint yet.** Nothing in the DeepSeek engine,
  worker or SDK creates assignments, reports tool calls or reads pause/cancel state. These
  endpoints are a server-side registry, gate and ledger: a human or a future engine adapter calls
  them. Pausing or cancelling an assignment changes its row and audit trail, it does not signal a
  process. No agent process is started or stopped by this code.
- Scope (paths, tools, network hosts, credential names) is **recorded and subset-checked**, not
  enforced inside the sandbox. The engine and sandbox do not read `agent_assignments.scope` yet.
  Credential refs are names only; no secret value is stored or resolved here.
- Cost is whatever the caller reports (`costUsd`). Real model spend is not attributed to an
  assignment automatically, and the team budget gate is consulted once at spawn only.
- Not coordinated with the control lease of part 42: holding or losing control does not affect
  who may pause, cancel or reassign (any engineer or admin of the run's team can).
- Org-policy administration of `/api/v1/agents` routes is classified `administer` by the policy
  gate; engineers in an org team therefore cannot register agents even though the legacy check is
  admin-only anyway.

## Model
**Definition** (`agent_definitions`, one row per `(team, name, version)`): description,
capabilities, `toolPermissions`, a permission ceiling, allowed path prefixes, allowed network
hosts, credential ref names, model, optional fallback model, optional tool fallbacks, `maxDepth`,
`maxChildren`, `maxBudgetUsd`. State machine: `proposed -> approved -> retired`.
**Assignment** (`agent_assignments`): one spawned task in a run; `parent_id` builds the tree.
**Run pool** (`agent_run_pools`): per-run ceiling (agent count and budget) that parents the roots.
**Tool call** (`agent_tool_calls`): summary and outcome per invocation.
Money is integer micro-USD so compare-and-set arithmetic is exact.

## Directive by directive
**001 explicit agents, capabilities, tool permissions, versions.** Registry rows carry all of
them. `PUT /{id}` never edits in place: it saves version N+1 (state `proposed`); old versions stay
readable and spawnable while approved. Names are unique per team, case-insensitive (409
`NAME_TAKEN`). Agents may never hold `approve`, `administer`, `billing` or `export`: the ceiling is a
subset of `read, comment, steer` (422 otherwise). Closed schema: unknown body fields are 422.

**002 select or configure approved agents.** Admin registers and edits; a *different* admin
approves (403 `SELF_APPROVAL`; `HARNESS_AGENT_ALLOW_SELF_APPROVAL=1` lifts it for one-admin teams).
`POST /api/v1/agents/select {capabilities, tools, name?}` (admin or engineer) returns approved
agents only, newest approved version per name, whose capabilities and tools cover the request;
each call is audited as `agent.selected`. Spawn re-checks `approved` at call time (409
`AGENT_NOT_APPROVED`), and a foreign-team or unknown agent id is 404. Retiring a version stops new
spawns of it; live assignments continue until cancelled.

**003 when agents may spawn subagents, and limits.** Server-side in the spawn call, never trusted
from the client. Global caps (env, with hard maximums): `HARNESS_AGENT_MAX_DEPTH` (3, max 5),
`HARNESS_AGENT_MAX_CHILDREN` (5, max 10), `HARNESS_AGENT_RUN_MAX_AGENTS` (20),
`HARNESS_AGENT_RUN_BUDGET_USD` (5). The definition may only lower them. A root's `max_depth` is
`min(definition, global)` and children inherit it; a spawn at depth > max is 409
`SPAWN_DEPTH_EXCEEDED`. `maxChildren = 0` means "may not spawn" (409 `SPAWN_NOT_ALLOWED`); the limit
counts LIVE children (409 `SPAWN_CHILDREN_EXCEEDED`). Per run, `total_spawned` never decrements
(409 `SPAWN_RUN_LIMIT`) so cancel/respawn churn is bounded. Budget above the definition's max is
422 `BUDGET_EXCEEDS_AGENT_MAX`. Limits are compare-and-set UPDATEs on the parent row (or the pool),
so concurrent spawns cannot overshoot (race tests). The pool row is created lazily with the env
values current at the first spawn and not changed later.

**004 parent-child tracking, assignments, budgets.** Every assignment stores parent, depth,
delegator, title, limits and budget: `allocated`, `reservedForChildren`, `spent`, `remaining`. A
child's budget is reserved from its parent (a root's from the pool). When an assignment ends
(cancel, `finish`), its reservation returns to the parent and its spend rolls up. Spending past the
allocation (reported cost) pauses the assignment (`pauseReason: budget_exhausted`); resume is
refused (409 `AGENT_BUDGET_EXHAUSTED`) while over budget. Before every spawn `budget.check_budget`
must allow the team (409 `TEAM_BUDGET_EXHAUSTED`, fail-closed when the money store is degraded).
`requestId` makes spawn and tool-call reports idempotent (200 with `duplicate: true`).

**005 no delegation bypass.** The requested scope must be a subset of (a) the agent definition,
(b) the parent assignment's scope, and (c) the **delegating user's effective permissions** at the
run, computed with the pure policy engine: `decide_request` (org policy: deny rules, assignments,
emergency grants) when the team has an organization, `missing_for_grant` with the user's team role
otherwise. Failures: 403 `SCOPE_EXCEEDS_AGENT`, `SCOPE_EXCEEDS_PARENT`, `DELEGATION_DENIED`, each
audited as `agent.delegation_denied` with the exceeded dimensions. Reassign repeats the user check
for the user doing the reassigning. A viewer or guest cannot spawn at all (403 `ROLE_DENIED`).

**006 subtask ownership and concise progress.** `GET runs/{runId}/agents` (any role of the run's
team) returns the tree rows (agent name and version, parent, delegator, state, budget, model used,
progress percent and note, the last 3 tool-call summaries), a `summary` with counts by state and
one plain-text line per assignment, and the pool totals. Progress is reported through the
tool-call body (`progress: {percent, note}`), because the brief fixed the path list (see Gaps).

**007 scoped access.** Per assignment `scope = {permissions, tools, paths, network,
credentialRefs}`. Omitted means: permissions `["read"]`, everything else empty (deny by default).
Paths are relative prefixes matched on segment boundaries (`src` covers `src/a`, not `srcevil`),
no `..`, absolute or backslash paths. A tool report outside the scope is stored as outcome `denied`
with zero cost and answered 403 `TOOL_NOT_IN_SCOPE`. Enforcement inside the sandbox is NOT wired.

**008 tool summaries without hidden reasoning.** `POST .../tool-calls` accepts only `tool`,
`target`, `summary` (<= 500 chars), `outcome` (`ok|error|unavailable`), `costUsd`, `durationMs`,
`requestId`, `progress`, `finish`. The model forbids extra fields, so `reasoning`, `thoughts` and the
like are 422 and have no column to land in. Obvious secret shapes (`sk-...`, `ghp_...`, `Bearer ...`)
are redacted from summary and target. Each report is audited as `agent.tool_called` (tool and
outcome only). This cannot detect reasoning written inside a free-text summary.

**009 pause, cancel, reassign.** `pause` (active to paused) and `resume` cascade through live
descendants (children paused by a parent carry `parent_paused` and are resumed with it; a child
cannot be resumed under a paused parent). `cancel` works on active or paused assignments, cancels
the whole live subtree leaves first, and repeats until the root is terminal so a child spawned
mid-cancel cannot be left orphaned (409 `CONCURRENT_SPAWN` if it keeps happening). `finish`
(`completed` or `failed`, via the tool-call body) needs no live children (409 `CHILDREN_ACTIVE`).
`reassign {agentId}` moves the assignment to another approved agent whose ceiling still covers the
assignment's scope; limits never grow (`max_children = min(old, new)`), budget is unchanged.
Repeats are 200 `changed:false`; ended assignments are 409 `ILLEGAL_TRANSITION`. Every transition is
audited with before/after state. "Where technically supported": only the ledger changes, see above.

**010 fallback.** Model: if the definition's model is unavailable (operator list
`HARNESS_AGENT_UNAVAILABLE_MODELS`, or an open `engine_runtime` circuit breaker for
`deepseek-official|<model>`), spawn, resume and reassign use the fallback model the approved
definition declares, store `model_requested` vs `model_used` plus a reason, and audit
`agent.fallback_used`; with no usable fallback the call is 503 `AGENT_MODEL_UNAVAILABLE`. This is
not a forced downgrade (AGENTS.md law): only an admin-approved, explicit fallback is ever used, and
it is visible. Tool: a report with `outcome: unavailable` returns the definition's declared
`toolFallbacks` substitute only if it is in the assignment scope, records it and audits
`agent.fallback_used`; otherwise `fallback: null` and nothing is substituted. The server cannot
observe a tool outage itself, it relies on the report.

## Errors (stable codes)
`AGENT_NOT_APPROVED`, `PARENT_NOT_ACTIVE`, `SPAWN_NOT_ALLOWED`, `SPAWN_DEPTH_EXCEEDED`,
`SPAWN_CHILDREN_EXCEEDED`, `SPAWN_RUN_LIMIT`, `SPAWN_BUDGET_EXCEEDED`, `BUDGET_EXCEEDS_AGENT_MAX`,
`TEAM_BUDGET_EXHAUSTED`, `SCOPE_EXCEEDS_AGENT`, `SCOPE_EXCEEDS_PARENT`, `DELEGATION_DENIED`,
`AGENT_MODEL_UNAVAILABLE` (503), `TOOL_NOT_IN_SCOPE`, `ASSIGNMENT_NOT_ACTIVE`, `CHILDREN_ACTIVE`,
`AGENT_BUDGET_EXHAUSTED`, `ILLEGAL_TRANSITION`, `CONCURRENT_SPAWN`; registry: `NAME_TAKEN`,
`SELF_APPROVAL`, `ILLEGAL_STATE`, `VERSION_CONFLICT`. Foreign or unknown runs, agents and
assignments answer 404 `NOT_FOUND` with one envelope.

## Audit
`agent.registered|updated|approved|retired|selected` (section1, with before/after for updates and
transitions), `agent.spawned|spawn_refused|delegation_denied|paused|resumed|cancelled|reassigned|
fallback_used|tool_called` (section4, raw SQL). Refusals the user can hit are audited best-effort;
the spawn row is written in the same transaction as the reservation.

## Lead wiring needed
`app.include_router(agents.router)` in section1; in the orchestrator assembly
`configure_agent_tree(service)` and include `agent_tree.router`. `agent_tree` imports `api`
(for `get_principal`), so include it from the assembly, not from the top of `api.py`.
