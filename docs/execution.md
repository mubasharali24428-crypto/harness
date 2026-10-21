# Execution tracking (part 44.004–010)

Session 6 adds durable subtasks, execution limits, completion review, abandonment, and safe retry
records. The API lives at `/api/v1/orchestrator/runs/{runId}/...`, uses the authenticated principal,
and scopes reads and writes to the caller's team. Unknown and foreign runs share the same 404
response. Schema: `section1/migrations/0048_execution.sql`; implementation:
`section4/src/harness_orchestrator/execution.py`.

## Subtasks and evidence

`GET|POST /subtasks`, `PATCH /subtasks/{id}`, and the `block`, `unblock`, `complete`, and `evidence`
actions track dependencies, blockers, state, and evidence references. Dependency cycles are refused.
Completion needs evidence and completed dependencies. Edits use `expectedVersion`; repeated creates
and evidence references are idempotent. Evidence is a reference and note, not a copy of file bytes.

## Execution limits

`PUT /execution-limits` sets optional limits using `expectedVersion`; `GET /execution-status`
reports usage, enforcement points, checkpoints, and a safe-resume assessment. Seconds, completed
model-call ledger rows, completed-run cost and durable `agent_tool_calls` rows are checked at run start
and queue claim. A sweep (`run_limit_sweeper`, started by `deploy/runners/services.py` every
`HARNESS_LIMIT_SWEEP_SECONDS`, default 30; 0 turns it off) pauses an already-running run that exceeds
those limits: the pause releases the claim and the owning worker's fence loop stops the run, so any
replica may sweep. The tool-call cap counts `agent_tool_calls` rows from two writers: the agent
tool-call report route (`POST /agents/{assignmentId}/tool-calls`) and the engine itself. When an
engine call returns, `run_metering.record_engine_tool_calls` writes one row per runtime tool call
(`reported_by = 'engine'`, `assignment_id` = the run id, the call id dedupes a replay). Inside a live
run the engine's shell plugin also enforces the cap: it is handed the remaining budget
(`execution.remaining_tool_calls`) and denies every call past it. A run whose engine call times out
or crashes returns no event list, so its calls are not recorded. Spend during an in-flight engine call is not visible until that call
completes, so a run can overshoot a cost limit by one call.

The safe-resume response uses durable agent events, checkpoint history, run claims, current
execution state, approvals, and workspace/session gates. The engine resumes its existing session
history under the same run id. It cannot undo side effects from a step that was interrupted; review
workspace checkpoints before retrying.

## Completion, abandon, retry

`POST /completion/report` records an agent report and output references. It does not mark the work
accepted. An `approve` holder other than the reporting agent must accept or reject the report digest.
Abandon and fail require a reason, preserve the event log and evidence, and use the existing state
machine vocabulary. Retry uses a caller `requestId`, records an attempt number, and refuses while a
claim/task/retry is active, while side effects remain unreconciled, or when the run is not safely
resumable. Retry resumes the same engine session; it does not create a fresh isolated execution.

## Boundaries

The engine can enforce limits only where its current lifecycle exposes a check. In-flight spend
stays unmeasured until a call completes, and the sweep is a polling loop (up to one interval late,
and it does not run while the orchestrator is down; the claim and start checks still do).
Checkpoint content is owned by Section 5; this API reports references and safe-resume reasoning, it
does not copy or restore checkpoint files. A plan version is approved with
`POST .../plan/versions/{n}/approve`, which binds the approved content to what the engine runs
(`docs/plans.md` section 4a).

Tests: `section4/tests/unit/test_execution.py` and `test_execution_runs.py`; opt-in PostgreSQL
probe: `test_session6_live_pg.py` (not run in the current environment).
