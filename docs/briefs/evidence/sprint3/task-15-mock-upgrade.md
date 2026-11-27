# Task 15 — Live approval e2e OR honest mock upgrade

- Date (UTC): 2026-11-24
- Status: **mock upgrade done; live round-trip BLOCKED (no stack)** — see Task 12.

## Live path not taken

Task 12 (stack smoke) is BLOCKED: Docker daemon is down, no compose backend on `:5499`.
A real section4 round-trip would be fabricated without it, so no `approval-rendering-live.e2e.ts`
stack test was added. `docs/briefs/evidence/sprint2/task-11-browser.md` carries the
"still mocked" addendum.

## What was added instead

- `section7/e2e/approvalFixture.ts` — one shared approval mock, now also carrying the Task 14
  `similarCases` read-only list. `approval-rendering.e2e.ts` imports it, so the browser spec
  and the parity test cannot drift apart.
- `section7/e2e/approvalFixture.test.ts` — fails if the mock drifts from section4's documented
  `ApprovalRequest` contract in `section4/openapi.json` (action enum, paths/summary/diff maxima,
  digest shape) or if the approvals GET route disappears. Note: that route has no response_model,
  so its 200 schema is generic (`additionalProperties: true`); the request-side limits are the
  non-vacuous drift guard.

## Verification

```
cd section7 && npx vitest run          → 120 files / 1229 tests passed
cd section7 && npx tsc --noEmit        → clean
cd section7 && E2E_SKIP_STACK_CHECK=1 E2E_BASE_URL=http://localhost:3020 \
  npx playwright test e2e/approval-rendering.e2e.ts --retries=0
                                       → 6 passed (7.4s)
```

The 6 browser tests include the **axe** (WCAG A/AA/21AA/22AA, no serious/critical violations)
and **375 px** clipping assertions — the Task 14 UI change did not regress them.

## Side effects (not committed)

That playwright run regenerated `docs/briefs/evidence/sprint2/x07-plain-1280.png`,
`x21-decision-1280.png`, `x21-decision-375.png`. They are generated artifacts and are deliberately
left out of this commit; the pre-existing dirty `r2-*.png` screenshots and untracked user files
are likewise untouched.
