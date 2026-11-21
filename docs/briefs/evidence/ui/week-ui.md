# UI week ledger — Monday baseline and decisions

**Date:** 2026-11-13 · **Branch:** `w28-s1-delegate` · **Revision:** `04b819b`
**Plan:** `FUTURE PLAN 2.0.md` §5A · **Status:** Monday complete; D-U1–D-U4 signed by the owner.
The plan text cites `8836dfa`; per Phase 0's one-revision rule this baseline is pinned to the current HEAD
`04b819b` ("Inbox names the real action and opens the plan tab; run timeline reads the run's own steps").

## Environment

The RC frontend was stopped for the baseline so the checked revision was the only thing served.
HEAD was built in an isolated copy (`/tmp/ui-week-build`, RC public env vars, symlinked `node_modules`)
and served on `localhost:3011` with `next start`. The API/orchestrator/identity services were the
running RC stack (`harness-rc`). After the run, the local server was stopped and `harness-rc-frontend-1`
was restarted and health-checked (see Restore proof).

Working tree at baseline: the two known untracked docs (`FUTURE PLAN 2.0.md`,
`docs/briefs/glm-harness-prompt-2026-11-13.md`); no other lane's files touched.

## Checks at `04b819b`

| Check | Command | Result |
|---|---|---|
| Typecheck | `cd section7 && npx tsc --noEmit` | exit 0, no output |
| Unit/integration | `cd section7 && npx vitest run` | **106 files, 1,153 tests passed**, 4.11 s |
| Production build | `npx next build` in the isolated copy | success; route table emitted (all app routes + 9 settings pages) |
| Non-paid e2e | Playwright, specs below, `--retries=0` | **8 passed, 0 skipped**, 20.1 s |

E2E environment: `E2E_COMPOSE_PROJECT=harness-rc E2E_BASE_URL=http://localhost:3011
E2E_API_URL=http://localhost:18001 E2E_IDENTITY_URL=http://localhost:18001
E2E_ORCHESTRATOR_URL=http://localhost:18004`.

| Spec | Test | Time | What it proves |
|---|---|---|---|
| `a11y.e2e.ts` | login page, WCAG 2.2 A/AA | 490 ms | no axe violations on login |
| `a11y.e2e.ts` | signed-in pages | 5.0 s | no axe violations across signed-in routes |
| `a11y.e2e.ts` | run page | 1.6 s | no axe violations on the run surface |
| `demo.e2e.ts` | signed-out `/demo` replay | 682 ms | demo reaches the replay without looping |
| `home.e2e.ts` | needs-you/working match API; oldest item by keyboard | 1.3 s | keyboard open measured **0.18 s** (budget 10 s) |
| `palette.e2e.ts` | palette journey, axe, open budget | 1.5 s | open measured **12.9 ms** (budget 100 ms) |
| `phone.e2e.ts` | 375 px home, palette, run, verdict card | 2.4 s | no sideways scrolling |
| `ui-baseline.e2e.ts` | screenshots (new this week) | 5.6 s | 18 before-images written; rerun 5.8 s |

The U1 gap "timed task not live-verified" in §5A.1 is closed by the 0.18 s keyboard result.
The mission-control view of all running agents is still missing (U1 scope, not Monday's).

### Skips and reasons

| Spec | Why it did not run |
|---|---|
| `journey.e2e.ts` | creates a project, approves a brief and plan, and starts a real run — a paid model call; §5A.3 requires the reviewer present and an approved budget |
| `real.e2e.ts` | real OTP sign-in plus a real-model run; same paid-run guardrail |
| `multiplayer.e2e.ts` | needs a second identity/browser session, an orchestrator at its own default `:8104`, and starts a run; outside the non-paid baseline set |

`ui-baseline.e2e.ts` is new: it is the before/after evidence spec (`UI_PHASE=after` later in the week).
It seeds one proposed plan through the real plan-gate endpoint (no model call) so the inbox shows a
real needs-you card, then captures home, the recorded demo replay, and the verdict card.

## Before-screenshots

18 images in `docs/briefs/evidence/ui/`, verified widths and both themes:

- Surfaces: `home`, `run`, `review`
- Widths: 375, 768, 1280 px (900 px tall viewport, full-page capture)
- Themes: dark, light
- Pattern: `before-<surface>-<width>-<theme>.png`

| Surface | Source of the captured state |
|---|---|
| home | signed-in inbox with one proposed plan ("Approve or reject the plan") |
| run | recorded demo replay with the scrubbable run timeline |
| review | verdict card using the `phone-fixture` stub (see below) |

## Demo loop root cause

Already fixed at `6d51e12` (Kimi T2) and covered by `e2e/demo.e2e.ts` (682 ms, green here):
in the `?next=demo` flow the login page offered a second "Try the demo" button that pushed back to
`/demo`, which sent a signed-out visitor to login again. The fix replaces that button with the login
form and, after sign-in, returns via `/demo?start=1` so the original click seeds the replay once.
Code: `section7/app/login/page.tsx` (`start=1`), `section7/app/demo/page.tsx`.

## Findings carried into the week

1. **Review data is blocked on Section 8 GitHub OAuth.** `/review?pr=…` returns
   "github not connected for this user; complete the OAuth flow first". Monday's review screenshots
   use one stubbed `phone-fixture` response in `ui-baseline.e2e.ts` (and `phone.e2e.ts`); this is
   labelled fixture evidence, not a live review.
2. **Sidebar project switcher is blank on the demo run page at 1280 px** — the demo project is not in
   the sidebar's project list. Candidate U0/U2 fix; captured in the run screenshots.
3. **`phone.e2e.ts` rewrites two tracked PNGs** (`phone-palette-375.png`, `phone-run-375.png`) on every
   run. They were restored to HEAD after the baseline so the week's diff stays intentional.
4. **Baseline noise:** the first capture had an empty needs-you card; the spec now seeds one plan
   first, so the evidence shows the real triage state.

## Decisions (signed by the owner, 2026-11-13)

| ID | Question | Decision | Record |
|---|---|---|---|
| D-U1 | Component stack | **Tailwind v4 + shadcn/ui (Radix, owned source) + cmdk**; migrate page by page | `docs/decisions/2026-11-13-du1-component-stack.md` |
| D-U2 | Visual direction | **Dark-first, one accent**; light stays a supported secondary theme | `docs/decisions/2026-11-13-du2-visual-direction.md` |
| D-U3 | Ordering | **U0 before P-17**; the portal is a layout over the same tokens | `docs/decisions/2026-11-13-du3-u0-before-p17.md` |
| D-U4 | Risk tiers | **Off**; every approval explicit until P-D6 | `docs/decisions/2026-11-13-du4-risk-tiers.md` |

With D-U1 signed, Tuesday's U0 migration (app shell + nine settings pages) can start.

## Guardrails honoured

- No deploy; no `docker compose` changes; `down -v` not used; demo containers (`harness-*` without
  `-rc-`) untouched.
- No paid model call; `journey.e2e.ts`/`real.e2e.ts` not run.
- No auto-approval; risk tiers remain off.
- No commits; working tree changes are only this week's evidence, spec and decision drafts.

## Restore proof

```
harness-rc-frontend-1 Up (healthy) 127.0.0.1:3011->3000/tcp
frontend HTTP 200
```

## Next

1. ~~Owner signs D-U1–D-U4~~ — signed 2026-11-13, records above.
2. Tuesday: inventory every route/component still on `globals.css`; migrate the shell and the nine
   settings pages onto tokens/primitives while old pages keep working; extend the 100 ms budget and
   axe checks to every route.
