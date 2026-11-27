# UI operability, cloud evidence: slice 2 (run workspace, status first)

- Slice: 2, run workspace status first
- Branch: `claude/ui-op-s2-run-status` (off `w28-s2-sprints1-4` at `fe2233c`)
- Code SHA: `6a81980`
- Date: 2026-11-25 (cloud session)

## Commands and exit codes

Run from `section7/` after `npm ci` (exit 0).

| Command | Exit | Result |
|---|---|---|
| `npx tsc --noEmit` | 0 | clean |
| `npx vitest run src/components/workspace/runStatusFirst.test.tsx` | 0 | 1 file, 13 tests passed |
| `npx vitest run` (full) | 0 | 126 files, 1,287 tests passed (baseline 125 files, 1,274 tests) |

## What the tests lock in (`section7/src/components/workspace/runStatusFirst.test.tsx`)

- The header's h1 is the run goal, the state is in words, and spend is a figure or the word "unknown", never `$0` for a missing value.
- The header offers at most one next action: Open approval (Plan pane) while a plan awaits approval, Open diff (Diff pane) on an opened PR or finished run, none while working.
- Each step's receipt states What, When and Outcome; a failed step has a visible "Failed" label and `data-outcome="failed"`, and successful steps do not.
- With the "disconnected" fixture (stale aggregate), the header shows "Reconnecting: this is the last state the server reported." and keeps the goal, state and spend; the step column renders the same last state.
- Since-last stays the default diff scope and hides the snapshot history behind it (also covered by `tests/unit/diffScope.test.ts`, unchanged).

## Method and limits

- Node-env vitest, no jsdom/RTL (repo protocol, `tests/support/elementTree.ts`). The header is expanded and its button handler called;
  the column is rendered with `react-dom/server`. No browser, no real event stream: "disconnected" is the page's `stale` flag
  (`useRun` sets it when a refresh fails), not a dropped socket.
- `ReviewPane` / `DiffPane` were not changed.

## Not run

- Live RC / Mac: NOT RUN (cloud session, no stack).
- Playwright e2e and screenshots: NOT RUN (they need a running server).
