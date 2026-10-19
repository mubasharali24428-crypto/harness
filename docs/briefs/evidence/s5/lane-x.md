# Session 5, lane X: UI for parts 43, 46, 47 and 56 (collaboration UI)

Contract: `docs/briefs/path-to-9-session5-prompt.md` (X / UI). Behaviour described in `docs/accessibility.md`; backend contracts in
`docs/discussion.md`, `docs/notifications.md`, `docs/realtime.md` (section 5). Not committed, not pushed. No docker, `make`, `run_smoke.py`
or Playwright run. Nothing outside `section7/` and the two named docs was touched.

## Files

New, `section7/app/projects/[projectId]/runs/[runId]/` (same module style as controlApi / controlSection):

| File | Role |
|---|---|
| `realtimeCore.ts` | pure client rules of realtime.md 5: `FrameReducer` (port of `delivery.Reducer`), `parseFrame`, `normalizeHistory` (old and new history shape), backoff with equal jitter, poll interval, close-code policy, status text, status announcer, stale-state / 429 helpers |
| `collabStream.ts` | framework-free controller: ticket, socket, cursor, backfill on gap / truncated / `cursorAhead`, reconnect, polling fallback, offline, hidden tab, schema flag, access lost |
| `useCollabStream.ts` | React adapter (real WebSocket / fetch / online / visibility wiring) |
| `connectionStatus.tsx`, `historyLog.tsx` | visible status indicator; windowed history with Load earlier / Jump to latest |
| `discussionApi.ts`, `discussionModel.ts`, `discussionView.tsx`, `discussionSection.tsx` | part 43: API + error text, pure view-model, presentational panel, container |
| tests | `realtimeCore.test.ts`, `collabStream.test.ts`, `discussion.test.tsx`, `keyboard.test.tsx` |

New, other: `section7/app/notifications/` (`notificationsApi.ts`, `notificationsModel.ts`, `openNotification.ts`, `NotificationBell.tsx`, `inboxView.tsx`,
`inboxScreen.tsx`, `page.tsx`, `unsubscribe/page.tsx`, `notifications.test.tsx`); `section7/app/settings/notifications/` (`prefsView.tsx`, `prefsScreen.tsx`,
`page.tsx`); `section7/src/lib/a11y.ts` (+ `a11y.test.ts`), `src/lib/intl.ts`, `src/components/LiveAnnouncer.tsx`; `section7/tests/support/elementTree.ts`
(test driver, not collected as a test); `docs/accessibility.md`; this file.

Changed (minimal): `collabApi.ts` (429 keeps `Retry-After` on the error), `collabModel.ts` (comment event lines, two pure announcement helpers),
`sharedRunPanel.tsx` (stream hook instead of wake-up socket + history poll, one announcer, history log, discussion slot, connection indicator,
stale / 429 handling, focus parking, Ctrl+Enter), `controlSection.tsx`, `presenceSection.tsx`, `agentTree.tsx` (focus parking; their live regions route
through the shared announcer when inside the panel), `src/components/AppShell.tsx` (bell), `src/lib/focus.ts` (`parkFocus`), `app/settings/page.tsx` (link),
`app/globals.css` (reduced-motion block). `collab.test.tsx`: **one assertion changed** (see "Existing behaviour changed"). `openCollabSocket` in
`collabApi.ts` and its test are untouched and now unused by the panel (kept so its test stays green; delete both when convenient).

## Directive status

"Done" = implemented and covered by a test that fails when the behaviour breaks (see "Mutation check"). "Static" = checked by static render only.

### Part 43 (discussion UI)

| Directive | Status |
|---|---|
| 001 comments are not agent input | Done. Panel says so; the only path to the agent is the explicit promote confirmation. No code path posts a comment as guidance |
| 002 replies, reactions, mentions | Done. Threads built from `parentId`/`depth`, indentation capped at `policy.maxReplyDepth`, orphan replies shown as roots; reactions are the API's closed set (`aria-pressed` toggles with counts); mentions are explicit `@userId` of roster participants (never yourself, max 10), previewed under the box. `ignoredMentions` is deliberately not shown (no oracle) |
| 003 references | Display only: available refs shown (`kind id, line n`), unavailable refs say so and show nothing else. **Creating** a reference from the UI is out of scope (needs plan / file / diff / approval pickers); the API client accepts none |
| 004, 005 promote | Done. Button only when `canSteer` and `can.promote`; opening only opens a confirmation naming the version, "cannot be taken back" and the control-lease queueing; only the confirm button calls the API; result sentence says sent / queued / already sent |
| 006 authorship, delete, moderation | Done. Author name snapshot, relative time with full date in `title`, edited marker, tombstone ("This comment was deleted" with who), hidden / purged placeholders, moderation line with who and why. Admin Moderate form (hide / restore / delete, reason of at least 3 characters) is implemented; no moderation queue |
| 007 edit window, history | Done. Edit shown only when the server's `can.edit` is true, deadline printed from `editableUntil`; `expectedVersion` sent; history viewer (original / versions, "You cannot see this text" when redacted) |
| 008 mentions without leaks | Done (client side): candidates are only the roster; no id lookup |
| 009 search | Done. Search landmark, 2-character minimum, snippets as plain text, "Show in thread" only for loaded comments of this scope, cursor paging. Hits in other scopes are text only (the client cannot derive a project for a link) |
| 010 export, retention | Export: button shown to admin / engineer only, fetches with the bearer token and saves JSON (a plain link cannot carry the token). Retention: purged comments show the placeholder. **Discussion settings screen (`discussion-settings`) is out of scope**: admin API only, no UI in this lane |
| Session scope | No session page exists in section7. `DiscussionSection` accepts `{kind:"session", id}` (polls 15 s, announces new comments) and is documented; only the run scope is wired and exercised |

### Part 46 (real-time, client contract 5.x)

| Rule | Status |
|---|---|
| 5.1 cursor per run, in memory, dedupe by (runId, seq), 256-frame buffer | Done (`FrameReducer`). Replays the 6 reducer cases of `test_delivery_reducer.py` plus the 30-seed loss / duplicate / swap convergence test against an in-memory log with holes |
| 5.2 apply / gap / backfill (page 200 while hasMore), `replayTruncated`, `cursorAhead` reset, unknown kind applied, unknown control ignored | Done and tested at controller level, including the older history shape |
| 5.3 status values, text not colour | Done: connecting, catching up, live, reconnecting, polling (paused), offline, access lost, plus an "update required" flag shown on top. The lead's four words map to: connected = Live, reconnecting, polling fallback = "Live updates paused", stale = "update required" (server newer schema) and the stale-mutation message below |
| 5.3 backoff 1,2,4..30 s with equal jitter, reset only after 30 s live, close-code floors, 3x 4408 in 5 minutes, 4429 `retryAfterSeconds`, 4401 / 4403 once, 4404 / 4410 stop | Done and tested with fake timers |
| 5.3 polling 3 s +-20% / 20 s safety / 30 s hidden / resume on visible, 429 waits `Retry-After`, 403 / 404 stop | Done (`Retry-After` is read from the header only in `collabApi.call`) |
| 5.3 accessibility announcements (5 s rule, once, no retries) | Done (`createStatusAnnouncer`, fake-timer tests) |
| 5.4 stale 409 codes, re-read, never auto-resubmit; 429 disables controls | Done for the shared panel and comments / notifications mutations (`isStale`, `mutationError`, `cooldownSeconds`). `STALE_CLIENT_STATE` itself is not emitted by any route yet (realtime.md says so), so the generic code is tested at unit level only |
| 5.5 `schemaVersion` sent, newer frame keeps its place, "reload" shown | Done |

### Part 47 (notifications UI)

| Directive | Status |
|---|---|
| 001 inbox | Done. Cursor paging (`nextCursor`), filter (active / unread / read / snoozed / dismissed), type filter, mark read, snooze (1 h, 3 h, tomorrow, next week), dismiss, mark all read (follows the type filter) |
| 002, 003 email opt-in, scopes | Done. Global / workspace / session scopes (workspace and session need an id typed in: no list endpoint exists), per-event table with "Result now" from `effective`, email default shown as "Never (default)". Saves a merge-patch of changed fields only, with `expectedVersion`; a conflict reloads the server copy |
| 004 quiet hours, digest, urgent | Done. Quiet hours with start / end / IANA time zone (validated like the API), digest period / hour, text says quiet hours hold email only and urgent bypasses |
| 005 dedupe, grouping | Display: a burst shows its count in the fixed plural sentence |
| 006 no sensitive content | Done. Sentence derived client-side from the closed `summaryCode` (13 codes, parity with the server's table asserted by a test that reads `notifications.py`), unknown code is generic, server `preview` text is never rendered |
| 007 links | Done. Only through `GET /link/{id}`; 404 reads "You can no longer open this ..."; only same-site absolute paths to pages this app has are navigated (sessions / workspaces say the page does not exist yet); `?open=<id>` from the email is resolved the same way |
| 008 delivery state | Done. In-app always; email state in words only when not `none` |
| 009 mute, snooze, dismiss, unsubscribe | Done. Mutes listed with Unmute, add mute (type / run / session / workspace, duration); `/notifications/unsubscribe?token=` page confirms with a button and sends no Authorization header |
| 010 attention-respecting defaults | Done. Bell is a plain link with the count in its accessible name, badge only, no sound / popup / focus (a test scans the source for audio, `Notification`, `alert`, `autoFocus`, `.focus(` in the bell), hidden tab does not poll, new items announced politely and throttled, urgency is the word "Urgent" |
| Placement | Bell in the sidebar (`AppShell`, renders nothing without a session); inbox at `/notifications`; settings at `/settings/notifications` with a link from `/settings` |

### Part 56 (collaboration UI)

Keyboard operation, focus management, announcements, reduced motion, locale formatting and long-history navigation: see `docs/accessibility.md`
(sections 1 to 6). Approve / reject / apply, handoff offer / accept / decline, direction box, suggestion accept / reject, control and agent buttons,
comment forms and the notification inbox are native controls with names, call the right handler and park focus first; tested at handler level.

## Existing behaviour changed

* `sharedRunPanel.tsx`: events no longer come from `getHistory` inside `refresh`; the stream delivers them. `refresh` re-reads roster and approvals on every
  delivered batch (coalesced 250 ms) and on the safety poll (3 s not live, 20 s live, as before).
* `<section aria-label="Pending decisions" aria-live="polite">` lost its `aria-live` (it re-read the whole list, diff included, on every change).
  `collab.test.tsx` line 280 asserted the old attribute and now asserts it is absent. Approvals are announced through the shared region instead.
* The history list is now a windowed `HistoryLog`: still `role="log"` named "Collaboration history" with one `<li>` per event; its buttons are outside the list
  (the multiplayer e2e reads `history.locator("li")`). Each `<li>` now has `tabindex=-1`.
* The shared run `Card` has `aria-label="Shared run"`, `tabindex=-1`, `data-focus-park`.

## Commands run (all from `section7/`) and results

| Command | Result |
|---|---|
| `npx vitest run` (before any change) | 61 files, 572 passed |
| `npx tsc --noEmit` | exit 0, no output |
| `npx vitest run` (final) | **67 files, 816 passed** (244 new) |
| per new file | `realtimeCore.test.ts` 67, `collabStream.test.ts` 25, `discussion.test.tsx` 52, `keyboard.test.tsx` 33, `notifications.test.tsx` 50, `src/lib/a11y.test.ts` 17 |
| mutation check (script run by hand, not committed) | 22 one-line mutations of the new code (dedupe off by one, prevSeq ignored, floor dropped, `control=1` dropped, 4410 not terminal, hasMore ignored, backoff never reset, promote without confirmation, promote without `canSteer`, orphan replies dropped, focus parking removed from Approve, Ctrl+Enter check removed, announcer coalescing removed, duplicate suppression removed, `//` and backslash paths allowed, unknown code not generic, "Urgent" word removed, unchanged email always sent, `expectedVersion` dropped, `parkFocus` emptied, locale ignored): **all 22 failed the suite; 0 survived** (2 first-pass mutations missed their target text: one duplicated another and was dropped, the other was redone as two mutations) |

ESLint: **not run, none exists** (no config and no `eslint` in `section7/node_modules/.bin`). `next build` and Playwright: **not run** (the lead owns them; a build
would also overwrite `.next`). No axe run, no screen reader, no real-browser keyboard test (see `docs/accessibility.md` section 10). No Playwright spec was added.

## What the lead must wire or check

1. **Orchestrator discussion router:** `deploy/runners/services.py` (lines 456-465) already calls `discussion.configure_discussion(service)` and includes `discussion.router`
   (`section4/api.py` itself does not mention it). I did not run the services, so whether the e2e stack serves it is unchecked. If it does not, every run page requests
   `GET .../runs/{id}/comments`, gets 404, and the multiplayer e2e counts any response >= 400 as a fault; the panel then shows "Discussion is not available right now" instead of a form
   (and re-polls every 15 s).
2. **Identity: `notifications.router`** is already included in `section1/.../app.py`; `AppShell` now polls `GET /api/v1/notifications/unread-count` every 60 s on every page for a signed-in
   user, so a missing migration 0046 would show up as 4xx / 5xx in the e2e fault list.
3. `collab_history` route: the client accepts the old `{events}` shape and the enriched one (`hasMore`, `prevSeq`, `nextCursor`). Wiring `delivery.history_page` (lane R item 2) turns
   on gap proof by `prevSeq`; until then ordering is by cursor only.
4. `docs/realtime.md` section 9 last bullet ("the browser client today treats socket frames only as a wake-up") is now outdated: the panel implements section 5. Not edited (not my file).
5. e2e selectors checked by reading, not by running: `getByRole("log", {name:"Collaboration history"})`, `getByLabel("Direction for the agent")`, `getByLabel("Proposed change")`,
   `getByTestId("shared-run-panel")`, suggestion `Accept`, handoff `Accept` (exact). New buttons (Reply, Delete, reaction names, Search ...) were checked not to collide with the ones those specs use.

## Honest gaps

* Containers with effects have **no automated test**: `SharedRunPanel`, `useCollabStream`, `DiscussionSection` (focus return, polling, download), `InboxScreen`, `PrefsScreen`, `NotificationBell`
  (the node environment does not run effects). Their logic was pulled into pure functions / controllers that are tested; the glue is checked by `tsc` only. A browser run is needed.
* The element-tree driver proves wiring and focus-call order, not browser focus, Tab order, focus rings or real key events. No contrast, zoom or screen-reader check.
* History: every event is kept in memory, first load pages forward in 200s; "Load earlier" is a render window (the API cannot page backwards). No virtualization.
* The 429 `Retry-After` header is kept only by `collabApi.call` (shared panel, comments, notifications); other modules using `apiFetch` do not read it. History polling uses 30 s when absent.
* Reference creation, a discussion settings screen, an admin moderation queue, `@name` resolution / autocomplete (ids only), workspace / session pickers for notification scopes
  and mutes (ids typed in), and a session page are not built.
* Notification links to `/sessions/..` and `/workspaces/..` have no destination page in this app. The bell lives in the sidebar, so it is behind the drawer at 900 px and below.
* English strings only; `<html lang="en">` is fixed; only dates, times, numbers and relative times follow the locale. The pre-existing presence / control sections format "about N min" without `Intl`.
* `openCollabSocket` (wake-up only) is dead code kept for its test.
