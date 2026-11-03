# Handoff: W17 lost-lease re-dispatch (blocked — owner action required)

## Status
Blocked on W17's owner. Changes are stashed/uncommitted on `w1-engine-tools` (stash@{0}). Do not touch W17's files until the owner commits or hands them over (AGENTS.md rule: frozen contracts, per-file ownership).

## What exists (stash@{0})
Two files modified:

`section1/src/harness_identity/models.py`
- `RunnerJob.run_id` + `RunnerJob.attempt` now composite primary key (was `run_id` alone)
- New column `attempt: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)`
- New column `evidence_from_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)`
- Docstring: keyed by `(run_id, attempt)` since 0073; orphaned runs never auto-re-queue; one live attempt per run

`section1/src/harness_identity/runner_api.py`
- `_job_public` now includes `"attempt": row.attempt`
- `_own_job_or_404` scopes to latest attempt: `order_by(RunnerJob.attempt.desc()).limit(1)`
- `_tool_events(db, run_id, after_seq=0)` — optional `seq > after_seq` filter
- `finish_job` calls `_tool_events(db, job.run_id, job.evidence_from_seq)` so the settling verdict derives from evidence after this attempt's mark only

## What is missing (owner to do)
1. **Migration 0073** — highest existing migration is `0072_index_admission.sql`. Per AGENTS.md: schema changes = new numbered migration in `section1/migrations/`, never edit 0001. The 0073 migration must add `attempt` to the `runner_jobs` PK and add `evidence_from_seq` column.
2. **Commit the stashed model/runner_api changes** after 0073 lands.
3. **Write tests** — the stash message says "assign_job re-offer + tests NOT written, untested". Tests needed for attempt-scoped job lookup, re-offer of orphaned claims, and `evidence_from_seq` verdict scoping.
4. **Run section1 suite** — `cd section1 && ./.venv/bin/python -m pytest tests/ -q` (must pass before any PR).

## Context
Part of Marathon 2.0 W17 (trust/confinement). W22 (stop/recovery) is complete and committed (`0797515`). Per `docs/m2-continuation-plan.md` "immediate next work": complete W17 gates before starting W31 (Scheduler experiment). W23 separate-machine fresh-setup run and F2 provider check also remain open.

## Suggested commit message
W17: lost-lease re-dispatch — attempt-keyed runner jobs + evidence_from_seq

Add `attempt` to runner_jobs primary key and `evidence_from_seq` column so a
dead host's partial failure cannot poison a re-dispatch and its passes cannot
be borrowed. Scope `_own_job_or_404` to the latest attempt and filter tool
events by `evidence_from_seq` in `finish_job`. Includes migration 0073 and
attempt-scoped tests.
