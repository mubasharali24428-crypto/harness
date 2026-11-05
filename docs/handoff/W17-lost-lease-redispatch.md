# Handoff: W17 lost-lease re-dispatch (shipped — `76317e4`)

## Status
Shipped and on `master`. The W17 change set landed as `76317e4` ("W17 lost-lease re-dispatch:
attempt-keyed runner jobs, verified end to end"), an ancestor of `master` via PR #7; the
EXECUTION_STARTED follow-up is `b3b1b49`. Migration `0073_runner_job_attempts.sql` is present, the
attempt-scoped tests exist and pass (`section1/tests/test_runner_attempts.py`, 5 passed), and
section1 is green in CI.

This file previously said the work was blocked on W17's owner, with migration 0073 absent and the
attempt-scoped tests unwritten, and pointed at `stash@{0}`. None of that is true any more:
`stash@{0}` now holds an unrelated operator-local auth carve-out, and W17 does not block W31.

## What shipped
`section1/migrations/0073_runner_job_attempts.sql`
- `runner_jobs` primary key becomes `(run_id, attempt)`; attempt 1 is the original offer.
- Adds `evidence_from_seq`, the run's max event seq when the attempt was offered.
- At most one attempt per run may be live (offered/leased/running); a re-dispatch is allowed only
  when the previous attempt's claim is `orphaned`.
- The lease stays the single `run_claims` row: a re-dispatch puts it back to `queued` so the next
  attempt claims it through the normal queue path.

`section1/src/harness_identity/models.py`
- `RunnerJob` keyed by `(run_id, attempt)`; adds `attempt` and `evidence_from_seq`.

`section1/src/harness_identity/runner_api.py`
- `_job_public` includes `"attempt"`.
- `_own_job_or_404` scopes to the latest attempt.
- `_tool_events(db, run_id, after_seq=0)` filters `seq > after_seq`.
- `finish_job` settles from `_tool_events(db, job.run_id, job.evidence_from_seq)`, so the verdict
  cannot borrow the dead host's evidence.
- The re-offer path mints the next attempt and reports a live attempt as `JOB_TAKEN`.

## Tests
`section1/tests/test_runner_attempts.py` (5 passed locally):
- re-dispatch of an orphaned run mints attempt 2
- a live attempt blocks re-offer and is reported
- the re-dispatch verdict excludes the dead host's evidence
- a settled run is not re-offered
- claiming publishes EXECUTION_STARTED with the attempt

## What remains
W17 no longer blocks W31 (Scheduler experiment); start it from `docs/m2-continuation-plan.md` when
scheduled. Still open and unrelated to W17: the W7 real-window VS Code task, the W23
separate-machine fresh-setup run, the F2 provider check, and the external human gates (counsel
TERMS/PRIVACY, SLO/on-call adoption, dedicated sandbox worker certification, Hermes rights).
