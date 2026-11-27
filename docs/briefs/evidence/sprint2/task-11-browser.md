# Task 11 — X-07 / X-21 approval rendering in a real browser (2026-11-24)

Base `fae78d70` + this commit. **Stack:** Docker daemon down, so no compose backend. The spec runs
Chromium against `next dev` (section7, port 3020) with every `/api/v1` call answered by
`page.route` (one approval in the exact section4 `collab.list_approvals` shape). This verifies
the UI half; the backend half is section4 `test_approval_copy.py` + `test_collab_backend.py`.

    cd section7 && E2E_SKIP_STACK_CHECK=1 E2E_BASE_URL=http://localhost:3020 \
      npx playwright test e2e/approval-rendering.e2e.ts --retries=0
    ✓ plain rendering: the sentence leads, technical record stays
    ✓ decision card at 1280px: dominant band above the fold and above Approve
    ✓ decision card at 375px: dominant band above the fold and above Approve
    ✓ keyboard only: reach and press Approve without a mouse; digest echoed
    ✓ axe: plain approval card has no serious or critical violations
    ✓ axe: decision approval card has no serious or critical violations
    6 passed (6.7s)

**Defect found and fixed by this run:** at 375 px the first grid build overflowed the card and
clipped the *elevated* and *critical* columns: the critical credentials cell was invisible. Fixed
with fixed table layout, compact cells and short visible labels (low/mod/elev/crit) whose full
word stays in an `sr-only` span. The spec now asserts both active cells lie inside the card.

**Keyboard order (as built):** card → technical diff (`<pre tabindex=0>`) → *Approve* → *Reject*.
The grid is read before the buttons in DOM order but is not a tab stop (a table, not a control).
The prompt's "deny before approve" order was not adopted; changing button order is a UX
decision for the owner, not a verification fix.

Screenshots: `x07-plain-1280.png`, `x21-decision-1280.png`, `x21-decision-375.png` (this folder).

**Not covered:** live backend round-trip (needs the stack), screen-reader audio check, setting
the preference through a settings UI (no such UI exists yet; preference set via API).

## Sprint 3 / Task 15 addendum (2026-11-24)

- **Still mocked.** Task 12 stack smoke is BLOCKED (Docker daemon down, no compose backend), so
  no live section4 round-trip was added. The browser spec continues to use `page.route` for every
  `/api/v1` call.
- New guard: `section7/e2e/approvalFixture.ts` is the single mock payload shared by the spec and
  `approvalFixture.test.ts`, which fails if the mock drifts from section4's documented
  `ApprovalRequest` limits (action enum, paths/summary/diff maxima) or if the approvals GET route
  is no longer in `section4/openapi.json`.
- Superseded note above: the settings UI **now exists** (Sprint 3 Task 13, commit `2a1dc0cc`) —
  `/settings/account` offers Technical / Plain / Decision card against `GET/PUT /me/rendering`.
- Task 14 also added `similarCases` to the API and the card; the shared fixture carries one item
  so the browser spec exercises the new read-only block.

## Sprint 4 / Task 24 — LIVE addendum (2026-11-24, stack from Task 21)

The stack is now up, so the live path was taken. `section7/e2e/approval-rendering-live.e2e.ts`
drives **real** section1 `GET/PUT /api/v1/me/rendering` and real section4 plan/join/leader/
approval APIs — no `page.route` for approvals or rendering. It seeds a run + approval through the
documented APIs, verifies the real list response carries server `plain`, `decisionGrid` and
`similarCases`, changes the preference through `/settings/account`, re-reads it from the real
service, and re-checks axe + 375 px on the live card.

```
E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
E2E_ORCHESTRATOR_URL=http://localhost:8004 npx playwright test \
  e2e/approval-rendering.e2e.ts e2e/approval-rendering-live.e2e.ts --retries=0
  → 7 passed (1 live + 6 mocked)
```

Screenshots: `docs/briefs/evidence/sprint4/live-plain-approval.png`, `live-plain-375.png`.
The "still mocked" note above now applies only to the historical sprint-3 state; the X-07 UI half is
**verified live** at the Sprint 4 SHA. The mocked spec stays green as a regression guard.
