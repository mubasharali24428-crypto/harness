# Part 56: accessibility walk (session s12-b)

Date: 2026-10-20. Branch `s12/scale-ops`, worktree `harness-s12-b`.

## What runs

`section7/e2e/a11y.e2e.ts`, three tests, Chromium only, axe-core 4.13.0 through `@axe-core/playwright` 4.13.0 (pinned).
Rules: WCAG 2.0 A/AA, WCAG 2.1 AA, WCAG 2.2 AA (`wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa`).

- login page
- signed-in pages `/`, `/runs`, `/notifications`, `/settings`, `/dashboard/cost`
- run page, reached through the demo seed (`/demo` → `POST /demo/seed`)

Command (frontend served by `next dev` on :3000 against the compose backends, because the identity CORS allow-list is `http://localhost:3000`):

    cd section7 && E2E_BASE_URL=http://localhost:3000 npx playwright test e2e/a11y.e2e.ts --reporter=list --retries=0

Result on the final tree: 3 of 3 pass.

## Violations the walk found and what changed

| Rule | Where | Fix |
|---|---|---|
| color-contrast | Default link blue, `/settings` "Project rules", `/notifications` settings link | `color: var(--accent)` |
| color-contrast | `in-flight` phase pill `#3b82f6` (about 3.6:1 at 12 px) | `#60a5fa` (about 5.9:1) in `app/dashboard/planGate.tsx` |
| color-contrast | `dangerButtonStyle` `#ef4444` with white text (about 3.8:1) | `#dc2626` (about 4.8:1) in `src/lib/styles.ts` |
| target-size | Breadcrumb links, about 16 px tall (3 on the run page) | `minHeight: 24`, inline-flex in `src/components/Breadcrumbs.tsx` |
| scrollable-region-focusable | CodeMirror `.cm-scroller` (diff and file editor) | `keyboardScroller` plugin sets `tabIndex = 0` in `src/lib/codemirror.ts` |
| color-contrast | Secondary text `#6b7280` (3.3 to 3.9:1 on the dark theme), 16 uses | `var(--muted)` (5.3 to 6.2:1); StatusPanel idle colour `#8b949e` |

Walk fixes, so a pass means something:

- `loginViaUi` used to return even when no session was stored (its `Promise.race` resolves on timeouts). The first run therefore scanned the login page as if it were signed in. It now throws when `harness.session` is missing.
- Every signed-in page must stay on its own path (no bounce to `/login`) and return a status below 400. `/dashboard` did not exist and its 404 page passed the axe check; the walk now uses `/dashboard/cost`.
- Every page is scanned even after one fails, and each failure line names the first three axe targets with their HTML.

## Verification

- `npx vitest run`: 74 files, 850 tests pass after the colour and link changes.
- Walk: 3 of 3 pass on the final tree (log: scratchpad `s12b-a11y-3000-e.log`).

## Not covered (Part 56 stays partial)

- Pages outside the list: `/review`, `/meeting`, `/projects/*/tree`, `/settings/*` other than `/settings`, `/dashboard/cost` aside, the sign-in SSO branch.
- Screen reader behaviour (no NVDA or VoiceOver run). The walk checks markup, not reading order or announcements.
- Keyboard-only task completion. The walk checks focusability, not that each task can be finished without a mouse.
- Other themes and browsers. Dark theme and Chromium only.
- Row 12 terminal panel item 009 is still not asserted on the run page.
- axe catches only part of WCAG. Manual review is still needed.
