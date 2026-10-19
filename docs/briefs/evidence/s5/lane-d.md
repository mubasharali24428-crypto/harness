# Lane D evidence: part 43 collaborative discussion

Files: `section1/migrations/0045_discussion.sql` (new); `section4/src/harness_orchestrator/discussion.py` (new);
`section4/tests/unit/test_discussion.py` (28), `test_discussion_flows.py` (25), `test_discussion_support.py` (fixture, no tests);
`docs/discussion.md`. No existing file was edited (`collab.py`, `control.py`, `api.py`, `notify.py` are only called).

Commands run (section4):
* `./.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_discussion.py tests/unit/test_discussion_flows.py` -> 53 passed (~7 s, repeated).
* `./.venv/bin/python -m pytest -q -p no:cacheprovider` (full, once at the end) -> 1085 passed, 35 skipped, ~50 s.
* `./.venv/bin/ruff check src tests` -> my files clean; 6 E501 findings remain in lane N's `test_notify.py` and
  `test_notify_sources.py` (not mine, not touched). Never `--fix`.
* Mutation check (a script that patches `discussion.py` and reruns the lane tests, then restores it): dropping the edit window,
  accepting all mentions, skipping the promote version check, dropping session membership in search, keeping revisions
  on a non-sensitive delete, dropping the session membership guard, dropping the retention-at-read rule, allowing
  reactions on hidden comments, dropping the team filter in export, and dropping the promote request id each fail the tests.
  Dropping the team filter in `_get` survived until `test_another_teams_row_under_the_same_scope_id_is_invisible_and_untouchable` was added (now killed).
* Sqlite runs the REAL migrations 0041, 0043, 0045 (and 0046 in the one real-inbox test). No engine tasks are spawned; the
  race tests `asyncio.run(gather(...))` and finish before `engine.dispose()`.

## Directives
001 comments on sessions and runs, plus run event positions: `POST|GET {runs|sessions}/{id}/comments`; `event`/`approval`/`plan`/`diff`/`file` references (`test_references_*`).
002 replies (bounded depth 2, `test_replies_are_bounded_*`), mentions (`test_mentions_*`, `test_run_mentions_*`, `test_an_edit_notifies_only_newly_mentioned_people`), reactions as a closed set enforced in API and DB (`test_reactions_*`).
003 references to plan, file, diff, approval and event position, validated on write, **re-authorized on every read**, unavailable ones return no id (`test_references_are_checked_on_write_and_reauthorized_on_every_read`, `test_session_references_resolve_through_the_sessions_runs`).
004 discussion vs instruction: nothing reads comments on the agent side; a comment writes no `redirect.*` event (`test_comments_are_never_agent_input`); the only bridge is promote (`test_promote_*`).
005 explicit action: promote needs `steer`, the literal `action: "send_as_guidance"` (422 without), the `expectedVersion` the promoter saw, goes through `collab.redirect` (same events, same lease queue: `test_a_non_holder_promote_is_queued_for_the_holder_like_any_guidance`), is idempotent, records the link `comment_promotions` + audit `comment.promoted` (`test_promote_is_idempotent_and_a_retry_heals_a_half_done_promotion`, `test_two_simultaneous_promotes_send_one_instruction`).
006 authorship (name snapshot), timestamps, `edited`, revision history route, moderation state/by/at/reason in every listing (`test_create_list_and_authorship_are_visible`, `test_history_lists_every_revision_*`, `test_moderation_*`).
007 edit window (team setting, default 900 s, exact boundary, fake clock) and deletion = tombstone; audit-sensitive scopes keep revisions for admins, others blank them; `auditSensitive=always` (`test_edit_window_*`, `test_the_edit_window_is_exact_*`, `test_delete_*`, `test_the_team_can_force_*`).
008 mentions only reach people who can already see the scope; unknown/foreign/not-allowed users are indistinguishable (`ignoredMentions`); the notify call carries ids only (no text, no title); private session is a 404 for non-members (`test_a_private_session_is_a_404_*`, `test_mentions_reach_only_people_who_can_see_the_scope_and_leak_nothing`, `test_mentions_land_in_lane_ns_real_inbox_with_ids_only` with the real `notify.emit` and migration 0046).
009 search over authorized scopes only (own team, member sessions, admins all), hidden/deleted/expired excluded, wildcards escaped, paged (`test_search_*`). "Session history" is read as discussion only (see gaps).
010 retention (`retentionDays`, enforced at read plus `purge_expired`) and export (own scope and team, role-based redaction, past-retention text never exported, audited) (`test_export_*`, `test_retention_*`, `test_settings_*`). Attachments do not exist in this lane, stated in the export.

Cross-cutting: wrong team = identical 404 envelope on all 9 scope routes in both scopes; role gate before ownership with an `authz.denied` row; every audit action is a reserved name and no row contains comment text (`test_every_audit_action_is_a_reserved_name_and_no_text_is_logged`); openapi has exactly the contracted paths; each route has the policy permission the lead classified (`test_every_discussion_route_has_the_policy_permission_the_lead_classified`); the migration has no `ADD COLUMN IF NOT EXISTS`, no `now()` default, no dollar quotes and re-runs cleanly.

## For the lead
* Wire `app.include_router(discussion.router)` and `discussion.configure_discussion(service)` (same place as `control`).
* Schedule `discussion.purge_expired(factory)` (worker loop or cron); read-time enforcement already hides expired text.
* Paths: only those of the contract. `{scope}` routes are registered explicitly (`/runs/{scopeId}/comments...` and `/sessions/{scopeId}/comments...`); the path parameter is named `scopeId` in OpenAPI. `openapi.json` and the SDKs need regenerating (forbidden for lanes).
* Gate: all discussion paths already classify correctly (tested). No gap found. `comment` is granted to viewers by the policy engine; the legacy column also allows viewers but not guests.
* `notify.emit` is called with `kind='mention'`, `source_kind='comment'`, no `summary_code` (lane N defaults it to `mentioned`). No change needed in N.
* Audit names written raw by section4, all reserved: `comment.*`, `discussion.settings_changed`; `authz.denied` for refusals. The `comment.*` events on the run stream are new kinds in `run_collab_events` (ids only); `docs/audit-and-events.md` lists the old kinds and was not edited here.
* Matrix row for part 43 and the evidence summary are not edited by this lane.

## Honest gaps
* Not run on Postgres (no docker). The SQL is portable by construction but unproven there; no live-PG test.
* No rate limit or per-scope cap on comment writes; no markdown/escaping policy server-side (plain text); no per-thread unread counts.
* Search is an unindexed `LIKE` scan and covers discussion text only: searching session history (events, plans) is part 50.
* The purge job is a function nobody schedules yet; retention is enforced at read in the meantime.
* Session scope has no live push event; clients poll. Run scope events are ids only.
* Audit-sensitivity `auto` is "has had a high-impact approval"; other audit-relevant histories do not mark a scope.
* Promoted guidance text is the comment verbatim from a possibly low-trust author; the safeguard is the explicit action by a steerer who must name the version they read, and the lease queue; the engine's prompt framing of guidance is unchanged.
* Moderation reason text is stored on the comment row and shown to scope readers; it is not scanned for secrets.
* Removing a person from a session does not remove their old comments or mention rows.
