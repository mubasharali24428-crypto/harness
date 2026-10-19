# Lane PL evidence: part 44 directives 001-003 (task statement, versioned plans, comments and proposed edits)

Files (all new, nothing else edited): `section1/migrations/0047_plans.sql`, `section4/src/harness_orchestrator/plans.py`,
`section4/tests/unit/test_plans.py` (29), `test_plans_edits.py` (24), `test_plans_support.py` (fixture), `docs/plans.md`, this file.

Commands (section4): `.venv/bin/python -m pytest -p no:cacheprovider -q tests/unit/test_plans*.py` -> 53 passed (~3 s, repeated 3x, stable);
`.venv/bin/ruff check src tests` -> all checks passed (never --fix); full `.venv/bin/python -m pytest -p no:cacheprovider -q` once ->
1196 passed, 37 skipped, 63 s. Sqlite runs the REAL migration 0047; the legacy `agent_plans` table is created with its 0001/0013/0023 columns.
No engine tasks are spawned; races use `asyncio.gather` over separate sessions and finish before `engine.dispose()`; the clock is `collab.db_now` patched.
Mutation check (script patches `plans.py`, reruns the lane tests, restores it): dropping the lock on create, the lock on comment, the self-approval rule,
the edit compare-and-set, `expectedVersion`, the stale-edit check, supersede-on-approval, or recording the approved digest each fail the tests. Dropping
`AND version = :seen` on the task UPDATE survives because the `(run_id, version)` primary key of `task_spec_revisions` already refuses the second writer
(`test_two_simultaneous_updates_of_one_version_make_exactly_one_new_version` passes either way); it is belt and braces, not a gap.

## Directives
001 task statement, success criteria, constraints, owner: `GET|PUT runs/{id}/task`, versioned (`task_specs.version`, one `task_spec_revisions` row per save,
`?version=n`), optimistic `expectedVersion`, no-op saves do not bump, owner must be a human member of the team, bounds, `task.updated` audit (ids and changed
field names only) and event (`test_task_*`, `test_saving_the_same_task_*`, `test_the_owner_must_be_*`, `test_two_simultaneous_*`).
002 plan as versioned reviewable structured content: immutable `plan_versions` with canonical-JSON sha256, listing, per-version read, structured diff
(`test_versions_are_numbered_digested_*`, `test_the_same_content_always_has_the_same_digest`, `test_diff_*`), bounded content (`test_plan_content_is_validated_*`,
`test_a_run_keeps_a_bounded_number_of_versions`), the legacy plan as version 1 followed lazily, mirrored once under concurrency (`test_the_legacy_plan_*`,
`test_two_simultaneous_first_reads_*`, `test_a_legacy_approval_locks_*`, `test_a_legacy_rejection_*`).
003 plan comments and proposed edits before approval: comments per version with optional field path, paged, idempotent, bounded (`test_reviewers_comment_*`,
`test_comment_rules`, `test_a_retried_comment_*`); structured-patch edits proposed with `comment`, decided by `approve`, accept creates a new version,
reject creates nothing, no self-accept, one decision under races, stale edits (`test_an_edit_is_proposed_decided_*`, `test_who_may_propose_*`,
`test_nobody_accepts_their_own_*`, `test_two_approvers_*`, `test_a_second_edit_on_the_same_base_*`, `test_a_bad_patch_*`, `test_ops_apply_in_order_*`);
everything refused after approval with `PLAN_LOCKED` (`test_everything_is_refused_once_the_plan_is_approved`).
Team scope and no oracle: every route answers the identical 404 for a foreign and an unknown run (`test_foreign_and_unknown_runs_*`, `test_the_other_teams_data_*`,
`test_an_edit_id_from_another_version_or_run_is_a_404`). Audit rows ids-only: `test_comments_are_audited_by_id_*`, `test_edit_audit_rows_carry_ids_*`.

## Honest gaps
* No route approves a *version*. Approval is still the legacy `plan/approve` (planId), so only the mirrored legacy plan (and nothing created natively or by an
  accepted edit) can be approved by the existing routes. The lock/digest binding works on whatever the legacy approve decides. A native version cannot become
  `approved` and cannot reach the engine, which executes the in-memory legacy plan. Closing this needs a lead decision outside the PL paths: either a
  version-approval route plus a way to make the engine run `plan_versions.content`, or have accepted/created versions re-propose through `plan_gate`.
* The planner/engine do not read the task spec or plan versions. Nothing yet turns "legacy plan re-proposed" into a version except the lazy mirror on read, so a
  mirror only appears when someone reads or writes through these routes (state following is also lazy, no push).
* Lazy mirror and status following write no audit row (derived from the legacy decision).
* The `plan.version_created` etc. events use new `run_collab_events.kind` strings; if the realtime layer or the UI whitelist event kinds, the lead must add
  `task.updated`, `plan.version_created`, `plan.comment_added`, `plan.edit_proposed`, `plan.edit_decided`.
* Untested on PostgreSQL: the migration (portable SQL, JSON as TEXT, partial unique indexes), JSONB columns of `agent_plans` returning decoded values (handled by
  `_jl`, both forms), concurrent mirroring under real row locks. No advisory locks are used (one `pg_advisory_xact_lock` per event insert comes from `collab._event`).
* Diff is structural at item level, not word level. Comments cannot be edited, deleted or moderated (immutable by design here); no mentions or notifications.

## What the lead must wire
1. `from harness_orchestrator import plans` in `app.py`: `app.include_router(plans.router)` and `plans.configure_plans(service)` next to `configure_discussion`.
2. Policy gate: already classified (steer fallback for task PUT and POST versions, `comment` for POST comments/edits, `approve` for decide). Check GET task and
   versions fall under the `read` rule (they do: `GET /api/v1/orchestrator/.*`).
3. Audit names `task.updated`, `plan.version_created`, `plan.comment_added`, `plan.edit_proposed`, `plan.edit_decided` registered in section1 `audit.py`; `authz.denied` already is.
4. `openapi.json` / sdk regeneration and the spec-drift lists (`test_spec_drift`) when the router is included; UI lane X consumes the `can` flags and `stale` on edits.
5. Migration 0047 in the runner order (after 0046); it needs `teams`, `agent_runs` (FK targets) and writes only new tables.
