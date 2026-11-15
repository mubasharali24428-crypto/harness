# UI week leftovers (U0, U5) — 2026-11-15

Branch `w28-s1-delegate`, nothing pushed.

## What a user can now do

- **Move between settings sections**: every settings page has one row of section links (Overview
  plus nine sections) with the current one marked. All settings pages were already in the shell.
- **Reach every page from the palette** (⌘K): the nine settings sections, Phone triage, Demo, Docs
  and Pricing are listed by name. A unit test walks `app/` and fails if a static page is not
  reachable (login, the SSO callback and the unsubscribe link are excluded on purpose).
- **Use the phone inbox** at `/triage`: pending plan approvals only, Approve and Deny only. The
  component existed but no route showed it.
- **Open a run's checkpoints inside the shell**, with a link back to the run.
- **Scroll wide tables from the keyboard**: sideways-scrolling tables are now focusable regions.

## Verification actually run (from `section7/`)

| Check | Result |
|---|---|
| `npm run typecheck`, `npm run build` | pass (`/triage` is a route) |
| `npm test` | 111 files, **1198 passed** |
| `node scripts/u-check.mjs` (local production build, RC backend data) | **0 failures**, no console errors; axe clean and 0 px overflow at 1440/375 × dark/light on settings/team, triage and checkpoints; palette opens API keys and Phone triage; settings nav works from the keyboard |
| Screenshots | `docs/briefs/evidence/ui/u-{settings-team,triage,checkpoints}-{1440,375}-{dark,light}.png` |

## Not done, not verified

- **Verified on `:3011`** after the owner's go-ahead (frontend rebuilt, tag `vv90r4`): the new
  `e2e/leftovers.e2e.ts` plus the composer, review and workspace specs pass **9 of 9 in three of
  four runs**. The one failing run died in test setup: the identity service answered 429 to the
  logins (three back-to-back runs make about twelve sign-ins in a minute). Run alone it passes; the
  login rate limit is working as built, not a defect of these pages.
- **Triage was only seen empty in a browser** ("Nothing is waiting on you"); approve and deny on a
  real pending plan are covered by the component's 12 unit tests, not by a browser run.
- `PhoneTriage.tsx` and its test were another lane's uncommitted files; they are committed here
  unchanged because the new route needs them.
- Not started: the five-key tour, the under-60-second demo, the timed task recording (U1),
  presence (U6, gated), the Cost page headline, the client portal (P-17).
- `prReview.tsx` is still unused and not deleted. `e2e/multiplayer.e2e.ts` was not checked.
- D-U1 still records shadcn/ui, which is not installed.
