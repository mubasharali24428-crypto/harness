# Task 24 — Live approval + settings e2e (G-E2E-1, G-X07-1)

- Date (UTC): 2026-11-24
- Status: **PASS — live stack, no mocks**
- SHA at verification: `ef21cd76` (Task 21 commit; stack unchanged by later commits)

## Stack / ports

Compose from Task 21: identity `http://localhost:8001`, orchestrator `http://localhost:8004`,
frontend `http://localhost:3010` (local `.env` overrides the default :3000). Global setup's stack
probe passed with `E2E_API_URL` pointed at 8001.

## What the new spec proves

`section7/e2e/approval-rendering-live.e2e.ts` uses `page.request` + the session token the app stored
after a real dev-bypass login. There is **no `page.route`** for approvals or rendering:

1. Real `GET /api/v1/me/rendering` → `technical`; real `PUT` → `plain`; real `GET` confirms.
2. Real `POST /api/v1/orchestrator/plans` mints a run; real `join`, `leader`, and
   `POST /runs/{runId}/approvals` create a pending high-impact approval.
3. Real `GET /runs/{runId}/approvals` carries server-computed `plain`, `decisionGrid` and
   `similarCases` (an empty array on a first denial is honest and accepted).
4. The routed run page renders that real response (`plain-<id>` equals the API's `plain` string).
5. `/settings/account` changes the preference through the shipped UI; the real service confirms
   `technical` after saving.
6. axe (WCAG A/AA/21AA/22AA) and 375 px checks run on the **live** decision card.

## Verification

```
cd section7 && E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
  E2E_ORCHESTRATOR_URL=http://localhost:8004 npx playwright test \
  e2e/approval-rendering.e2e.ts e2e/approval-rendering-live.e2e.ts --retries=0
→ 7 passed (1 live + 6 mocked, 5.9s)
```

The mocked `approval-rendering.e2e.ts` and `approvalFixture.test.ts` remain green; the historical
"still mocked" evidence in sprint2 now has a live addendum. Screenshots are under
`docs/briefs/evidence/sprint4/` (`live-plain-approval.png`, `live-plain-375.png`) and are left
uncommitted as generated artifacts, like previous sprints' screenshots.

## Honest limits

- `similarCases` was empty (first denial in a fresh team); the read-only block renders nothing, as
  designed. No cross-team data was seeded or faked.
- This proves the X-07 **UI half + real wire contract**; the KPI signal gap on the collab path is
  recorded separately in Task 27 evidence.
