# Part 05 known-gap checklist: pass/fail (session 2)

Paths relative to `section4/` unless noted. `CB` = `tests/unit/test_collab_backend.py`, `R2` = `tests/unit/test_collab_rules_s2.py`,
`LT` = `tests/unit/test_lifecycle_races.py`. Results come from the suites recorded in `lane-a.md` (section4 833 passed, 32 skipped).

| # | Check | Result | Evidence |
|---|---|---|---|
| 001 | Explicit session leader | PASS | `CB::test_single_leader_and_roster`, `R2::test_two_simultaneous_leader_claims_yield_exactly_one_leader`, `R2::test_leader_changes_record_before_and_after` |
| 002 | Participants and presence visible | PARTIAL | roster visible to team roles (`CB::test_single_leader_and_roster`); live presence/typing indicators are not built (Session 4) |
| 003 | General redirect/nudge exists | PASS | `CB::test_guidance_is_recorded_with_actor_and_idempotent`, `R2::test_simultaneous_guidance_is_all_kept_in_seq_order_and_delivered_once` |
| 004 | Redirect has actor, timestamp, run, changed intent | PASS | same guidance test; `R2::test_forged_actor_fields_in_bodies_are_ignored`; `R2::test_approval_lifecycle_records_before_after_and_emits_events` |
| 005 | Scope/permission change needs a reason | PASS | `R2::test_scope_and_permission_changes_need_a_reason`, `CB::test_scope_change_needs_reason_and_leader` |
| 006 | Handoff has explicit ownership semantics | PASS | `CB::test_handoff_ownership_transfers_only_on_accept`, `CB::test_handoff_rejection_keeps_leader_and_bad_recipient_refused` |
| 007 | Per-action approval gate for high-impact file actions | PASS | `CB::test_approval_flow_blocks_until_leader_approves_exact_digest`, `tests/unit/test_staging_e2e.py::test_high_impact_delete_is_withheld_then_applied_after_leader_approval` |
| 008 | Changed proposal invalidates prior approval | PASS | `CB::test_amending_an_approved_proposal_voids_the_approval`, `R2::test_amend_and_scope_invalidation_record_what_changed`, `R2::test_scope_change_racing_an_approval_decision_never_leaves_it_usable` |
| 009 | Audit identifies requester, approver, outcome | PASS | `R2::test_approval_lifecycle_records_before_after_and_emits_events`; hash chain: `section1/tests/test_audit_chain_pg.py` (live Postgres). Not audited: pause/resume requests (see `docs/audit-and-events.md` section 8) |
| 010 | Pause and stop reliably terminate execution | PASS with limit | `tests/unit/test_lifecycle_races.py` (10 race/idempotence tests), `test_lifecycle_recovery.py`; a provider-side model call already in flight cannot be recalled, only ignored (`docs/lifecycle.md`) |

9 PASS, 1 PARTIAL (002), 0 FAIL.
