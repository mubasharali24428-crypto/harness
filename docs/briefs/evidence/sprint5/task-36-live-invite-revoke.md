# Task 36 — Live B-09 invite → revoke journey (G-B09-1)

- Date (UTC): 2026-11-25
- Status: **FAIL at 2026-11-25 first run (historical; see Phase B update at the bottom — Playwright PASS pending owner Mac run).**
  Original: the journey runs and found two product defects; revoke does not work.
  P-D7 stays DEFERRED; this is engineering evidence only, not a Gate 6 verdict.
- Stack: compose dev stack rebuilt from `5932eaf1` (+ the D1 client fix below, frontend image rebuilt).

## The journey (`section7/e2e/invite-revoke-live.e2e.ts`)

Admin (UI sign-in) + engineer + viewer, one revision, browser + API:

| Step | Result |
|---|---|
| Admin mints three invites in `/settings/team` (engineer, viewer, third) and revokes the third in the UI | PASS |
| Engineer signs in in a second browser context and redeems in the UI | **FAIL before fix (D1)**, PASS after |
| Viewer redeem races a forwarded-token redeem (engineer presenting the viewer's token) and a revoked-token redeem | PASS: 200 viewer; forwarded refused (≥400); revoked **410 INVITE_REVOKED** |
| Both sign into the admin's team at their invited roles | PASS (`engineer`, `viewer`) |
| Shared run: engineer joins and requests an approval (201); viewer reads approvals (200) but cannot request (403) | PASS |
| Admin removes the engineer | **FAIL (D2 / D3)** |
| Removed engineer's token dead on identity + orchestrator; re-login 403; viewer untouched | not reached |

## Defects found

**D1 — UI redeem always 422 (fixed in this commit).** `redeemInvite` sent `{token}` only;
since M18-C5 the server also requires the invited `email`. Identity log: `POST /invites/redeem 422`.
Fix: `teamApi.redeemInvite(token, email, sessionToken)` + an "Invited email" field on the redeem
form. `vitest app/settings/team` → 19 passed; `tsc` clean; the live UI redeem then answered
"Joined team … as engineer."

**D2 — removing a member who has acted is a 500 on Postgres (not fixed: design decision).**
`DELETE /teams/{id}/members/{userId}` →
`RestrictViolationError: … violates RESTRICT setting of foreign key constraint
"fk_audit_log_actor_member_id" on table "audit_log"` (`teams_api.py:161 remove_member`). The
engineer's approval request wrote an `audit_log` row whose `actor_member_id` is their membership,
so the membership row cannot be deleted. Any member who ever did anything cannot be removed.
SQLite (the unit-test default) does not enforce this FK the same way, which is why unit tests pass.
Options for the owner: soft-delete memberships (`removed_at`) and filter everywhere, or keep the
row and revoke access another way; detaching `actor_member_id` would rewrite append-only audit.

**D3 — the shipped Remove (and role change) buttons send the wrong id (not fixed: contract change).**
The roster (`GET /teams/{id}/members`) returns the membership `id` and no `userId`; the page passes
that `id` to `DELETE`/`PATCH /teams/{id}/members/{user_id}`, which look up by user id → **404**
(identity log: `DELETE /api/v1/teams/{team_id}/members/{user_id} 404`). The UI hid the row anyway.
Fix needs `userId` on `MemberListResponse` (OpenAPI + TS client regen), so it is left for a
separate change. The spec therefore removes through the admin API.

## Run

```
cd section7 && E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
  E2E_ORCHESTRATOR_URL=http://localhost:8004 npx playwright test e2e/invite-revoke-live.e2e.ts --retries=0
# before D1 fix: fails at "Joined team … as engineer." (server 422)
# after D1 fix, UI Remove: fails at the dead-token check, /me still 200 (server DELETE was 404, D3)
# admin API removal: Expected 204, Received 500 INTERNAL_ERROR (D2)
```

Final run with the marking in place:

```
  ✘  1 [chromium] › e2e/invite-revoke-live.e2e.ts:30:5 › live B-09: … remove the engineer (1.5s)
  1 passed (3.0s)          # = failed as expected; identity log shows the DELETE … members 500 again
```

Historical: the spec carried `test.fail(...)` while D2 was open. That marker was removed after D2
shipped (migration 0082); see the Phase B update below.

Rate limit note: identity throttles `/auth/login`, invite minting and redeem at 10/min per IP; a
burst of live specs from one machine gets 429, which the browser shows as "Failed to fetch" (the
429 carries no CORS headers). Run this spec on its own or after a minute's pause.

## Phase B update (2026-11-25, owner Mac, after migration 0082)

- Journey (API/log level): **passed** after 0082 was applied. `DELETE` member answered **204** and the
  removed engineer's tokens were dead. Source: owner's Mac Phase B report (B3/B4), not reproduced in cloud.
- Playwright: the owner's Mac run reported **FAIL** at that time only because `test.fail(true, ...)`
  was still in the spec ("Expected to fail, but passed"). The marker is now removed from
  `section7/e2e/invite-revoke-live.e2e.ts`.
- **Playwright PASS is NOT claimed.** Only the owner's Mac re-run (`--retries=0`, command above) can confirm it
  without the marker. Status stays pending until that log exists.
