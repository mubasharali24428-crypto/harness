# Session 5 — Discussion, notifications, real-time delivery, collab UI a11y (parts 43, 46, 47, 56-collab)

Prefix: `docs/briefs/stable-prompt-prefix.md`. Directives: `docs/master-prompts/harness-multiplayer-master-build-prompt.md` sections 43, 46, 47, 56
(read only your section, plus `docs/briefs/PROJECT-STATE.md` section 6 "Facts that bite"). Target: each part at 9/10 = every directive implemented or
explicitly out of scope with a reason, invariant tests, an evidence file, docs that match, honest gaps. Part 56 is only raised for the collaboration UI
(rest of 56 is Session 10).

## What exists (verify, do not trust)
- Session 4 landed sessions (`collab_sessions`, `session_members`, `session_runs`), presence, control leases and queued suggestions
  (`control_instructions`, `control/decide`), agents. Docs: `docs/sessions.md`, `presence.md`, `control.md`, `agents.md`.
- Collaboration per run lives in section4 (`collab.py`, tables `run_participants`, `run_collab_events`, `run_handoffs`, `high_impact_approvals`).
  Events carry a per-run `seq`; Session 2 added a collab push socket and `docs/audit-and-events.md` describes delivery rules. Read both before changing anything.
- Notifications today: section1 `notifier.py` (durable email outbox + worker) and `notification_rules.py` (run-terminal rules). There is no in-app inbox,
  no preferences, no digests. Guidance today = `collab.redirect`; non-holder guidance is queued for the lease holder (`control.py`).
- Org policy gate (`contracts/.../policy_enforce.py`): the lead already classified the paths below (comment/steer/administer/export, `/api/v1/notifications/*` = read).
- Sections other than section1 may import only `harness_contracts`; section4 speaks raw SQL (`sqlalchemy.text`) to shared tables, section1 too (Core `text()`).
  All migrations are section1 files (next free numbers: **0045 D, 0046 N, 0047 R**).

## Lane contract (the lead wires everything; lanes never touch shared files)
Lanes: **D** discussion (43), **N** notifications (47), **R** real-time delivery (46). A UI lane **X** (section7, parts 43/46/47/56 UI) runs after D, N, R report.
Each backend lane owns ONLY:

- **D** (section4): migration `0045_discussion.sql`, `section4/src/harness_orchestrator/discussion.py` (exports `router`, `configure_discussion(service)`),
  tests `section4/tests/unit/test_discussion*.py`, `docs/discussion.md`, `docs/briefs/evidence/s5/lane-d.md`.
- **N** (section1 + one helper for section4): migration `0046_notifications.sql`, `section1/src/harness_identity/notifications.py` (exports `router`, prefix `/api/v1/notifications`;
  also `async def run_digests(factory, now)` and `async def deliver_due(factory, now)`), section4 `section4/src/harness_orchestrator/notify.py` with
  `async def emit(db, *, team_id, user_id, kind, source_kind, source_id, run_id=None, session_id=None, workspace_id=None, urgent=False, dedupe_key=None, summary_code=None)`
  (raw SQL, NO routes; it applies the recipient's preferences and inserts the inbox row; never raises into the caller; never stores message content, only ids and a
  closed `summary_code`), tests `section1/tests/test_notifications*.py` and `section4/tests/unit/test_notify*.py`, `docs/notifications.md`, `evidence/s5/lane-n.md`.
  N may also add ONE `notify.emit` call (try/except around it, same transaction style as the neighbours) at each existing source: approval requested, handoff offered/decided,
  run failed/blocked in `collab.py`/`service.py` — N is the only lane that edits those call sites this session, and only to add the call. N may make a minimal edit to `notifier.py`
  to hook `deliver_due`/`run_digests` into the existing worker loop if (and only if) it takes < 15 lines.
- **R** (section1 + section4, delivery only): migration `0047_realtime.sql` only if needed, `section1/src/harness_identity/eventbus.py` and the WebSocket handler region of `app.py`
  (R is the ONLY lane allowed to edit `app.py`, and only the WebSocket/ticket code; the lead mounts routers afterwards), section4 `signals.py` is off limits, new
  `section4/src/harness_orchestrator/delivery.py` (cursor/replay helpers, `router` only if a new route is truly needed), tests `section1/tests/test_realtime*.py` and
  `section4/tests/unit/test_delivery*.py`, `docs/realtime.md`, `evidence/s5/lane-r.md`. R also documents the client contract (cursor, dedupe by `(runId, seq)`,
  out-of-order buffering, backoff, polling fallback, schema version policy) so lane X can implement it.

Forbidden for all lanes: `models.py`, `audit.py` (Session 5 audit names are already reserved), `policy_enforce.py`, `capability-matrix.md`, `Makefile`, `deploy/`,
`openapi.json`/sdk files, anything under `section7/` (except lane X). Do not run docker, `make pg-*`, `run_smoke.py` or any Playwright e2e (the lead does).
Never `ruff check --fix`. Do not commit. Report paths, test counts, honest gaps. Use `text()` SQL, no new ORM models. Sqlite tests that spawn engine tasks must await
them before `engine.dispose()`.

## Reserved audit actions (section1 audit writer rejects unregistered names)
comment.{created,edited,deleted,hidden,restored,promoted,exported,purged}, discussion.settings_changed,
notification.{preferences_changed,muted,unmuted,snoozed,unsubscribed,digest_sent,delivery_failed}.
Section4 writes audit rows raw (see `workspace_gate._audit_refusal`); keep detail free of secrets and comment text (ids and before/after state only).

## Fixed interfaces between lanes
- `comments(id, scope_kind 'run'|'session', scope_id, team_id, author_user_id, author_name_snapshot, parent_id NULL, body, state 'visible'|'hidden'|'deleted', ref_kind NULL, ref_id NULL, ref_pos NULL, created_at, edited_at NULL, version)`
  (D owns; plus `comment_revisions`, `comment_reactions`, `comment_mentions(comment_id, mentioned_user_id)`, `discussion_settings(team_id PK, edit_window_seconds, retention_days, ...)`).
  D calls `notify.emit(kind='mention', ...)` for each valid mention in the same transaction. D must not import N's section1 code; N's `notify.py` lives in section4.
- `notification_inbox(id, team_id, user_id, kind, source_kind, source_id, run_id, session_id, workspace_id, urgent, dedupe_key, group_key, count, summary_code,
  state 'unread'|'read'|'snoozed'|'dismissed', snoozed_until, created_at, delivery JSON)` (N owns). Preview text is derived at read time from `summary_code` + ids and
  never contains comment/session content by default (directive 47.006).
- Event delivery contract (R): every collab event is addressed `(runId, seq)` with a strictly increasing per-run `seq`; `GET` replay with `since=<seq>` returns events with
  `seq > since` in order; ephemeral presence is NOT in the durable stream.

## Paths each lane serves (already classified by the lead; use exactly these)
- D, prefix `/api/v1/orchestrator`, for `{scope}` in `runs`/`sessions`: `GET|POST {scope}/{id}/comments`, `PATCH|DELETE {scope}/{id}/comments/{commentId}`,
  `GET {scope}/{id}/comments/{commentId}/history`, `POST {scope}/{id}/comments/{commentId}/(reactions|promote|moderate)`, `GET {scope}/{id}/comments/export`,
  `GET discussions/search?q=`, `GET|PUT discussion-settings`.
- N, prefix `/api/v1/notifications`: `GET ""` (cursor paged inbox, `unread` filter), `GET /unread-count`, `POST /{id}/(read|snooze|dismiss)`, `POST /read-all`,
  `GET|PUT /preferences`, `GET|PUT /preferences/scopes/{scopeKind}/{scopeId}`, `POST /mute`, `DELETE /mute/{muteId}`, `GET /link/{id}` (re-checks authorization,
  returns the target path or 404), `POST /unsubscribe` (token body).
- R: no new public paths unless a gap makes one unavoidable; report it in the evidence file instead of inventing it.
If a directive needs a path outside these, say so in the evidence file.

## Requirements common to every lane
Deny by default; team-scoped reads; foreign or unknown resources answer 404 with the same envelope (no existence oracle); never trust client-supplied identity
(use the principal); idempotent retries where a client may repeat (`requestId`); every refusal a user can hit is a stable error code; docs in `docs/` describe behaviour
as built including limits. Tests run on sqlite with the real migration SQL (strip nothing the migration needs; avoid `ADD COLUMN IF NOT EXISTS`). Add tests that FAIL when
the invariant breaks (wrong team, wrong role, stale version, races, fake clock).

## Per-lane acceptance (nine-out-of-ten bar)
- **D / part 43**: 001-010. Comments on sessions and runs; replies (bounded depth), mentions limited to people who can already see the scope (a mention never reveals
  a private session title or content to a non-member, 008), reactions (closed set), references to plan/file/diff/approval/event position that are re-authorized on
  read; discussion is never agent input unless `promote` (explicit action by a user with `steer`, writes a normal guidance instruction through the same path as
  `collab.redirect`/control queue, and records the link, 004/005); authorship, revision history, timestamps, moderation state visible (006); documented edit window and
  deletion = tombstone that keeps revisions and audit for audit-sensitive scopes (007); search only over authorized scopes (009); retention days and export (own scope,
  `export` permission, redacted of other teams) (010).
- **N / part 47**: 001-010. In-app inbox for approvals, mentions, handoffs, failures; email only via explicit opt-in preference (default off for non-urgent);
  per-event, per-session, per-workspace settings with documented precedence; quiet hours (user time zone) with urgent bypass policy; digests (`run_digests`);
  dedupe by `dedupe_key` and grouping of bursts (`count`); no sensitive preview by default; `GET /link/{id}` re-checks authorization; delivery state visible;
  mute/snooze/unsubscribe keep audit history; defaults documented as accessible and attention-respecting (no sound, no auto-popups, badge counts only).
- **R / part 46**: 001-011. Transport/guarantee decision recorded from the existing architecture; durable cursors and replay for collab events; documented and tested
  handling of duplicate/delayed/missing/out-of-order events (server helpers where the server can help; client rules documented); polling fallback documented and its
  server side tested (cursor paging, no gaps); backpressure and rate limits on the socket and on mutation routes (stable `RATE_LIMITED` / slow-consumer close code);
  ephemeral vs critical separation verified; schema version field and compatibility rules; stale-client mutation protection (mutations that carry
  `expectedVersion`/cursor are refused with a stable 409 `STALE_CLIENT_STATE` when behind — only where a server-side version exists, otherwise document); metrics for
  delivery lag, reconnects and recovered gaps (closed-label Prometheus counters/histograms registered with the existing domain-metrics module style); authenticated,
  scoped subscriptions (a socket can only receive runs of its team; revoked access closes the socket).
- **X / UI (lane run later by the lead)**: comments panel with edit/delete/react/promote, notification bell + inbox + preferences, connection status with backoff and
  polling fallback, keyboard-only approve/redirect/handoff tests, throttled aria-live, reduced motion, locale-aware dates/numbers, long-history navigation, `docs/accessibility.md`.
