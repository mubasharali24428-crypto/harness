# Notifications and user control (master prompt part 47)

Code: `section4/src/harness_orchestrator/notify.py` (producer, `emit`), `section1/src/harness_identity/notifications.py`
(router `/api/v1/notifications/...`, `deliver_due`, `run_digests`), migration
`section1/migrations/0046_notifications.sql`, worker hook in `section1/src/harness_identity/notifier.py`
(`NotificationWorker._loop`). Covered by `section1/tests/test_notifications_{inbox,prefs,delivery}.py` and
`section4/tests/unit/test_notify.py`, `test_notify_sources.py`. Everything below is behaviour as built.

## What it is (001)
A person's **inbox** (`notification_inbox`) holds one row per notification or per burst. A row stores ids, a
flag and a closed `summary_code`, never text from a comment, plan, diff, session title or run. The words a
person sees are derived at read time from the code (`PREVIEWS` in `notifications.py`). Rows are written only
by `notify.emit`; the API only reads them and changes the person's own handling state.

### Closed set of `kind` values and summary codes
| kind | summary codes (first = default) | who is told today |
|---|---|---|
| `approval` | `approval_requested`, `approval_decided` | the run leader when a high-impact approval is requested (`collab.request_approval`, urgent) |
| `mention` | `mentioned` | lane D (discussion) calls `emit(kind='mention')` per valid mention; not wired by this lane |
| `handoff` | `handoff_offered`, `handoff_accepted`, `handoff_rejected` | the recipient when offered, the proposer when decided (`collab.request_handoff`, `decide_handoff`) |
| `failure` | `run_failed`, `review_failed` | the run's leader and participants on `engine_failed` / `review_failed` (`service._emit`) |
| `blocked` | `run_blocked` | the run's leader and participants on `blocked_awaiting_clarification` (`service._emit`) |
| `control_request` | `control_requested`, `control_granted`, `control_declined` | **no call site yet** (control.py is not this lane's file) |
| `suggestion` | `suggestion_queued` | **no call site yet** (queued guidance for the lease holder, control.py) |
| `budget` | `budget_warning`, `budget_exhausted`, `plan_limit_reached` | the team's billing managers when spend first crosses the warning threshold, when the monthly cap blocks a run, and when the daily run cap blocks one (once per day), never for a degraded denial; an org manager outside the team gets it in their first other org team (`budget.deliver_budget_warning`, part 57; team-scoped, no run link, `source_kind='team'`) |

An unknown `kind` is rejected; a `summary_code` that is not in the kind's list is replaced by the kind's
default. Adding a kind needs a row here, codes in `SUMMARY_CODES` (both sections) and a `PREVIEWS` entry.
Observers are not told about run failures; the person who caused an action is never told about it.

## `notify.emit` (producer contract)
```
async def emit(db, *, team_id, user_id, kind, source_kind, source_id, run_id=None, session_id=None,
               workspace_id=None, urgent=False, dedupe_key=None, summary_code=None) -> dict
```
* Raw SQL on the caller's session, **no commit**: the row is atomic with the caller's change. It runs in a
  SAVEPOINT, so a failure inside `emit` cannot poison the caller's transaction (verified on PostgreSQL 18,
  where one failed statement otherwise aborts everything).
* **Never raises.** Result is a dict: `created`, `grouped` (merged into a burst row), `duplicate`,
  `suppressed` (`inapp_off` or `muted`), `rejected` (`bad_kind`, `bad_id`, `bad_dedupe_key`, `not_in_team`,
  `foreign_scope`) or `error` (logged).
* **Same team only.** The recipient must be a `team_members` row of `team_id`; `run_id`, `session_id` and
  `workspace_id` must belong to the team (unknown or foreign: `foreign_scope`). The session and workspace a
  run lives in are filled in so per-session and per-workspace settings apply to run events.
* Writes ids and the closed code only: `source_kind` (<= 32 chars) and `source_id` (<= 80) are ids, there is
  no parameter that can carry text.
* `emit` does not check that the recipient can *see* the scope. The caller picks the recipient (lane D limits
  mentions to people who can see the scope); opening the link re-checks (below).

## Order of decisions in `emit`
1. validate, 2. recipient and scope in the team, 3. resolve preferences (precedence below), 4. in-app `off`
for this event: suppressed, 5. a live mute on the run, session, workspace or kind: suppressed unless
`urgent`, 6. dedupe, 7. grouping, 8. insert, 9. decide the email channel (queue only, nothing is sent here).

## Preferences and precedence (003)
Rows in `notification_preferences`: one **global** row per person plus optional **workspace** and **session**
rows. Each carries `inapp` (`on`/`off`), `email` (`off`/`urgent`/`immediate`/`digest`) and per-kind `events`
(`{kind: {inapp?, email?}}`). Unset means inherit. Built-in defaults: in-app **on**, email **off**.

Most specific wins: `session event > session default > workspace event > workspace default > global event >
global default > built-in`. So a workspace default `off` beats a global per-event `immediate`, and inside one
scope the per-event entry beats that scope's default. The same rule is implemented in section4 `resolve` and
section1 `resolve` and pinned by one shared table in both test suites. `GET /preferences` and
`GET /preferences/scopes/...` return `effective` per kind. Only the global row holds `timezone`, quiet hours
and the digest schedule.

## Email: explicit opt-in only (002)
The default for every kind, urgent included, is **no email**. A person opts in by setting `email` to
`urgent` (only urgent events), `immediate` or `digest` (global, per workspace/session or per kind). Nothing
else sends mail; `notify.emit` only queues a row (`email_state='pending'` or `'digest'`), and
`deliver_due` / `run_digests` send. At send time the setting is checked **again**: opting out (or
unsubscribing) after queueing cancels the mail, a switch to `digest` moves the row into the digest, a row that
was read, dismissed or is snoozed is not mailed. Mail goes to `users.email` of an active team member; none:
skipped (`no_recipient`).

## Quiet hours, digests, urgent escalation (004)
* **Quiet hours** (global row): `quietHours {enabled, start, end}` as `HH:MM` wall-clock in the person's
  `timezone` (IANA name), start inclusive, end exclusive, may wrap midnight. They hold **email** only: a
  non-urgent mail due inside the window is delayed to the end of the window (`reason: quiet_hours`, `dueAt`).
  The inbox is silent by design, so quiet hours do not change it. Judged at queue time (emit) and again at
  send time (deliver_due). An unknown or unavailable zone falls back to UTC; the PUT route refuses unknown
  zones (422 `INVALID_TIMEZONE`). start == end is refused (422 `INVALID_QUIET_HOURS`).
* **Urgent escalation policy** (`urgent=True` at the source): bypasses quiet hours, bypasses digest batching
  (it is mailed immediately when the person has any email mode other than `off`), bypasses a mute (it still
  only appears in the inbox), is never merged into a calm burst. It does **not** override an explicit
  in-app `off` or `email off`. Today only `approval.requested` is urgent (it expires and blocks the run);
  failures, blocks and handoffs are not.
* **Digest** (`run_digests(factory, now)`): one email per person listing still-unread digest items as fixed
  sentences (max 20, then "and N more"). Due when the person's local time is past `digest.hour` (default 8),
  the last digest is from an earlier local day (`daily`) or at least 7 days old (`weekly`), and it is not
  inside quiet hours. Handled items are skipped. Nothing pending: no email.
* `deliver_due(factory, now)` and `run_digests(factory, now)` take the clock as a parameter. They are called
  from `NotificationWorker._loop` (every poll, default 30 s) after the existing outbox drain; their failures
  are logged and never stop the loop. Retries: 3 attempts, immediate mail backs off 60 s, 120 s; digest 300 s,
  600 s; then `failed` + audit `notification.delivery_failed`. Success of a digest: audit
  `notification.digest_sent` with count and kinds.
* Mail text is the fixed preview sentence, a link `{HARNESS_APP_URL}/notifications?open={id}` and an
  unsubscribe link. `HARNESS_APP_URL` is read from the environment (empty means relative links).

## Dedupe and grouping (005)
* `dedupe_key` (<= 120 chars): the first `emit` per (team, person, key) wins, repeats return `duplicate` and
  change nothing (atomic `INSERT ... ON CONFLICT DO NOTHING` on `notification_dedupe`). A key is permanent: pick a
  key that names the event (`approval:<id>`, `handoff:<id>:offered`). A retry of an event that was folded into
  a burst is also a no-op. Dismissing a row does not release its key.
* Grouping: an event with the same `group_key` = `kind : source_kind : (run | session | workspace | source id) :
  urgent flag` as the person's **unread** row updated within `HARNESS_NOTIFY_GROUP_SECONDS` (default 600)
  increments `count`, refreshes `updated_at` and `source_id` and sends no second email. A read, snoozed or
  dismissed row, an older one, another run, another kind or another urgency starts a new row. Two truly
  simultaneous first events can create two rows (no lock): accepted, see gaps. The `budget` kind adds its summary
  code to the group key, so a fresh unread warning never absorbs the hard-stop alert (`budget_exhausted`).

## No sensitive content (006)
Nothing but ids and the closed code is stored (`test_created_row_holds_ids_and_a_closed_code_only`,
`test_preview_is_fixed_text_never_content`, `test_mail_holds_no_content...`). API previews, email subjects and
bodies are fixed sentences ("An approval needs your decision"); they never name a person, session, run, file or
comment. There is no "show details" preference: richer previews are not offered at all, which is stricter than
"off by default". Audit rows hold ids and before/after setting values, never notification text.

## Links re-check authorization (007)
`GET /api/v1/notifications/link/{id}` returns `{notificationId, path}` or **404**, on every call. It re-reads
the notification (must be the caller's own, same team) and re-checks the run (team), the session (the
`sessions` visibility rules: active member or team admin, not deleted) and the workspace (the `workspaces`
rules: private needs membership). Losing any of them gives the same 404 envelope as an unknown id; the inbox
row itself stays (history). Paths: run `/projects/{projectId}/runs/{runId}`, session `/sessions/{id}`,
workspace `/workspaces/{id}`, else `/settings/billing` for the `budget` kind (the page opens for the team the person
is signed in to, so an org manager outside the capped team sees their own team's billing) and `/notifications` for anything else; for approval / comment / handoff / suggestion sources a
`?focus={sourceKind}:{sourceId}` query is added. Clients must open notifications through this route, never
build the path from ids themselves. The emailed link goes to `/notifications?open={id}`, which the client
resolves through this route after login.

## Delivery state (008)
Every item carries `delivery`: `{"inapp": "delivered", "email": {state, reason, attempts, dueAt,
lastAttemptAt}}`. Email `state`: `none` (not opted in), `pending` (waiting, `dueAt`, `reason` such as
`immediate`, `quiet_hours`, `urgent_bypass`, `retrying`), `digest`, `sent`, `failed`, `skipped` (`reason`:
`opted_out`, `handled_in_app`, `no_recipient`, `unsubscribed`). The UI should show email state only for
non-`none` states.

## Mute, snooze, dismiss, unsubscribe keep history (009)
* **Snooze** (`POST /{id}/snooze {minutes | until}`, at most 30 days): the item leaves the list and the badge
  and returns as unread when the time passes (computed at read time, nothing to run). Audit
  `notification.snoozed`.
* **Dismiss**: state `dismissed`, hidden from the default list (`?state=dismissed` shows it). **Read** /
  **read-all** set `read`. Rows are never deleted by the product. Dismissed items cannot be snoozed (409
  `ILLEGAL_STATE`).
* **Mute** (`POST /mute {scopeKind: workspace|session|run|kind, scopeId, minutes|until?}`): silences
  non-urgent events for that scope or kind (none of them reach the inbox while it is live). The scope must be
  visible to the caller (404 otherwise). A repeat is idempotent (200, same id). `DELETE /mute/{id}` **ends**
  the mute (`ended_at`), the row stays; repeating it is a no-op. A timed mute that ran out is closed the next
  time the person mutes the scope. Audit `notification.muted` / `notification.unmuted` (muted again with a new
  time audits `muted` again).
* **Unsubscribe** (`POST /unsubscribe {token}`, no login): the link in each email carries a random token
  (`notification_unsub_tokens`, only its SHA-256 is stored, valid 90 days). First use switches the email
  channel off for that person (global `email=off`, their own per-scope and per-kind email overrides cleared,
  queued mail skipped) and audits `notification.unsubscribed`. **Single use with an idempotent answer**: a repeat
  answers 200 `alreadyApplied: true` and changes nothing, so an old email can never switch off a channel the
  person has re-enabled. Unknown or expired token: 404.
* **Preference changes** audit `notification.preferences_changed` with scope and the before/after of only the
  changed fields; an identical PUT writes nothing (no new version, no audit).

## Accessible, attention-respecting defaults (010)
The server defines no sound, no pop-up and no auto-open anywhere: the only signal is `GET /unread-count`
(`unread`, `urgentUnread`) for a badge. Defaults: in-app on, email off, no quiet-hours-only behaviour needed
because nothing interrupts. Rules for the UI (lane X): show a badge count only, never open a panel or a toast
by itself, announce new items in a polite, throttled live region, never encode urgency by colour alone (the
item has `urgent: true` and the word "urgent" must be shown), keep the inbox reachable by keyboard, use the
person's locale for dates, and show `preview` text unchanged (it is the accessible name).

## Routes (all under `/api/v1/notifications`, policy permission `read`, own data only)
| Route | Behaviour |
|---|---|
| `GET ""` | cursor paged (`limit` 1-100 default 30, `cursor`), filters `unread`, `kind`, `state`; order `updated_at` desc; `{items, nextCursor}` |
| `GET /unread-count` | `{unread, urgentUnread}` (snoozed items whose time passed count) |
| `POST /{id}/read`, `/dismiss`, `/snooze` | the item; foreign or unknown id: 404 `NOT_FOUND` |
| `POST /read-all` | optional `{kind}`; `{updated}` |
| `GET\|PUT /preferences` | global row, `effective`, live `mutes`, the `kinds` list; PUT is a merge-patch with optional `expectedVersion` (409 `VERSION_CONFLICT`); unknown fields 422 |
| `GET\|PUT /preferences/scopes/{workspace\|session}/{id}` | per-scope row and `effective`; scope must be visible (404); only `inapp`, `email`, `events` |
| `POST /mute`, `DELETE /mute/{id}` | above |
| `GET /link/{id}` | above |
| `POST /unsubscribe` | above; the only route without a session (the token is the credential) |

Error codes: `NOT_FOUND` (404), `VALIDATION_ERROR`, `INVALID_TIMEZONE`, `INVALID_QUIET_HOURS` (422),
`VERSION_CONFLICT`, `ILLEGAL_STATE`, `MUTE_CONFLICT` (409).

## Tables (migration 0046)
`notification_inbox` (the contract columns plus `updated_at`, `read_at`, `email_state`, `email_due_at`,
`email_attempts`), `notification_preferences`, `notification_mutes`, `notification_dedupe`,
`notification_unsub_tokens`. JSON values are TEXT. No `ADD COLUMN IF NOT EXISTS`; applied on PostgreSQL 18.4
and on SQLite in the tests. Rollback: drop the five tables (product data only).

## Not built / limits
* No retention or pruning of `notification_inbox` and `notification_dedupe` (rows are small, never deleted).
* No channel other than in-app and email (no push, chat or webhook).
* Previews are English only (the `summaryCode` is stable for client-side localization).
* Two simultaneous first events of one burst can produce two rows; a mute created while an event is being
  written may let that one event through.
* No per-route rate limit beyond the global middleware; the unsubscribe token is 256 bits.
* No Prometheus metrics for notifications (domain_metrics is not this lane's file).
* `control_request` and `suggestion` have no call site yet.
* `budget` rows link to `/notifications` (no run, session or workspace) and name no amount: the preview is
  "Team spend is nearing its monthly limit" (warning) or "Team spend reached its monthly limit" (hard stop).
  A dedupe key per month and code means one row per person per team per month for each.
* Zones need tz data on the host (`zoneinfo`); add the `tzdata` package to slim images, otherwise saved zones
  fall back to UTC at emit time.
