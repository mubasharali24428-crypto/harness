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
