# Accessibility of the collaboration UI (master prompt part 56, collaboration scope)

As built in session 5, lane X (`section7/`). This page describes what the code does and what has and has not been checked.
It is not a conformance claim: **no screen reader, real browser keyboard test or automated axe run was performed** (see
"Not covered"). Part 56 outside the collaboration UI (the older pages) is out of scope here.

Scope: the shared run panel (`app/projects/[projectId]/runs/[runId]/`), the discussion panel (part 43), the notification bell,
inbox and settings (part 47), and the real-time connection indicator (part 46).

## 1. Keyboard map

Everything below is a native element (`<button>`, `<select>`, `<input>`, `<textarea>`, `<a>`), so the browser
supplies Tab / Shift+Tab order, Enter and Space activation, and arrow keys in selects. There are no custom widgets with roving
tabindex, no `div role="button"`, and **no focus trap anywhere** (the older modal `trapFocus` is not used by these screens).

| Where | Keys | Effect |
|---|---|---|
| Whole app | Tab, Shift+Tab | document order; the skip link is the first stop (existing) |
| Approval card (leader) | Enter / Space on **Approve this change**, **Reject**, **Apply approved change** | decides / applies; focus moves to the card first |
| Approval diff | Tab, then arrows / PgUp / PgDn | the diff is a labelled, focusable scroll region (`aria-label="Proposed change"`, `tabindex=0`) |
| Handoff | Tab to **Hand off lead to**, arrows to choose, Enter on **Offer handoff**; recipient: Enter on **Accept** / **Decline** | focus is parked on the panel before the control disappears |
| Direction box | **Ctrl+Enter** (Cmd+Enter on a Mac) sends; plain Enter and Shift+Enter add a line | the hint is printed under the box |
| Suggestions waiting (holder) | Enter on **Accept** / **Reject** | each button is `aria-describedby` the suggestion text |
| Control, agents, conflicts | Enter / Space on the buttons | focus is parked on the surrounding region (`data-focus-park`) before the action |
| Team history | Tab into the log, arrows / PgUp / PgDn scroll it; **Load earlier**, **Jump to latest** are buttons outside the list | see section 6 |
| Comment | Tab through **Reply**, **Edit**, **Delete**, **Send as guidance**, **History**, **Moderate** and the reaction toggles | reactions are `aria-pressed` buttons |
| Inline comment forms (reply, edit, new) | **Ctrl+Enter** / Cmd+Enter sends, **Escape** cancels (not on the always-open new-comment box) | on open, focus goes into the field; on close it returns to the button that opened it |
| Confirmations (delete, send as guidance), history, moderation | **Escape** closes | the group takes focus when opened |
| Notification bell | Enter on the link | goes to `/notifications`; nothing opens by itself |
| Inbox | Enter on **Open**, **Mark read**, **Snooze** (then a duration; **Escape** closes the menu), **Dismiss**, **Mark all read**, **Load more** | each is `aria-describedby` its sentence |
| Settings, Notifications | standard form controls; radio group for scope; **Save settings** is the submit button, so Enter in a field submits | `<table>` has a caption and row headers |

## 2. Focus management

Rules, all implemented in code and unit-tested at handler level:

1. **Never move focus on a poll, a push or an announcement.** Focus only moves as the direct result of the person's own action.
2. **Before a control disappears or is disabled, focus moves to something that stays** (`parkFocus`, `src/lib/focus.ts`): the
   nearest ancestor marked `data-focus-park` (`tabindex="-1"`). Approval cards use their own `data-approval-card`. Used for approve /
   reject / apply, handoff, join / claim leader, suggestion accept / reject, control and queue buttons, agent pause / resume / cancel,
   conflict resolution, inbox mark read / dismiss / snooze / mark all.
3. **Opening an inline form moves focus into it; closing returns focus to the opener** (`DiscussionSection`, by `data-comment-action`).
   If the opener is gone (a deleted comment) focus lands on the comment's list item.
4. Disabled while a request is in flight, so a repeated Enter cannot submit twice.
5. Visible focus comes from the existing global `:focus-visible` outline (`app/globals.css`); nothing here removes outlines.

## 3. Announcements (one throttled live region per screen area)

Implementation: `src/lib/a11y.ts` (`createAnnouncer`, framework-free) and `src/components/LiveAnnouncer.tsx` (`useAnnouncer`,
`LiveRegions`, `AnnounceContext`). A screen area renders **one polite region (`role="status"`, `aria-live="polite"`, atomic) and
one assertive region (`role="alert"`)**, both visually hidden.

Rules (`createAnnouncer` defaults; tests use fake timers):

* **Polite:** the first message is spoken at once; everything that arrives in the next **4 s** is collected and spoken as one
  message ("5 updates. Latest: ...") when the window closes; at most one polite message per window, however long a burst lasts.
  The same sentence twice in a row counts once; at most 20 distinct messages are remembered per window (the rest are counted).
* **Assertive (urgent):** immediate, but at most one per **1 s** (the newest wins inside the gap). Used only for: a new approval
  request that **you** must decide, and "You no longer have access to this run".
* A live region speaks only when its text changes; repeating the same sentence appends a non-breaking space.

What is announced (and nothing else):

| Source | Message | Mode |
|---|---|---|
| New approval request | "Approval needed from you: ..." (you are the leader) / "New approval request: ..." | assertive / polite |
| Other people's fresh collaboration events (less than 60 s old) | the history line, clipped to 140 characters | polite |
| Presence, control and agent actions (existing sections) | their existing sentences, now routed through the shared region (they no longer own a second region inside the shared run panel) | polite |
| Connection | "Live updates are paused. The page keeps refreshing." only after 5 s without a socket; "Live updates are back." after a spell; "This page is out of date, reload ..." once; access lost (assertive) | see realtime.md 5.3 |
| New notifications | "N new notifications" when the unread count grows (never the first poll, never a drop) | polite |
| Inbox / settings actions | "Marked as read", "Snoozed", "Dismissed", "Settings saved", "Muted" ... | polite |
| New session comments | "New comment from X" (session scope only; run scope uses the event line) | polite |

Never announced: each reconnect attempt, polls that changed nothing, history catch-up, your own actions' echoes, notification
text beyond the fixed sentence, the content of other people's comments. The old `aria-live="polite"` that wrapped the whole
"Pending decisions" list was removed (it re-read the diff on every change); one existing test assertion was updated for that.

Limits: the bell, the inbox and the settings page each have their own region, so `/notifications` has two polite regions (bell and
inbox). They are both throttled, but the throttle is per region.

## 4. Reduced motion

`app/globals.css` has a `@media (prefers-reduced-motion: reduce)` block that makes transitions and animations effectively instant
and turns off smooth scrolling (it covers the sidebar drawer slide, the skip-link drop and the two progress-bar width transitions, which are inline styles that an `!important` rule overrides; these are the only transitions in the app).
Programmatic scrolling in the new code goes through `scrollBehavior()` (`src/lib/a11y.ts`), which returns `"auto"` when the
setting is on. The new UI has **no animation of its own**: the connection indicator is static text, not a pulsing dot.

## 5. Locale-aware dates and numbers

All new code formats through `src/lib/intl.ts` (`Intl.DateTimeFormat`, `Intl.NumberFormat`, `Intl.RelativeTimeFormat`), using the
browser's locale and the person's time zone unless a test pins them: comment times (relative text, full date in the `title`,
machine value in `<time dateTime>`), history times, notification times, counts, digest hours, mute deadlines. Unparseable input
renders as an em dash. There is no hard-coded `en-US` format in the new files (a test asserts the formatter honours the locale).
Quiet hours are entered as `HH:MM` in an `<input type="time">` and read by the server in the saved IANA time zone, chosen from
`Intl.supportedValuesOf("timeZone")` (UTC first) with a short fallback list.

Not localized: all other strings (see "Not covered"). `<html lang="en">` in `app/layout.tsx` is fixed.

## 6. Long histories

* The team history keeps every delivered event in memory and renders only the newest **100** (`HistoryLog`). **Load earlier (100 of N
  hidden)** widens the window by 100 at a time; it is a button outside the `role="log"` list.
* The log region is `tabindex=0` with an accessible name, so the keyboard can scroll it. It follows the end while you are at the end;
  if you scroll up and events arrive, **Jump to latest (N new)** appears. It scrolls (smoothly unless reduced motion is on) and
  focuses the last row.
* The "cursor" of docs/realtime.md is the stream's own `seq` cursor (forward only: `collab-history?after=`). The API cannot page
  **backwards**, so "load earlier" is a render window over events already loaded, not a server request. A run with tens of thousands of
  events is paged forward in 200-event pages on first load and kept in memory; there is no virtualized list and no server-side "before" cursor.
* Comments page by cursor (`after=<comment id>`, 100 per page, **Load more comments**); the inbox pages by `nextCursor` (**Load more**);
  search pages by `before` (**More results**).

## 7. Connection status (part 46)

The indicator above the panel states the connection in words: Connecting, Catching up, Live, "Reconnecting, refreshing every 3 s",
"Live updates paused, refreshing every 3 s", You are offline, "You no longer have access to this run", and, appended when the server
sent a newer schema, "This page is out of date, reload to see all activity" with a **Reload page** button. It is deliberately not a
live region (the announcer speaks the few changes that matter). Mutations answered with a stale-state 409 show "This changed while
you were looking. The latest version is now shown; review it and try again." and are never resubmitted; a 429 disables the controls for
the `Retry-After` seconds (30 s when the header is absent) and says so.

## 8. Plain text only

Comment bodies, search snippets, history revisions and notification sentences are rendered as React text children. No new code uses
`dangerouslySetInnerHTML` or `innerHTML` (a test scans the source). Notification wording comes from a fixed client-side map keyed by
the closed `summaryCode`; an unknown code reads "You have a notification". Urgency is the word "Urgent", never colour alone.

## 9. How it is tested (and what that proves)

Vitest, node environment only (there is no DOM library in this repo). Four techniques:

1. **Pure logic:** reducer, backoff, status text, announcer (fake timers), formatters, view-models.
2. **Static render** (`renderToStaticMarkup`): roles, names, `aria-*`, `tabindex`, escaping.
3. **Element-tree driver** (`section7/tests/support/elementTree.ts`): expands function components into host elements so a test can find a
   control by name and call its handler with a fake event (including `Ctrl+Enter`, `Escape`, `closest(...).focus` order). With the
   optional hook runtime it also drives `useState` components.
4. **Controller with fakes:** the stream controller against a fake socket, history and clock.

Files: `realtimeCore.test.ts`, `collabStream.test.ts`, `keyboard.test.tsx`, `discussion.test.tsx` (run directory),
`app/notifications/notifications.test.tsx`, `src/lib/a11y.test.ts`.

## 10. Not covered (honest list)

* **No real browser run.** Tab order, focus rings, `:focus-visible` behaviour, real key events, scroll behaviour, `matchMedia`,
  `Intl` data of a real device and the Next.js build of the new routes were not exercised. The element-tree tests prove handler wiring,
  names, roles and the order of focus calls, not that a browser moves focus there.
* **No screen reader testing** (VoiceOver, NVDA, JAWS, TalkBack). Live-region behaviour differs between them; the throttling is a
  design choice, not a measured result.
* **No axe / Lighthouse run.** No colour-contrast check of the new elements (they use the existing CSS variables and `Pill` / `Notice` /
  `Button` components; contrast was not measured). No zoom / reflow check at 200-400%.
* **No full i18n:** strings are English literals; only dates, times, numbers and relative times follow the locale. No RTL review.
* The bell sits in the sidebar, which collapses into a drawer at 900 px and below: on small screens it is reachable only after opening
  the navigation.
* Pre-existing sections keep their own text and structure (conflict table, presence chips, agent tree); only their live regions and
  focus-parking were changed.
* The notification links to `/sessions/{id}` and `/workspaces/{id}` have no page in this app; the inbox says so instead of navigating.
* Moderation controls exist for admins (hide / restore / delete with a reason) but there is no admin-wide moderation queue.
