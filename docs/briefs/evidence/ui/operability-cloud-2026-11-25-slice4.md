# UI operability, cloud evidence: slice 4 (team settings)

- Slice: 4, team settings
- Branch: `claude/ui-op-s4-team-settings` (off `w28-s2-sprints1-4` at `fe2233c`)
- Code SHA: `e950cc2`
- Date: 2026-11-25 (cloud session)

## Commands and exit codes

Run from `section7/` after `npm ci` (exit 0).

| Command | Exit | Result |
|---|---|---|
| `npx tsc --noEmit` | 0 | clean (includes the edited e2e file) |
| `npx vitest run app/settings/team` | 0 | 3 files, 32 tests passed (new file: 11) |
| `npx vitest run` (full) | 0 | 126 files, 1,285 tests passed (baseline 125 files, 1,274 tests) |

## What the tests lock in (`section7/app/settings/team/teamOperability.test.tsx`)

- Member rows show name, email and role (role select labelled "Role for <name>"); invite rows show email, role and the server's status.
- **D3, member-remove URL: green.** Remove → in-page confirm → `DELETE ${IDENTITY_BASE}/api/v1/teams/team-1/members/user-bo`. The fixture's membership row id (`mem-row-2`) differs from the user id and never appears in the URL.
- Role change → `PATCH .../members/user-cy` with `{"role":"engineer"}`, same assertion against the row id.
- A row without a user id (agent member) is never sent.
- The first press of Remove only asks ("Remove this member?"); Cancel sends nothing and keeps the row.
- A failed DELETE (409 LAST_ADMIN) puts the row back in its original position and keeps the server's message; a failed PATCH snaps the role back and keeps the error.
- Revoke asks in the row ("Revoke this invite? The link stops working."); only "Yes, revoke" calls revoke; "Cancel" (named "Keep the invite for <email>") backs out.
- Every button in the admin roster has an accessible name.

## Method and limits

- Node-env vitest, no jsdom/RTL (repo protocol). The tables are expanded with `tests/support/elementTree.ts`; the test wires
  the table actions to `rosterOps` the same way `page.tsx` does and stubs `fetch`. The page component itself is not rendered.
- `e2e/invite-revoke-live.e2e.ts` now clicks the new confirm button; it was type-checked, not run.

## Not run

- Live RC / Mac: NOT RUN (cloud session, no stack).
- Playwright e2e (including the edited invite-revoke live spec) and screenshots: NOT RUN (they need a running server).
