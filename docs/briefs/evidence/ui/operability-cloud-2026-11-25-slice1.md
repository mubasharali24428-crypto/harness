# UI operability, cloud evidence: slice 1 (Home "Needs you")

- Slice: 1, Home "Needs you"
- Branch: `claude/ui-op-s1-needs-you` (off `w28-s2-sprints1-4` at `fe2233c`)
- Code SHA: `ecb18f4`
- Date: 2026-11-25 (cloud session)

One evidence file per slice (`-slice<N>.md`) so the five slice PRs into `w28-s2-sprints1-4` do not
conflict with each other on one file.

## Commands and exit codes

Run from `section7/` after `npm ci` (exit 0).

| Command | Exit | Result |
|---|---|---|
| `npx tsc --noEmit` | 0 | clean (no TS5102 / baseUrl issue on this base) |
| `npx vitest run src/components/home` | 0 | 1 file, 14 tests passed |
| `npx vitest run` (full) | 0 | 126 files, 1,288 tests passed (baseline before the slice: 125 files, 1,274 tests) |

## What the tests lock in (`section7/src/components/home/needsYou.test.tsx`)

- Home lists approvals, blocked runs and open reviews from the runs list, oldest wait first; working and done runs are not listed.
- Each row has an age, a one-line plain reason and exactly one action, Open, whose accessible name names the run and the reason.
- An unknown blocker reads "the reason was not reported"; a missing time reads "—", never an invented age.
- Empty reads "Nothing waiting" and links to `/runs`; loading is a status region, not an empty list.
- A failed load is an alert naming `GET /api/v1/orchestrator/runs` with the server's reason and a Retry button that reloads.
- The list is a named, programmatically focusable list; Enter on it opens the oldest item; Enter on a row link, modified Enter and other keys are left alone.
- The list takes focus after loading only when focus is on `<body>` or the main region (the page was just reached, e.g. by `g h`), never from an input.

## Method and limits

- Repo protocol is node-env vitest with no jsdom or React Testing Library (see `tests/support/elementTree.ts`);
  this slice follows it rather than adding dependencies. Components are expanded to an element tree and their
  handlers are called with fake events; the loader runs against a stubbed `fetch`. Real focus movement, Tab order
  and key events in a browser are not exercised.
- No axe helper exists for component tests (axe is wired only into Playwright); roles and accessible names are asserted instead.
- Scope requests: no existing list API carries a live scope request. A run stopped by repeated shell-gate denials
  is `blocked` (`engine_failed`) and appears in this list; the request itself is shown on the run page. No contract bump.

## Not run

- Live RC / Mac: NOT RUN (cloud session, no stack).
- U1 timed task ("oldest approval opened in under 10 s, keyboard only, on a real inbox"): NOT RUN (cloud session, no stack). Owner Mac later.
- Playwright e2e and screenshots: NOT RUN (they need a running server).
