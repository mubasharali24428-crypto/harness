# Task 33 — Live blind-approval KPI e2e (G-X07-4)

- Date (UTC): 2026-11-25
- Status: **PASS — live compose stack, no mocks**
- SHA under test: `5932eaf1` (Tasks 31/32/37 included). The dev stack (`harness-*`) was rebuilt
  from this checkout first (`docker compose --env-file .env -f deploy/docker-compose.yml up -d
  --build --wait` → rc=0; migrate exit 0, "drift gate: OK"); the Nov 24 images predated Task 31/32.
- Host: owner Mac, Docker Desktop 29.5.3, PostgreSQL 18 (compose `db`).

## What the spec proves (`section7/e2e/blind-kpi-live.e2e.ts`)

1. A fresh admin signs in through the real `/login` form; a second real user signs in, is invited
   by the admin (`POST /teams/{id}/invites`, role engineer), redeems with the invited email and signs
   into the admin's team. Self-approval stays off.
2. Fresh team KPI is empty; the engineer's KPI fetch is **403** (admins only).
3. Four collab decisions on one shared run (admin leads, engineer asks):
   - approved **in the shipped UI** after the diff was scrolled on screen → client sends `viewed_detail: true`;
   - approved over the API with `viewed_detail: false` (blind);
   - approved over the API with no signal (unknown — never blind);
   - rejected.
4. Real `GET :8008/api/v1/review-kpi/blind-approval` →
   `overall == byRendering.technical == {decisions 4, approvals 3, withSignal 2, blind 1, unknownSignal 1, rate null}`
   (rate is `null` below `minSample` 10).
5. `/dashboard/cost` tile renders that response: "insufficient data (2 of 10 measured approvals)".

## Run

```
cd section7 && E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
  E2E_ORCHESTRATOR_URL=http://localhost:8004 E2E_REVIEW_URL=http://localhost:8008 \
  npx playwright test e2e/blind-kpi-live.e2e.ts --retries=0
  ✓  1 [chromium] › e2e/blind-kpi-live.e2e.ts:19:5 › live: collab approvals land in the admin's blind-approval KPI (1.5s)
  1 passed (2.7s)
```

Audit rows written by that run (compose PG, newest first):

```
docker exec harness-db-1 psql -U harness -d harness -tAc "select action, detail->>'rendering',
  detail->>'viewedDetail', left(detail->>'teamId',8) from audit_log where action in
  ('approval.approved','approval.rejected') order by seq desc limit 4"
approval.rejected|technical||b6122a8d
approval.approved|technical||b6122a8d
approval.approved|technical|false|b6122a8d
approval.approved|technical|true|b6122a8d
```

Screenshot: `docs/briefs/evidence/sprint5/task-33-kpi-tile.png` (generated, left uncommitted like
earlier sprints' screenshots).

## Honest limits

- Only the `technical` rendering bucket is exercised (the admin's default). Plain/decision buckets
  are covered by section8 unit tests, not live.
- Blind-approval rate on real pilot decisions: still **not measured** (no pilot traffic).
