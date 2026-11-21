# Hand-back: F2
Model: GLM 5.3 Flash   Date: 2026-11-14   Revision: f7af86d
Status: DONE
Token use (estimate): 70000

## Files changed (uncommitted)
- `section7/e2e/palette.e2e.ts` — palette 100 ms open budget now measured on every route from the U0 inventory (`docs/briefs/evidence/ui/u0-inventory-2026-11-14.md`): 23 static routes in the loop plus the 3 dynamic run routes (`/projects/[projectId]/runs/[runId]`, `/checkpoints`, `/projects/[projectId]/tree`) resolved from a demo-seeded run. Original test name kept; original keyboard journey (filter → cost page, `g r` jump, Escape close) and its /settings budget measurement unchanged. Measurement mechanics extracted into `openPaletteMs()` unchanged (key event → `[cmdk-input]` in DOM, in-page). Breaches collect into a record asserted empty at the end, so one run lists all gaps. Per-route annotations + `console.log` kept.
- `section7/e2e/a11y.e2e.ts` — `PAGES` extended from 5 to 22 static signed-in routes (every inventory page except `/login`, which stays covered pre-auth by the dedicated login test). Run test extended to scan the same 3 dynamic run routes after demo seeding, with the same collect-all-failures pattern as the static walk. Timeouts raised: signed-in walk 600 s, run test 300 s. Assertion law unchanged: zero axe violations per page.
- `docs/briefs/evidence/glm/F2-handback.md` — new (this file).

## Checks run
- `cd section7 && npx tsc --noEmit` — exit 0, zero errors. No AppShell.tsx errors at run time (the warned mid-edit either had not landed or was already type-consistent), so all three files type-clean including mine.

## Checks not run
- Playwright (`npx playwright test e2e/palette.e2e.ts e2e/a11y.e2e.ts`) — NEEDS-REVIEWER-RUN. No stack started, per task constraints. All behavioral claims below are unverified until this run.
- vitest — not the e2e runner; not run (matches `*.e2e.ts` exclusion law in `playwright.config.ts`).

## Decisions I made (reviewer may overrule)
- Route list source of truth: the inventory's route table (27 rows minus `app/layout.tsx` which is not navigable). Kept inventory table order.
- Palette test stays ONE test (original name kept): journey phase first, then the per-route budget loop, then demo seeding for the 3 dynamic routes. Alternative would have been `test.each` per route — rejected because each route re-login would multiply stack round-trips; one signed-in session loops all routes.
- Budget law per route: `ms > 0` sanity plus `ms < 100`, same semantics as the original assertion; breach recorded as `<ms> ms` in the breaches map; assert at the end.
- `/login` IS included in the palette budget loop (signed-in revisit) and NOT in the a11y `PAGES` walk (already covered by the pre-auth login test). `/auth/sso/callback` included in both loops as a plain route visit.
- Each measured route gets a no-redirect guard (`pathname === route`) copied from the a11y walk, so AppShell's auth bounce can't attribute another page's numbers to a route.
- Palette-not-open on a route is recorded as a breach entry (`palette did not open: …`) instead of failing immediately — keeps the collect-all-gaps property.
- Palette closed with Escape after each measurement to leave a clean page for the next route.
- `test.setTimeout` raised (600 s palette, 600 s a11y walk, 300 s run test) to cover ~27 goto+measure/scan cycles at the config's 90 s default being too small.

## Open questions
- Should `/auth/sso/callback` be scanned signed-in without a real SSO code? It will render its error/Notice state; reviewer may prefer a synthetic valid callback or exclusion.
- If any route lacks the global palette (e.g. `/docs`, `/pricing` render without AppShell), the budget loop records "palette did not open" — reviewer should decide whether that is a product gap or a test exception.
- Signed-in revisit of `/login`: no redirect observed in code, but if the app bounces signed-in users away from `/login`, the no-redirect guard will flag it.

## Unrelated issues seen
- Inventory's "Nine settings pages (decision's separate list)" section is self-inconsistent: heading says nine, the numbered list has 10 entries, the note claims 11. The route table is authoritative; I used it.
- `section7/test-results/` and `section7/tsconfig.tsbuildinfo` are committed-or-present build artifacts in the repo; not touched, just noting.
- Another lane's AppShell.tsx mid-edit did not produce tsc errors during my run — timing luck, not a guarantee.

## Known limits of this change
- Nothing was executed against a running stack: palette-open latency, axe cleanliness, redirect behavior, and demo seeding success are all unverified until the reviewer's Playwright run.
- Dynamic route coverage depends on demo seeding working (`demo-seed-button`); if the replay breaks, the 3 dynamic routes are not measured.
- 100 ms budget on slower machines or cold dev server may breach on first-hit routes (no warmup pass was added); reviewer may want a warmup lap or per-route retries before judging breaches.
