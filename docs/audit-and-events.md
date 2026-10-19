# Audit and events (master prompt part 11)

Two records with two jobs. Every statement below was checked against the code and tests named next to it
(session 2, lane A). Paths are relative to the repo root; `s1` = `section1/src/harness_identity`,
`s4` = `section4/src/harness_orchestrator`.

| | Product events | Security audit |
|---|---|---|
| Question it answers | what happened in the run, for the people in it (UI history, live push) | who did or tried what, for investigators and auditors |
| Tables | `agent_events` (project stream), `run_collab_events` (collaboration) | `audit_log` |
| Written by | engine/bus (`s1/eventbus.py`), collaboration code (`s4/collab.py` `_event`) | `s1/audit.py` `write_audit` (ORM, action allowlist), `s1/audit_emit.py` helpers, raw SQL in `s4/collab.py` `_audit` and `s4/authz.py` |
| Tamper protection | none (ordinary rows) | hash chain + update refusal (migration 0036) |
| Contains denials | no | yes (`authz.denied`, `approval.self_denied`) |
| Payload rule | no diffs or free-text summaries in approval events | before/after values, no secret-shaped keys |
| Retention | 30 days by default, sweeps are opt-in | 365 days by default, sweep is opt-in |

A consequential collaboration change writes BOTH in one transaction (`s4/collab.py` `_approval_change`): the event
for collaborators (ids and states only) and the audit row (same ids plus before/after and the acting user/team).

## 1. Product events

| Stream | Read by | Cursor | Replay bound | Retention |
|---|---|---|---|---|
| `agent_events` | any role of the project's team: `WS /api/v1/ws/{project_id}`, `GET /api/v1/projects/{id}/events/replay` | `afterSeq` (table-global `seq`, so per-project gaps are normal) | `REPLAY_MAX_EVENTS` (default 5000) newest events plus a `replayTruncated` marker; HTTP `limit` max 2000 | 30 days, monthly partition drop (`deploy/runners/events_retention.py`, compose profile `events-retention`, not on by default) |
| `run_collab_events` | any role of the run's team (viewers included): `WS /api/v1/ws/{project_id}/runs/{run_id}/collab`, `GET /api/v1/orchestrator/runs/{runId}/collab-history?after=&limit=` (max 500); admins also via export (section 3) | `seq` (`afterSeq` on the socket, `after` on HTTP) | same `REPLAY_MAX_EVENTS` cap on the socket | `s4/retention.py` deletes them for finished, quiet runs (`HARNESS_RETENTION_DAYS`, default 30). It is a CLI (report-only without `--apply`); nothing in `deploy/` schedules it, so unless an operator runs it retention is indefinite |

Collaboration events: `kind` in `participant.joined`, `leader.assigned`, `leader.takeover`, `redirect.guidance`,
`redirect.scope_change`, `redirect.permission_change`, `handoff.requested|accepted|rejected`,
`approval.requested|approved|rejected|amended|invalidated|expired|claimed|completed`. `guidance.delivered` is an engine
cursor marker, hidden from history, push and export.

* Ordering: on Postgres `_event` takes a per-run advisory lock until commit, so `seq` order equals visibility order for
  one run and a cursor never skips a late commit.
* Idempotency: `(run_id, request_id)` is a unique index; a retry returns the first row with `duplicate: true`.
* Secrets: `_scrub` drops any key whose name contains token, secret, password, passwd, key, otp, authorization or
  credential, in events and audit (`s4/collab.py`, `s1/audit_emit.py`). It is by key name only; free text is not scanned.
* Bound: the push frame replaces a payload over `BUS_PAYLOAD_MAX_BYTES` (8192) with `{"omitted": true, ...}`.

## 2. Security audit (`audit_log`)

**Who can read.** `GET /api/v1/audit` (newest first, max 200) and `GET /api/v1/audit/export` are admin-only and
team-scoped. Nobody else has a read path.

**Hash chain (migration `section1/migrations/0036_audit_hash_chain.sql`).**

| Piece | Behaviour |
|---|---|
| Columns | `chain_pos` (commit order), `prev_hash`, `row_hash`; table `audit_chain_head(last_pos, last_hash)`, one row |
| What is hashed | SHA-256 over `prev_hash`, `seq`, `chain_pos`, `ts` (microseconds since epoch), `actor_member_id`, `actor_kind`, `action`, `target_type`, `target_id`, `detail` (jsonb text), joined by 0x1f, in SQL function `audit_row_hash` |
| Who seals | a deferred constraint trigger (`audit_log_seal`) at COMMIT, so ORM writers and raw-SQL writers are both chained and the head lock is taken last (no lock-order deadlock with collaboration rows) |
| Update refusal | trigger `audit_log_no_update` raises `audit_log is append-only (seq N)` for any UPDATE except the one-time sealing (`row_hash` NULL to value, content unchanged). The ORM also refuses update/delete (`s1/audit.py` `register_append_only_guard`) |
| Delete | allowed at the database (retention needs it) |
| Retention | `events_retention.py` prunes `audit_log` by `ts` (365 days, `AUDIT_RETENTION_DAYS`, opt-in profile). Verification treats a pruned prefix as anchored at the first surviving row and reports `pruned_prefix` |
| Backfill | rows that existed before 0036 are sealed by the migration in `seq` order (`test_migration_backfills_pre_existing_rows`) |
| Rollback | in the migration header (drop triggers, functions, `audit_chain_head`, three columns); no test runs it |
| Allowlist | `write_audit` raises on an action outside `APPEND_ONLY_ACTIONS`; raw-SQL writers are not checked against it |

**Verify.** `section1/.venv/bin/python section1/scripts/verify_audit_chain.py [DATABASE_URL]`
(default `$HARNESS_DATABASE_URL`; read-only; Postgres only; it recomputes every hash with the same SQL function).

| Exit | Meaning |
|---|---|
| 0 | chain intact (`audit chain OK: N rows, positions a..b`, plus a note when older rows were pruned) |
| 1 | tampering or breakage, problems printed (first 50) |
| 2 | could not verify (no DSN, cannot connect, SQLite without `audit_row_hash`) |

| Tampering | Reported as | Test (`section1/tests/test_audit_chain_pg.py`, live PG, opt-in `HARNESS_TEST_PG=1`) |
|---|---|---|
| edit a row (incl. its `detail` before/after) | `row content was modified` | `test_editing_a_row_fails_verification`, `test_editing_detail_before_after_values_fails_verification` |
| edit a row and recompute its hash | next row `prev_hash does not link` | `test_editing_the_stored_hash_too_still_breaks_the_next_link` |
| delete a middle row | `gap in chain` | `test_deleting_a_middle_row_fails_verification` |
| delete the newest rows | `chain head is at position ...` | `test_deleting_the_newest_rows_fails_verification` |
| insert around the trigger | `not in the chain` | `test_row_written_around_the_trigger_is_flagged` |
| SQL UPDATE | refused by the database | `test_update_is_refused_by_the_database` |
| prune oldest rows | still OK, `pruned_prefix` | `test_retention_pruning_oldest_rows_keeps_the_rest_verifiable` |
| concurrency, rollback | one linear chain, no gap | `test_concurrent_writers_keep_one_linear_chain`, `test_both_writers_are_chained_and_verify_clean`, `test_rolled_back_audit_leaves_no_gap`; section4 `test_collab_chaos_live_pg.py::test_collab_audit_rows_form_one_valid_hash_chain_under_concurrency` |
| CLI | exit 0 then 1 after an edit | `test_cli_exit_codes_intact_then_edited` |

## 3. Export endpoint

`GET /api/v1/audit/export` (identity service, `s1/app.py` `export_audit`).

| Aspect | Behaviour |
|---|---|
| Auth | admin only; any other role gets 403 (also for `stream=collab`), no credentials 401/403 |
| Scope | the caller's team only; there is no team parameter (`teamId=`/`team=` are ignored). `stream=audit`: rows whose actor is a member of the caller's team, plus unbound rows (`actor_member_id` NULL, e.g. denials) whose `detail` text contains `"teamId": "<caller team>"`. `stream=collab`: `team_id = caller team` in SQL; a foreign `runId` returns an empty list |
| Query | `stream` (`audit` default, `collab`; else 422), `afterSeq` (default 0), `limit` (default 200, clamped to 1..1000), `runId` (collab only) |
| Paging | ascending by `seq`; response `{stream, items, limit, hasMore, nextCursor}`; follow `nextCursor` while `hasMore` |
| Audit fields | `seq, actorMemberId, actorKind, action, targetType, targetId, detail, ts, chainPos, prevHash, rowHash` (the chain fields are null on SQLite) |
| Collab fields | `seq, runId, actor, kind, requestId, payload, ts` (no cursor markers) |
| Audited | each call writes `audit.exported` with `{stream, afterSeq, rows}` for the admin, best effort: a failed audit write is logged and the export still succeeds |
| 503 | `stream=collab` when `run_collab_events` does not exist |

Tests (`section1/tests/test_audit_export_and_collab_push.py`): `test_export_is_team_scoped_cross_team_gets_nothing_of_the_others`,
`test_export_has_no_team_parameter_to_widen_scope`, `test_export_requires_admin_and_authentication`,
`test_export_is_bounded_and_pages_without_gaps_or_repeats`, `test_export_carries_chain_fields_and_is_itself_audited`,
`test_collab_stream_export_is_team_scoped_and_hides_cursor_markers`.

## 4. WebSocket push of collaboration events

`WS /api/v1/ws/{project_id}/runs/{run_id}/collab` (`s1/app.py` `run_collab_ws`).

| Aspect | Behaviour |
|---|---|
| Auth | same admission as the project socket: one-time `?ticket=` from `POST /api/v1/ws-ticket {projectId}` (30 s, project-bound, bound to a client fingerprint sent as `X-WS-Fingerprint` or `?fingerprint=`); deprecated `?token=` and `Authorization: Bearer` still work. Close codes: 4401 unauthenticated, 4403 fingerprint mismatch, 4404 unknown or foreign project, or a run that is not in that project (no oracle) |
| Scope | rows are read with `team_id = principal team` in SQL; another team's events are never sent |
| Frame | `{channel:"collab", seq, kind, actor, payload, ts, runId, projectId, teamId, schemaVersion}` (exact field set pinned by `test_connect_replays_history_in_order_then_resumes_from_a_cursor`) |
| Resume | connect with `?afterSeq=<last seq seen>`; the catch-up is replayed first in ascending order, then live frames follow |
| Duplicates | the server never sends a `seq` at or below the connection's cursor and counts the skip as `duplicate`; clients still de-duplicate by `seq` across reconnects |
| Truncation | a gap larger than `REPLAY_MAX_EVENTS` (default 5000) sends the newest N frames, then `{channel:"collab", replayTruncated:true, oldestSeqSent, afterSeq}`; backfill the older part with `collab-history?after=<afterSeq>` |
| Wake-up | migration `section1/migrations/0037_collab_event_push.sql`: trigger `run_collab_events_notify` does `pg_notify('collab_events', {seq, runId, kind})` (identity only, never the payload). The identity `EventBus` LISTENs on `collab_events` (and `agent_events`) over one asyncpg connection, reconnects after 1 s on error, and sets the run's wake events. Rows are always re-read from the table. The waker is registered before the replay so nothing falls in the gap |
| Polling fallback | the socket also drains every `COLLAB_WS_POLL_SECONDS` (default 2.0), so a lost NOTIFY only delays delivery; plain HTTP polling of `collab-history` remains for clients without a socket. On SQLite there is no listener at all |
| Client messages | ignored; the socket only watches for disconnect |

Tests: `test_connect_replays_history_in_order_then_resumes_from_a_cursor`, `test_live_push_after_wakeup_and_via_poll_fallback`,
`test_wakeups_for_already_delivered_events_are_counted_not_resent`, `test_large_gap_is_capped_with_an_honest_truncation_marker`,
`test_other_teams_events_are_never_pushed`, `test_unauthorized_connections_are_closed_without_data` (6 cases),
`test_ticket_auth_is_shared_single_use_and_project_bound`; live PG (`test_collab_push_pg.py`):
`test_trigger_notifies_with_identity_only`, `test_bus_listener_wakes_a_run_subscriber_end_to_end`.

## 5. Before/after values

| Change | Audit row | `before` / `after` |
|---|---|---|
| member removed | `member.removed` | `{role, member:true}` / `{role:null, member:false}` |
| member role changed | `member.role_changed` | `{role}` / `{role}` (plus `fromRole`, `toRole`) |
| leader assigned, taken over | `leader.assigned`, `leader.takeover` | `{leader}` / `{leader}` |
| handoff | `handoff.requested`, `.accepted`, `.rejected` | `{leader[, pendingHandoffTo]}` / `{leader[, pendingHandoffTo]}` |
| approval lifecycle | `approval.requested|approved|rejected|claimed|completed|expired` | `{state}` / `{state[, decidedBy|claimedBy]}` (`requested` before is null) |
| approval amended | `approval.amended` | `{state, digest}` / `{state:"pending", digest}` (the old digest is kept only here) |
| approvals voided by a scope/permission change | `approval.invalidated` | `[{id, state}]` / `{state:"invalidated"}` |
| scope change | `redirect.scope_changed` | `after: {invalidatedApprovals}`, plus `reason`; no `before` |

Tests: `test_roster_api.py` (role change, removal), `test_collab_rules_s2.py::test_approval_lifecycle_records_before_after_and_emits_events`,
`::test_amend_and_scope_invalidation_record_what_changed`, `::test_leader_changes_record_before_and_after`,
`::test_unanswered_approval_expires_stops_blocking_and_leaves_evidence`.
`billing.plan_changed` carries `{from, to}` (plan names). NOT recorded: API keys, invites, project rules, reviewer thresholds, SSO
config, which carry ids only (before/after is "where safe" in the directive; these were not done).

## 6. Rule for simultaneous instructions

1. Ordinary guidance from several people is never merged or dropped: each is its own row, ordered by `seq`; the agent
   receives all of them, numbered, in `seq` order when the engine next starts a leg (`guidance_since`). Same `requestId` = one row.
2. Scope and permission changes are leader-only and need a non-empty reason (422 `REASON_REQUIRED`); a non-leader gets
   403 `NOT_LEADER` and an `authz.denied` row.
3. A scope or permission change voids every pending/approved high-impact approval. Racing a decision, the approval ends
   `invalidated` in either order.
4. Two simultaneous leader claims produce exactly one leader (partial unique index). Handoff keeps the proposer in
   control until the recipient accepts; one open handoff per run; an admin may take over an absent or live leader (audited).

Tests: `test_collab_rules_s2.py::test_simultaneous_guidance_is_all_kept_in_seq_order_and_delivered_once`,
`::test_simultaneous_scope_changes_only_the_leader_counts`, `::test_scope_change_racing_an_approval_decision_never_leaves_it_usable`,
`::test_two_simultaneous_leader_claims_yield_exactly_one_leader`, `::test_scope_and_permission_changes_need_a_reason` (6 cases).

## 7. Counters (Prometheus) and exact label values

Closed label sets, every series rendered at 0 from process start, values in memory (reset on restart). Defined in
`s4/domain_metrics.py` (all four) and `s1/domain_metrics.py` (event delivery). An unknown value collapses to `other`
(section4) or is ignored (section1).

| Counter | Label values actually emitted | Emitted at |
|---|---|---|
| `harness_approval_denied_total{reason}` | `not_leader` (only for `approval.*` actions), `self_approval`, `digest_mismatch`, `expired` (decision on an expired request, and each request expired by the TTL sweep), `not_pending`, `not_approved` (claim refused: wrong state, changed digest or expired), `not_executing`, `not_requester`, `not_amendable`, `staging_mismatch` (apply refused: staged bytes changed) | `s4/collab.py` `_denied`, `decide_approval`, `expire_stale_approvals`; `s4/api.py` apply route. `other` is declared, no call site emits it |
| `harness_run_cancel_total{outcome}` | `requested`, `confirmed`, `unconfirmed`, `noop`, `refused` | `s4/service.py`: `stop_agent` (requested, confirmed/unconfirmed, noop on a finished run), `pause_agent` (requested, confirmed/unconfirmed, refused when the run is already DONE), `_fence_lost_claim` (requested, confirmed/unconfirmed) |
| `harness_isolation_violation_total{kind}` | `bad_session_id`, `workspace_escape` (`engine.run_workspace`), `symlink_escape` (`engine.cleanup_scratch`, `staging._inside`, `staging.apply_group`), `path_traversal` (`collab.validate_paths`, `staging.apply_group`). `other` declared, never emitted | `s4/engine.py`, `s4/staging.py`, `s4/collab.py` |
| `harness_event_delivery_total{outcome}` | `delivered`, `replayed`, `duplicate`, `dropped` | identity: `delivered` and `replayed` count frames sent on the project and collaboration sockets; `duplicate` = NOTIFY for a seq already delivered directly, a collaboration frame at or below the cursor, or a wake-up that found nothing new; `dropped` = a project-stream subscriber queue was full (`eventbus.publish`, `_fanout_local`; the socket is closed 1013 to resync). Orchestrator: only `duplicate` (a `requestId` retry suppressed in `collab.redirect`) |

Reading the counters honestly:

* `unconfirmed` means "not confirmed gone when the request returned". A stop sent to a replica that does not own the worker is
  always `unconfirmed` (the owner kills it at its next heartbeat) and nothing counts the later confirmation.
* `path_traversal` and `bad_session_id` also fire for ordinary invalid input (an absolute or empty path in an approval request),
  not only attacks.
* Identity `duplicate` includes a harmless wake-up that arrives after an earlier drain already sent the event, so bursts of
  events produce duplicates without any fault. `dropped` cannot come from the collaboration socket (it re-reads the table).
* `expired` counts system TTL expiries as well as user decisions.

**Scraping.** Identity `GET :8001/metrics` and orchestrator `GET :8004/metrics` append these renderers
(`deploy/runners/services.py` `_wire_observability`, `simple_app(..., orchestrator_domain_metrics.render)`); the legacy
`GET /api/v1/orchestrator/metrics` also carries the orchestrator ones. Guards: `section1/tests/test_deploy_observability.py`
(`test_metrics_endpoint_appends_extra_domain_counters`; `test_services_wire_domain_counters_into_the_scraped_metrics` is a
source-text check, not a live scrape) and `section4/tests/unit/test_collab_rules_s2.py::test_agreed_domain_counters_appear_in_the_scrape_after_triggering_them`
(legacy route). `section1/tests/test_audit_export_and_collab_push.py::test_event_delivery_counters_appear_in_the_scrape_after_triggering_them`
checks the renderer text, not an HTTP scrape.

## 8. Honest limits

| Limit | Consequence |
|---|---|
| Chain and head live in the same database | a superuser (or table owner who can disable triggers) can rewrite rows, recompute every later hash and update `audit_chain_head`; verification then passes. There is no external anchor or signature. Keep the CLI output and `last_hash` outside the database to close this |
| Newest-row truncation is caught only through `audit_chain_head` | covered by `test_deleting_the_newest_rows_fails_verification` (rows deleted, head left alone). Deleting rows AND editing the head is not tested and not detected |
| Deleting the oldest rows looks like retention | reported as `pruned_prefix`, still exit 0. Rows older than the surviving prefix cannot be re-verified; rows sealed by the 0036 backfill were only protected from that point |
| The sweep and DELETE | the application role can delete audit rows (retention needs it); the sweep and the pruned-prefix case are not exercised together in a test |
| Export cannot prove completeness | it is team-scoped, so `chainPos` gaps are normal and linkage cannot be checked from it; recomputing `rowHash` needs Postgres's jsonb text and microsecond `ts`, which the JSON export does not guarantee. Verification is the database CLI. Paging is by `seq`, which is assigned before commit, so a row that commits late can fall behind `nextCursor`; re-export from an earlier `afterSeq` to catch it |
| "Export is audited" is best effort | an `audit.exported` write failure is swallowed |
| Not audited | a 403 on `/audit` or `/audit/export`, WebSocket admission failures, `PILOT_NOT_ENABLED` refusals, and staged approvals voided because the staged files changed (the new request is audited, the voided one is not) |
| Sockets are not re-validated after upgrade | a revoked session or removed member keeps receiving frames until the socket closes (threat T10b) |
| Free text | guidance content and approval summaries can hold secrets; only secret-shaped keys are scrubbed |
| One global chain | every committing audit row serializes on the head row at commit; throughput was not measured |
| Retention is mostly opt-in | the collaboration-event purge is a manual CLI and the agent_events/audit sweeps are an opt-in compose profile |
