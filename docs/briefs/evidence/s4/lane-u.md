# Session 4, lane U: UI for parts 41, 42, 45 and the queued-guidance e2e fix

All paths below are under `section7/app/projects/[projectId]/runs/[runId]/` unless stated. Nothing committed or pushed.

## Blocking problem: queued guidance (fixed)
Lane C queues guidance from a non-holder while a timed lease exists. The panel had no accept step, so
`e2e/multiplayer.e2e.ts` (peer sends "Keep keep.txt unchanged") only saw "control instruction queued".
- The lease holder now sees a region "Suggestions waiting" (from `GET control` `queuedInstructions`) with
  Accept and Reject (`POST control/decide {instructionId, decision}`). Buttons are described by the suggestion text.
- The sender sees a polite status "Your direction is queued. <leader> must accept it before the agent sees it."
  (`data-testid=direction-notice`, clears after 10 s) and their own suggestion stays listed as "Waiting for review."
- `sendRedirect` result type now carries `queued` / `instructionId` (`seq` may be null).
- `e2e/multiplayer.e2e.ts`: 5 lines added after the peer's "Send direction": assert the sender's queued note, then the
  leader clicks Accept inside `region "Suggestions waiting"`, then the existing history-log assertion. Nothing else changed.
  After accept the server writes an ordinary `redirect.guidance` event by the leader, so the log reads
  "p1-a gave guidance: Keep keep.txt unchanged".

## New directives
- 41.009 `presenceSection.tsx`: chips from `GET presence` (server `announcement` sentence + bucketed last seen, no exact times,
  server `notice` shown); heartbeat to `presence/heartbeat` with a per-tab `deviceId` (sessionStorage), only while joined,
  mode is not `off` and the tab is visible, cadence from `heartbeatEverySeconds` (30 s until the first reply),
  `presence/leave` with keepalive on unmount. Buttons: Observe only / Take part / Leave run / Rejoin run (after leaving) /
  Mute / Turn notifications on. Polite `aria-live` region speaks the server `announcement` of your action and other people's
  status changes seen between polls.
- 42.003/.007 `controlSection.tsx`: Control block (holder, plain-words expiry, away/takeable hints, queue). Take control / Request
  control use the `how` routes from the control view (`howUrl`); Release uses `control/release`; holder can Hand over or Decline queued
  requests, a requester can cancel. Conflict view lists open conflicts/proposals from `GET intent/conflicts` with base / current /
  their version per field, a warning with the number of approvals a resolution would void, and Keep current / Use their version
  (`resolve` with the version seen) for the holder or an admin.
- 45.006 `agentTree.tsx`: nested subtasks from `GET agents` (state, budget left, percent, last tool summary), Pause / Resume / Cancel
  for admins/engineers who have joined. A note says these change the record, not a process (docs/agents.md "NOT wired").
- Errors: `controlApi.ts` `friendlyError` maps SESSION_FULL, LEADER_MUST_HAND_OFF, LEASE_HELD, QUEUE_FULL, STALE_INTENT_VERSION,
  INTENT_CONFLICT, PRESENCE_DISABLED (and QUEUE_ORDER, NOT_LEADER, NOT_PENDING, ALREADY_RESOLVED, NOT_PARTICIPANT, ROLE_DENIED,
  TOO_MANY_DEVICES, ILLEGAL_TRANSITION, AGENT_BUDGET_EXHAUSTED) to plain sentences; unknown codes show the server message.
  Action errors render as `role=alert` inside their own section.
- Background polls fail silently (last data kept). This is deliberate: the existing offline step of the e2e expects exactly one
  `role=alert` in the panel (the panel's own), and a second one would break strict mode.

## Files
Changed: `collabApi.ts` (export `root`, `call`, `post`; `sendRedirect` type), `sharedRunPanel.tsx` (+35 lines: `notice` and
`children` props on `SharedRunView`, notice timer, three section mounts, `tick` = last collab seq), `section7/e2e/multiplayer.e2e.ts`.
Added: `controlApi.ts`, `usePolled.ts`, `controlSection.tsx`, `presenceSection.tsx`, `agentTree.tsx`, `controlPanels.test.tsx`.
Not touched: `clients/`, any openapi file, `section7/e2e/real.e2e.ts`.

## Verification (run in `section7`, sequentially)
- `npx tsc --noEmit`: no output, exit 0.
- Lint: `section7/package.json` has no lint script and no eslint config exists; the project's frontend gate (`make frontend`)
  is tsc + vitest, so tsc is the lint equivalent. Nothing else to run.
- `npx vitest run`: Test Files 61 passed (61), Tests 572 passed (572). New file `controlPanels.test.tsx`: 30 tests
  (error-code messages, client call shapes, suggestions holder vs sender, control status, conflict view, presence roster and
  aria-live region, changed announcements, agent tree nesting and steering buttons, panel mounts).

## Not verified / honest gaps
- No browser e2e was run (the lead runs the single smoke). In particular unverified against the real stack: that
  `GET presence`, `GET control`, `GET intent/conflicts`, `GET agents` return 2xx for the pilot team (the e2e fails on any 4xx/5xx
  response), that heartbeats never 4xx there, and the new Accept step timing (control view refetches on each new collab event
  and every 5 s; the e2e allows 15 s).
- vitest runs in the node environment (no jsdom, no testing library), so click handlers, the heartbeat timer/visibility logic and
  unmount leave are not exercised by tests; they are covered only by types and by static render of the views.
- No intent editor exists in the UI, so nothing here can itself produce a `409 INTENT_CONFLICT` or proposal; the conflict view shows
  and resolves conflicts that arrive from the API. `merge` resolution is not offered (keep current / take proposed only).
- Presence changes by expiry are announced only on the next poll (10 s) because the server writes no event for them.
- Role gating of agent buttons is presentational (admin/engineer who joined); the server decides.
- Pre-existing, unrelated: `describeEvent` prints "p1-b: control instruction queued" for the queue event (default branch); not changed.
