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
model-call ledger rows, and completed-run cost are checked at run start and queue claim. A periodic
worker sweep can pause an already-running run that exceeds those limits. Tool-call limits are
recorded only because the engine does not report tool-call counts. Spend during an in-flight engine
call is not visible until that call completes. The sweep function exists, but the deployment has no
scheduler wired to call it yet.

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

The engine can enforce limits only where its current lifecycle exposes a check. Tool calls and
in-flight spend remain unmeasured. A worker sweep is not scheduled in deploy. Checkpoint content is
owned by Section 5; this API reports references and safe-resume reasoning, it does not copy or
restore checkpoint files. Native plan versions are not yet approvable or executable: the legacy
plan approval route still controls the engine. See `docs/plans.md` and
`docs/briefs/evidence/s6/lane-pl.md`; part 44 remains partial until that path is closed.

Tests: `section4/tests/unit/test_execution.py` and `test_execution_runs.py`; opt-in PostgreSQL
probe: `test_session6_live_pg.py` (not run in the current environment).
