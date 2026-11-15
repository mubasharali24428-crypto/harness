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

## Added later the same day

- **Five-key tour** in the sidebar ("Keyboard"): open on a person's first visit, closed for good
  after "Got it", one click away afterwards. A unit test checks its shortcuts exist.
- **Cost page leads with one number**: total spend for the period in large type, tokens in the line
  under it. The doubled "By day" heading is gone.
- **Under-60-second first run**: measured, not built; the demo already finishes at once. Locally the
  demo button reaches a finished run with a pull request in 0.2 s; `e2e/leftovers.e2e.ts` now
  asserts sign-in to finished demo run in under 60 s.
- The timed task for U1 was already recorded in `week-ui.md` (0.18 s, keyboard).
- `prReview.tsx` (unused since R2) is deleted.
- `node scripts/u-check.mjs` after these: **0 failures**; unit tests **1199 passed**. These later
  items are **not yet on `:3011`** (the RC frontend was last rebuilt before them).

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
- `e2e/multiplayer.e2e.ts` is **stale and not fixed**: it still signs in to the old "Command
  Center", uses the removed "Open shared run" link and reads the shared-run panel that now lives in
  the Advanced pane. It needs the pilot stack (`deploy/pilot/stack.py`), which was not running, so a
  rewrite could not be verified and was not attempted.
- **No recorded screen-reader journey**: that needs a person using a screen reader.
- Presence (U6) and the client portal (P-17) are gated on owner decisions and not built.
- D-U1 still records shadcn/ui, which is not installed.
