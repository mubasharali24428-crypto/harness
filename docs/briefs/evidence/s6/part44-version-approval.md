# Part 44 close-out: version approval, scheduled limit sweep (commit 00a1412)

Closes the two gaps `lane-pl.md` and `lane-ex.md` left open. Lane evidence for 44.001-004 and 44.006-010 still stands; this file covers
44.002/003 (approving a reviewed version), 44.005 (limits) and the checks that were re-run.

## What changed

* `POST /runs/{id}/plan/versions/{n}/approve` (`approve` capability, classified in `policy_enforce.py`). It calls `service.adopt_plan_version`, which
  registers the reviewed version as the run's open legacy plan (new `plan-<hex>` id, `agent_plans` row, `plan_proposed` event, previous open plan
  marked `rejected`), then decides it through the ordinary CAS-first `approve_plan`. The engine therefore runs exactly the approved content.
  A second approve of the same version replays; an approved run answers `PLAN_LOCKED`; a run past planning refuses adoption.
* `execution.run_limit_sweeper(service, interval)`, started by `deploy/runners/services.py` (`HARNESS_LIMIT_SWEEP_SECONDS`, default 30, 0 = off).
  Any replica may sweep: pausing releases the claim and the owning worker's fence loop stops the run.
* `usage_of` reads `agent_tool_calls` inside a SAVEPOINT so a missing table cannot roll back the caller's transaction.
* Workbench shows "Approve version n" only when `can.approve`. Spec, python SDK and ts SDK regenerated; `spec_drift.py` clean.

## Directive map (new tests)

| Directive | Test |
|---|---|
| 44.002/003 version becomes executable | `test_a_native_version_is_approved_and_is_exactly_what_the_engine_runs`, `test_an_accepted_edit_runs_the_edited_plan_not_the_original`, `test_the_approved_edited_plan_survives_a_restart` |
| fail-closed on unenforceable plan content | `test_the_engine_gate_sees_the_approved_versions_forbidden_paths` |
| one decision under races, idempotent, audited once | `test_approving_twice_is_idempotent_and_audited_once`, `test_two_approvers_at_once_make_one_decision` |
| legacy and native plans cannot both be approved | `test_the_legacy_plan_replaced_by_an_adopted_version_can_no_longer_be_approved`, `test_a_version_mirrored_from_the_legacy_plan_uses_that_plan` |
| roles, team scope, lock | `test_roles_and_scope_are_enforced`, `test_an_approved_plan_locks_further_versions_and_cannot_be_adopted_over` |
| policy gate classifies the route | `test_the_new_route_is_classified_by_the_policy_gate` |
| 44.005 scheduled sweep | `test_the_scheduled_sweep_pauses_a_running_run_over_its_limit`, `test_a_failing_tick_is_logged_and_the_next_one_runs`, `test_cancelling_the_sweeper_ends_it_without_an_error` |

## Verification run (this machine, SQLite)

* `section4`: `pytest -q -p no:cacheprovider` -> 1464 passed, 38 skipped (85 s). `ruff check .` clean.
* `section7`: `npx tsc --noEmit` clean; `npx vitest run` -> 68 files, 817 tests passed.
* `scripts/spec_drift.py` -> "orchestrator specs and UI calls match the router."
* After the last docstring edit: `test_execution.py`, `test_execution_runs.py`, `test_limit_sweeper.py`, `test_plan_version_approval.py` -> 81 passed.

## Honest limits

* **Tool-call budget (44.005) counts reported calls only.** The only writer of `agent_tool_calls` is `POST /runs/{id}/agents/{assignmentId}/tool-calls`.
  Nothing on the engine path writes it (grep of section4, section5 and deploy shows no other writer), so tool calls made inside the engine are not
  seen by the orchestrator. The cap, the run-start/claim checks and the sweep are tested against reported rows; coverage of engine-internal calls
  needs the Section 5 mediated executor to report them, which is not built.
* Model spend inside an in-flight engine call is invisible until the call completes, so a run can overshoot a cost limit by one call.
* The engine executes a version only through the legacy `AgentPlan` fields (goal, files, steps, tests, risks, cost, forbiddenPaths, expectedResult).
  Plans that declare `forbiddenPaths` are refused by the runtime until the Section 5 mediated executor enforces them.
* NOT run: live PostgreSQL (`test_session6_live_pg.py`, Docker down), a cross-replica race for version approval, the smoke (`run_smoke.py`),
  `make gate`, section2 and section5 suites in this pass.
