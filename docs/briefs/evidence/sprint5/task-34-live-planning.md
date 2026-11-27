# Task 34 — Live planning e2e (G-E2E-4)

- Date (UTC): 2026-11-25
- Status: **PASS — live compose stack, no mocks**; mocked `planning.e2e.ts` still green
- SHA under test: `5932eaf1` stack image (rebuilt from this checkout, see Task 33); spec added on `a0871b20`.

## What the spec proves (`section7/e2e/planning-live.e2e.ts`)

1. Real admin (UI sign-in) + a real engineer invited and redeemed into the admin's team; a real
   project via `POST :8001/api/v1/projects`.
2. The admin types the brief into the shipped planning form. The **real** planner
   (`assignment_plan.propose`) assigns `docs` to the member the brief names ("named in brief;
   current team member") and leaves `pen` "Unassigned: availability unknown", missing `pentester`.
   axe (WCAG A/AA) on the live version card: no serious/critical violations.
3. C-07 on the real server: reject without a note → **422 NOTE_REQUIRED**; the UI's Reject is disabled.
   The member's review attempt → **403**.
4. Approve in the UI → version 2 `approved`, version 1 read-only; real list is
   `[[1, proposed, null], [2, approved, 1]]`; approving again → **409**.
5. The member's own `GET …/assignment-plans/mine` → version 2, tasks `["docs"]` only; the other
   task's title is absent from the body. No page errors.

## Run

```
cd section7 && E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
  E2E_ORCHESTRATOR_URL=http://localhost:8004 \
  npx playwright test e2e/planning.e2e.ts e2e/planning-live.e2e.ts --retries=0
  ✓  1 [chromium] › e2e/planning-live.e2e.ts:18:5 › live: admin proposes, reviews and approves an honest plan; the member sees only their task (1.6s)
  ✓  2 [chromium] › e2e/planning.e2e.ts:13:5 › admin sees an honest proposal and approves the version they reviewed (764ms)
  2 passed (3.0s)
```

Screenshot: `docs/briefs/evidence/sprint5/task-34-planning-live.png` (generated, uncommitted).
The mocked spec rewrites `docs/briefs/evidence/sprint2/task-12-planning-admin.png` on every run;
that change was reverted, not committed.

## Honest limits

- The member's view is checked through the real `/mine` API, not the member UI: a user in two
  teams gets `409 Select an existing team` from `/auth/login` without a `teamId`, and the login
  form has no team picker for the bypass path. That is a product gap, recorded here, not fixed.
