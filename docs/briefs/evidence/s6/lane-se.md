# Session 6, lane SE: search, export, deletion requests (part 50)

Docs: `docs/search-and-export.md`. Nothing committed; no docker/make/smoke run.

## Files (all owned by the lane)

| file | what |
|---|---|
| `section1/migrations/0050_search_export.sql` | `search_documents`, `search_index_state`, `export_jobs`, `deletion_requests` (portable SQL, loads on SQLite; no PG-only DDL) |
| `section4/src/harness_orchestrator/search.py` | indexer (`sync_index`, `reindex`), live re-authorising query, `GET search`, `GET search/status`; shared helpers (`scrub_text`, `clean_json`, `audit`, `rows`) used by exports |
| `section4/src/harness_orchestrator/exports.py` | export jobs and store, `purge_expired_exports`, deletion requests, routes |
| `section4/tests/unit/test_search_support.py` | fixture (real migrations 0041/0043/0045/0050, fake clock, temp export store) |
| `section4/tests/unit/test_search.py` (35), `test_exports.py` (24), `test_exports_deletion.py` (15) | 74 tests |
| `docs/search-and-export.md`, this file | |

## Commands run and results

* `cd section4 && .venv/bin/python -m pytest -p no:cacheprovider -q tests/unit/test_search*.py tests/unit/test_exports*.py` -> 74 passed (about 12 s).
* `.venv/bin/ruff check` on the six lane files (no `--fix`; `--select I001 --fix` once on `test_search_support.py`) -> All checks passed.
  `ruff check src tests` as a whole reports E501 in other lanes' files (`execution.py:1293`, `test_execution_support.py`, ...), not ours.
* Full `section4` suite once: `1426 passed, 37 skipped, 2 failed` in 85 s. The two failures are lane EX, outside our files:
  `tests/unit/test_execution_runs.py::test_start_is_refused_when_the_model_call_budget_is_used` and
  `::test_a_live_claim_or_task_or_pending_retry_means_the_previous_attempt_still_holds_the_run`.

## Directives

| directive | how it is met | tests |
|---|---|---|
| 50.001 search over authorised sessions, runs, comments, events, artifacts | six kinds indexed (plus approvals); `GET search` | `test_comments_are_indexed_and_found_with_a_live_snippet`, `test_runs_sessions_and_approvals_are_searchable_by_title_and_body`, `test_deleted_sessions_and_unavailable_artifacts_are_not_searchable` |
| 50.002 filters project, participant, status, date, approval state | SQL narrowing plus live re-check; `kind`, `cursor` | `test_filters_kind_participant_status_date_project_and_approval_state`, `test_filters_are_rechecked_against_the_live_source`, `test_cursor_paging_never_skips_or_repeats`, `test_query_validation` |
| 50.003 index only permitted data | hidden/deleted/purged/past-retention comments, deleted sessions, quarantined artifacts, noise events, raw output, secret-shaped keys and values, hidden-reasoning keys never enter `search_documents` | `test_hidden_deleted_and_purged_comments_are_not_indexed`, `test_secret_shaped_text_is_redacted_in_the_index_and_in_snippets`, `test_hidden_reasoning_keys_are_not_indexed`, `test_event_stream_uses_a_seq_watermark_and_skips_noise` |
| 50.004 re-check authorisation at serve time, incl. snippets | every candidate rebuilt from the live source; snippet from live scrubbed escaped text; no count; cursor only when another authorised hit exists | `test_a_stale_index_row_cannot_leak_a_comment_hidden_afterwards`, `test_private_session_comments_follow_membership_live`, `test_team_isolation_and_planted_foreign_rows`, `test_no_oracle_cursor_or_count_for_rows_the_caller_cannot_see`, `test_edited_text_is_judged_on_the_live_comment_not_the_indexed_copy`, `test_pilot_allowlist_hides_run_data_but_not_session_comments`, `test_snippets_are_escaped_bounded_and_built_from_live_text`, `test_scan_budget_exhaustion_is_reported_and_the_cursor_continues` |
| 50.005 export of session history and audit evidence in documented formats | `session_history`, `run_history`, `audit_evidence`; `json` and `ndjson` (`harness.export/1`) | `test_run_history_export_has_ordered_records_with_actor_identity`, `test_ndjson_format_has_header_records_and_a_complete_footer`, `test_session_history_covers_session_comments_and_linked_run_events`, `test_audit_evidence_is_team_scoped_scrubbed_and_ordered` |
| 50.006 event ordering and actor identity where authorised | `order`, `seq`, deterministic sort; actor block, pseudonyms on request, erased people shown as deleted user | `test_export_is_deterministic_for_the_same_data`, `test_include_actors_false_uses_pseudonyms_and_drops_the_roster`, `test_later_exports_show_the_erased_person_as_a_deleted_user`, `test_audit_evidence_is_admin_only` |
| 50.007 deletion requests, retained exceptions explained | own-data request, admin decision, execution of the documented list, `policy.retained` with reasons in every response and in the executed result | `test_execution_deletes_the_documented_list_and_keeps_the_documented_exceptions`, `test_search_no_longer_finds_the_deleted_text_even_with_a_stale_index`, `test_export_files_of_the_person_and_covering_their_data_are_deleted`, `test_session_export_files_are_deleted_with_the_authors_data`, `test_only_admins_decide_and_a_refusal_leaves_an_audit_row`, `test_an_admin_cannot_decide_their_own_request_unless_self_approval_is_enabled`, `test_rejection_needs_a_reason_deletes_nothing_and_cannot_be_executed_later`, `test_repeating_the_approval_is_a_no_op_and_a_crash_before_execution_is_resumable`, `test_one_open_request_per_person_and_a_retry_with_request_id_is_one_request` |
| 50.008 exports free of secrets, hidden reasoning, unauthorised content | `clean_json` + `scrub_text`; durable feed only; allow-listed agent summaries; comment visibility rules; team/scope guard and 404-without-oracle | `test_exports_contain_no_secrets_reasoning_noise_or_raw_output`, `test_hidden_deleted_and_past_retention_comment_text_is_withheld`, `test_foreign_unknown_and_private_scopes_answer_the_same_404`, `test_roles_without_export_permission_are_refused_and_audited` |
| 50.009 indexing delay and consistency defined | `docs/search-and-export.md` section 3 and `GET search/status` (`indexing`, `consistency`, lag per source) | `test_status_documents_delay_consistency_and_hides_counts_from_non_admins`, `test_query_refreshes_a_stale_index_but_not_a_fresh_one`, `test_second_sync_is_incremental_via_watermarks`, `test_indexing_pages_through_batches_and_reports_partial_progress`, `test_reindex_rebuilds_audits_and_cleans_hard_deleted_sources` |
| 50.010 audited bulk exports, size limits | `export.requested/completed/refused/downloaded` with ids and counts; `HARNESS_EXPORT_MAX_RECORDS` / `_BYTES`, `413 EXPORT_TOO_LARGE`, job kept as `refused`; TTL store; download re-authorises and is audited | `test_over_the_record_limit_is_refused_with_a_stable_code_and_no_file`, `test_over_the_byte_limit_is_refused_too`, `test_download_headers_checksum_and_audit`, `test_download_reauthorises_the_scope`, `test_expired_exports_are_refused_and_the_purge_removes_the_file`, `test_a_tampered_file_fails_its_checksum`, `test_other_engineers_foreign_teams_and_unknown_ids_cannot_download`, `test_a_retried_request_id_is_one_job`, `test_concurrent_duplicate_requests_create_one_job` |

Audit names written (all in the reserved list): `export.requested|completed|refused|downloaded`, `search.reindexed`,
`deletion.requested|approved|rejected|executed`, plus `authz.denied` for a non-admin deletion decision. Detail is ids, counts and
before/after states only (asserted for `deletion.*` and `search.reindexed`).

## Untested on PostgreSQL (the lead's live-PG test should cover)

1. The full text branch: `to_tsvector('simple', title || ' ' || body) @@ plainto_tsquery('simple', :fts)` (only the SQL builder
   `match_sql` and the fall-back-to-LIKE-on-error path are unit-tested; the error path was exercised on SQLite by forcing the
   engine).
2. `INSERT ... ON CONFLICT (team_id, kind, source_id) DO UPDATE` (documents) and `(team_id, source)` (state) with timestamptz
   binds; timestamp watermarks compared as timestamptz; `SELECT 1 WHERE EXISTS (...)` in the deletion export check.
3. Audit exports: the PG-only column list (`chain_pos, prev_hash, row_hash`) and `CAST(detail AS TEXT) LIKE` on `jsonb`; the
   `detail` value arriving as `dict` rather than `str` (`as_dict` handles both).
4. `DBAPIError` classification of a missing table (`missing_table` matches "does not exist") so `artifacts` before lane AR is skipped.
5. Migration 0050 itself on PG (foreign keys to `teams`, partial unique indexes, `TIMESTAMPTZ`).
6. No GIN index exists; check query time on a large team and consider the follow-up index (comment in the migration header).

## Honest gaps

* Artifact **file contents** are not indexed or exported (needs lane AR's store API); only name/media type/kind. Artifact
  files are not deleted by a deletion request (documented as retained, AR owns artifact deletion).
* Nothing schedules `sync_index`, `purge_expired_exports` or `discussion.purge_expired`; the on-query refresh (30 s) is the only
  driver. `reindex` has no HTTP route (not in the lane's path list).
* Participant and approval-state filters can miss items until the source changes or a reindex (membership and approval changes do
  not stamp the sources); the live re-check only removes false positives.
* Exports are built synchronously in memory (capped by `HARNESS_EXPORT_MAX_BYTES`) and stored on local disk: a multi-replica
  deployment needs a shared `HARNESS_EXPORT_DIR`. No object store, no encryption at rest beyond the platform.
* `audit_evidence` does not apply section1's `require_team_stepup` re-authentication (section4 cannot import section1); the lead
  may enforce it in the policy middleware or accept the gap.
* Secret redaction in free text is heuristic (shaped values and `key=value` pairs); JSON payloads are filtered by key name.
* LIKE mode is a sequential scan (bounded by the scan budget); PG full text matches whole words only.
* Deleting a person's data does not touch notification inbox rows (lane N), run events, approvals or audit rows (retained, listed).
* The export `from`/`to` window applies to record timestamps; comment edits do not create separate records.

## What the lead must wire

1. `app.include_router(search.router)` and `app.include_router(exports.router)`; `search.configure_search(service)` and
   `exports.configure_exports(service)` next to `configure_discussion`.
2. Policy classification of the Session 6 SE paths (already done by the lead per the prompt): `exports*` = export, `deletion-requests`
   approve/reject = administer, search = read. The code enforces legacy roles itself (export: admin/engineer; audit evidence and
   deletion decisions: admin) and does not depend on the middleware.
3. A scheduler for `search.sync_index(factory)`, `exports.purge_expired_exports(factory)` and `discussion.purge_expired(factory)`;
   an admin or CLI path for `search.reindex(factory, team_id)` if wanted.
4. Rate-limit entries (`ratelimit_hooks.MUTATION_ROUTE_LIMITS`) for `POST exports` and `GET exports/{id}/download` (large bodies).
5. `openapi.json` / SDK regeneration, capability-matrix rows for part 50, and the migrate-drift/migration-count gate for 0050.
6. Set `HARNESS_EXPORT_DIR` to a persistent (shared, if replicated) volume in deploy config; optionally the PG GIN index.
