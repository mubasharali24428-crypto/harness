# Acceptance map: core flow (part 27), session 2

Directives are the 10 items of `## 27. ACCEPTANCE: CORE FLOW` in
`docs/master-prompts/harness-multiplayer-master-build-prompt.md`.
Unit results were produced by lane D on 2026-10-18 (about 16:15 UTC) with the section4 venv, no Docker:
`cd "/Users/user/untitled folder 2/harness/section4" && ./.venv/bin/python -m pytest <ids> -q -p no:cacheprovider`.
Other lanes were editing concurrently; the lead re-runs after integration.
Browser/in-flight column filled by the lead on 2026-10-18 after integration; legs attributed to `lane-c.md`/`lane-b.md` were run by those lanes, not re-run in this table.

Paths below are relative to `section4/` unless they start with `section7/` or `section1/`.
`CB` = `tests/unit/test_collab_backend.py`, `ISO` = `tests/unit/test_isolation_and_staging.py`,
`SE` = `tests/unit/test_staging_e2e.py`.

| # | Directive | Covering tests | Fresh unit result (command: ids above, counts) | Browser / in-flight leg |
|---|---|---|---|---|
| 1 | A team member can create or join the selected shared run | `CB::test_single_leader_and_roster`, `CB::test_memory_only_run_is_persisted_before_join_or_409`; browser: `section7/e2e/multiplayer.e2e.ts` ("create → join → ...") | 2 passed | `run_smoke.py --otp` on the integrated tree: 1 passed (multiplayer.e2e.ts, 2026-10-18) (create→join step) |
| 2 | Authorized teammates see one consistent plan and run state | `CB::test_single_leader_and_roster`, `CB::test_guidance_is_recorded_with_actor_and_idempotent` (viewer reads the same history), `tests/unit/test_run_get.py` (run aggregate) | 14 passed | `run_smoke.py --otp` on the integrated tree: 1 passed (multiplayer.e2e.ts, 2026-10-18) (two-user view); refresh/reconnect leg per lane C evidence `lane-c.md` |
| 3 | Unauthorized users cannot view or control another team's run | `CB::test_foreign_team_gets_404_everywhere`, `tests/unit/test_collab_denial_audit.py` (all 15 routes, audited), `tests/unit/test_authz_failclosed.py`, `tests/unit/test_pilot_flag_rollback.py::test_rollback_does_not_open_runs_to_other_teams` | 87 passed (whole files) | none (API-level; no browser leg defined) |
| 4 | Every redirect is associated with an actor and the correct run | `CB::test_guidance_is_recorded_with_actor_and_idempotent`, `CB::test_observer_and_non_participant_cannot_redirect`, `CB::test_scope_change_needs_reason_and_leader`, `CB::test_approval_is_scoped_to_its_run` | 4 passed | `run_smoke.py --otp` on the integrated tree: 1 passed (multiplayer.e2e.ts, 2026-10-18) (redirect step); before/after in audit: `test_collab_rules_s2.py::test_approval_lifecycle_records_before_after_and_emits_events` (section4 833 passed) |
| 5 | High-impact changes stay blocked until the session leader approves | `CB::test_approval_flow_blocks_until_leader_approves_exact_digest`, `CB::test_engine_blocked_by_pending_approval_and_receives_guidance_once`, `CB::test_leader_cannot_approve_own_request_unless_policy_allows`, `CB::test_concurrent_decisions_have_one_winner`, `SE::test_high_impact_delete_is_withheld_then_applied_after_leader_approval` | 5 passed | `run_smoke.py --otp` on the integrated tree: 1 passed (multiplayer.e2e.ts, 2026-10-18) (approve/apply step) |
| 6 | Approval applies only to the exact reviewed proposal | `CB::test_amending_an_approved_proposal_voids_the_approval`, `CB::test_scope_change_invalidates_open_and_approved_requests`, `CB::test_expired_approval_cannot_be_decided`, `tests/unit/test_staging_digest_binding.py`, `ISO::test_apply_requires_exact_digest_and_lands_change`, `ISO::test_tampered_staged_copy_or_wrong_paths_refused` | 9 passed | `run_smoke.py --otp` on the integrated tree: 1 passed (multiplayer.e2e.ts, 2026-10-18); changed-proposal Playwright leg per `lane-c.md` |
| 7 | A handoff is explicit and visible to the participants | `CB::test_handoff_ownership_transfers_only_on_accept`, `CB::test_handoff_rejection_keeps_leader_and_bad_recipient_refused`, `CB::test_handoff_accepts_identity_orm_role_storage`, `CB::test_admin_takes_over_absent_leader` | 4 passed | `run_smoke.py --otp` on the integrated tree: 1 passed (multiplayer.e2e.ts, 2026-10-18) (handoff step); handoff UI vitest per `lane-c.md` |
| 8 | Stop prevents further action or honestly reports incomplete cancellation | `SE::test_stop_kills_worker_confirms_and_reports_stopped`, `ISO::test_cancel_kills_worker_and_its_children`, `ISO::test_cancel_is_sticky_until_reset`, `tests/unit/test_s1_r05_lifecycle_audit.py::test_stop_route_writes_run_stopped_audit_row`, `::test_stop_without_confirmation_records_no_cancel_confirmed`, `tests/unit/test_run_isolation_and_stop_truth.py::test_second_stop_on_finished_run_is_a_noop` | 6 passed | `run_smoke.py --otp` on the integrated tree: 1 passed (multiplayer.e2e.ts, 2026-10-18) (stop step); lifecycle races/recovery: `test_lifecycle_races.py`, `test_lifecycle_recovery.py` in the section4 run (833 passed, 32 skipped) |
| 9 | Run outputs stay isolated from other concurrent runs | `tests/unit/test_run_isolation_and_stop_truth.py` (whole file), `ISO::test_two_concurrent_runs_cannot_touch_each_other`, `ISO::test_seatbelt_isolates_concurrent_runs`, `ISO::test_worker_env_is_allowlisted`, `CB::test_approval_is_scoped_to_its_run` | 19 passed | lane B isolation fixes: section5 230 passed, 1 skipped (`lane-b.md`); cross-run Playwright leg per `lane-c.md` |
| 10 | Collaborators can review the final artifact and its action history | `CB::test_guidance_is_recorded_with_actor_and_idempotent` and `CB::test_handoff_ownership_transfers_only_on_accept` (history lists events), `SE::test_high_impact_delete_is_withheld_then_applied_after_leader_approval` (change lands after approval), `tests/unit/test_collab_denial_audit.py::test_collab_denial_audit` (audit row per refusal) | 31 passed | `run_smoke.py --otp` on the integrated tree: 1 passed (multiplayer.e2e.ts, 2026-10-18) (final artifact and history views) |

## Commands as run (counts are `N passed`, 0 failed in every row)

```
row 1  CB::test_single_leader_and_roster CB::test_memory_only_run_is_persisted_before_join_or_409        -> 2 passed
row 2  CB::test_single_leader_and_roster CB::test_guidance_is_recorded_with_actor_and_idempotent tests/unit/test_run_get.py -> 14 passed
row 3  CB::test_foreign_team_gets_404_everywhere tests/unit/test_collab_denial_audit.py tests/unit/test_authz_failclosed.py tests/unit/test_pilot_flag_rollback.py -> 87 passed
row 4  four CB ids from the table                                                                       -> 4 passed
row 5  five ids from the table                                                                          -> 5 passed
row 6  six ids/files from the table                                                                     -> 9 passed
row 7  four CB ids from the table                                                                       -> 4 passed
row 8  six ids from the table                                                                           -> 6 passed
row 9  test_run_isolation_and_stop_truth.py + four ids from the table                                   -> 19 passed
row 10 four ids from the table                                                                          -> 31 passed
```

Rows overlap on shared tests; the counts are per command, not additive.

## Notes for the lead

- Row 8 and 9 unit tests use real worker processes and sandbox profiles on this Mac
  (no Docker). `ISO::test_seatbelt_isolates_concurrent_runs` is macOS-specific.
- Collaboration flag semantics that rows 3 and 10 depend on: `HARNESS_PILOT_TEAMS`
  unset/empty is open to all teams; see `docs/runbooks/pilot.md` section 5 and
  `tests/unit/test_pilot_flag_rollback.py`.
- Nothing here claims the browser proof passed. `section7/e2e/multiplayer.e2e.ts`
  was not run by lane D.
