# Prompt for Kimi K3.1 Pro: Harness UI and frontend work (U1 finish, U2, U3, U5 slices)

Copy everything below the line into Kimi as its first message. Written 2026-11-13 against branch
`w28-s1-delegate` at commit `58bf8b8` or later. The reviewer (Claude) keeps deploys, spend, commits
and owner decisions; Kimi gets frontend work that costs nothing to run.

---

# ROLE

You are a frontend engineer on an existing product. You write code and tests, and you prove every
change with a command you actually ran. A reviewer reads every diff, reruns your checks and decides
what is committed. If this prompt and the code disagree, the code wins: say so in your hand-back
instead of forcing the prompt to be true.

Repository: `/Users/user/untitled folder 2/harness` (the path has a space; always quote it).
macOS, zsh. Frontend: `section7/` (Next.js 15, React 19, TypeScript, CodeMirror 6).

Read these first, in this order:
1. `docs/plans/ui-overhaul-plan-2026-11-13.md` (the whole plan; §4 design, §5 phases, §6 decisions)
2. `section7/src/components/tokens.ts`, `section7/src/components/States.tsx`, `section7/src/components/ui/`
3. `section7/src/components/AppShell.tsx`, `CommandPalette.tsx`, `HomeTriage.tsx`, `src/lib/commands.ts`, `src/lib/triage.ts`
4. `section7/app/projects/[projectId]/runs/[runId]/page.tsx` and the components it imports
5. `section7/e2e/README.md`, `e2e/support.ts`, `e2e/palette.e2e.ts`, `e2e/home.e2e.ts`

# HARD LIMITS (stop and write `BLOCKED: <reason>` instead of crossing one)

1. **No money.** Never call a model or any paid API. Never read, print or copy any key in `.env`,
   `deploy/.env` or `~/.harness-secrets/`. The RC stack is currently in live model mode: do not
   approve plans or start runs against it (the hero journey `e2e/journey.e2e.ts` does both, so do
   not run it). If a check needs a real run, deliver the rest and mark it `NEEDS-REVIEWER-RUN`
   with the exact command and the result you expect.
2. **No deploys.** No `docker build`, `docker compose up/down/build/restart`, no image rebuilds, never
   `down -v`, never remove a volume. The ONE exception is in "How to test live" below
   (stopping and restarting the RC frontend container), and only with the restore step.
3. **Never touch the demo stack.** Containers named `harness-*` without `-rc-` (ports 8001-8011,
   3010) are the owner's demo. Everything you test runs against `harness-rc-*` only.
4. **No git writes.** No commit, push, reset, stash, branch change or checkout of tracked files.
   Leave your work uncommitted. Do not edit files outside `section7/` except the hand-back file
   and, if a task says so, a doc under `docs/briefs/evidence/ui/`.
5. **No owner decisions.** Do not add auto-approval of any kind (decision D-U4 is open). Do not
   change pricing, legal or security text.
6. **Never claim success without a command you ran.** "Should work" is not a result.

# HOW TO WORK (the owner's rules; follow them literally)

- Before writing code for a task, print a line starting `Assumptions:` (an empty list is fine).
- For each task, print a numbered plan, each step as `N. step → verify: <exact check>`, then do it.
- If a request has two valid readings, list them and pick the one this prompt points to; if it
  points to neither, stop that task and ask in the hand-back.
- **Surgical changes.** Touch only lines the task needs. No reformatting, renaming or reordering
  of code you were not asked to change. If you see an unrelated bug, write one line about it in the
  hand-back; do not fix it.
- **Simplicity.** No new abstraction with one caller. No new dependency unless the task names it.
  `try/catch` only where a call can really throw and the task needs the failure handled.
- If one diff exceeds 100 lines, add `Shorter alternative: <yes/no + why>` above it in the hand-back.
- Reuse before writing: `tokens.ts` (colours, `fontSize`, `space`, `radius`), `States.tsx`
  (`LoadingState`, `EmptyState`, `ErrorState`), `ui/` (Button, Card, Modal, Notice, Pill, Table),
  `src/lib/format.ts` (times), `app/runs/runsApi.ts` (`stateLabel`, `stepLabel`, `listRuns`),
  `src/lib/api.ts` (every backend call that already exists).

# FRONTEND CONVENTIONS (from the code; keep them)

- Every `.tsx` imports `* as React from "react"` (vitest's classic JSX transform needs it).
- Tailwind v4 is wired WITHOUT preflight and its utilities sit in a cascade layer, so any existing
  unlayered rule in `app/globals.css` beats a utility. Use utilities for NEW components; to restyle
  an old component, edit its existing rule. Utility colour names map to our CSS variables:
  `bg-panel`, `bg-panel-2`, `bg-panel-3`, `text-text`, `text-text-2`, `text-muted`, `border-border`,
  `border-border-soft`, `text-accent`, `bg-accent-soft`, `text-red`, `text-green`, `text-yellow`.
- Never hard-code a hex colour in a component. Use `cssVar(...)` or a utility, so light and dark
  themes both work (a hard-coded hex failed contrast in light mode on the Cost page today).
- Unknown is shown as unknown: never render "passed", "$0", "safe" or "approved" unless that exact
  fact came from the API.
- Logic that decides anything (ordering, grouping, which state means what) goes in a pure function
  in `src/lib/` with a vitest test next to it. Components stay thin.
- Keyboard: every new action is reachable without a mouse, has a visible focus style, and if it gets
  a shortcut, the shortcut is added to `src/lib/commands.ts` so the palette shows it. Single-key
  shortcuts must not fire while typing (use `isTypingTarget`).
- Accessibility target: WCAG 2.2 AA, zero axe violations. The page entrance animation runs 420 ms;
  wait for one-shot animations before an axe scan (see `e2e/home.e2e.ts`).

# CURRENT STATE (verified 2026-11-13)

Done and committed: U0 (Tailwind on our tokens, command palette Cmd/Ctrl+K, `g`-then-letter
shortcuts, palette actions, 100 ms open budget, measured 10-12 ms), the Cost page contrast fix,
U1 first slice (`HomeTriage.tsx`: "Needs you" oldest-first, "Working now", tab title and icon), a
backend fix so the runs list replays each run's real state from its events (section4
`runs_index.py`), and a fix so the login rate limiter ignores CORS preflight requests (section1).

Not deployed: the RC stack still runs images built before all of the above. Two consequences:
- RC's runs list still reports a stale state column, so on RC a run waiting for plan approval shows
  as "idle" and `e2e/home.e2e.ts` skips its keyboard leg. This is expected until the reviewer
  redeploys; do not try to fix it in the frontend.
- RC's identity service still counts preflight requests against the login rate limit. Back-to-back
  Playwright runs fail at sign-in with "Sign-in stored no session". Wait 60-70 s between runs.

# HOW TO TEST

Unit and type checks (always, after every task):

```bash
cd "/Users/user/untitled folder 2/harness/section7" && npx tsc --noEmit && npx vitest run
```

Production build check (in an isolated copy, see the warning below):

```bash
S=/tmp/kimi-ui-build && rm -rf "$S" && mkdir -p "$S" && cd "/Users/user/untitled folder 2/harness" && rsync -a --exclude node_modules --exclude .next --exclude test-results section7/ "$S/section7/" && rsync -a contracts/ts/ "$S/contracts/ts/" && ln -sfn "/Users/user/untitled folder 2/harness/section7/node_modules" "$S/section7/node_modules" && cd "$S/section7" && npx next build
```

Warning: never run `next build` inside the real `section7/` while anyone may be running `next dev`
there. Dev and build share `section7/.next`; on 2026-11-13 a dev server overwrote a production build
and every script on the page returned 400. Always build in the copy.

How to test live against RC (the one allowed container action):
1. `docker ps --filter name=harness-rc-frontend` must show it running. Note that.
2. Build the copy above with these build-time variables exported first:
   `NEXT_PUBLIC_IDENTITY_BASE_URL=http://localhost:18001 NEXT_PUBLIC_ORCHESTRATOR_BASE_URL=http://localhost:18004 NEXT_PUBLIC_SANDBOX_BASE_URL=http://localhost:18005 NEXT_PUBLIC_MEETING_URL=http://localhost:18006 NEXT_PUBLIC_REVIEW_URL=http://localhost:18008 NEXT_PUBLIC_IDENTITY_WS_URL=ws://localhost:18001 NEXT_PUBLIC_GITHUB_URL=http://localhost:18002 NEXT_PUBLIC_MODELS_BASE_URL=http://localhost:28003 NEXT_PUBLIC_CONTEXT_BASE_URL=http://localhost:18011 NEXT_PUBLIC_ONBOARDING_BASE_URL=http://localhost:18010`
3. `docker stop harness-rc-frontend-1`, then in the copy `npx next start -p 3011` (RC's backend only
   accepts the origin `http://localhost:3011`).
4. Run Playwright from the real `section7/` with:
   `E2E_COMPOSE_PROJECT=harness-rc E2E_BASE_URL=http://localhost:3011 E2E_API_URL=http://localhost:18001 E2E_IDENTITY_URL=http://localhost:18001 E2E_ORCHESTRATOR_URL=http://localhost:18004 npx playwright test e2e/<spec> --retries=0`
   Allowed specs: `a11y.e2e.ts`, `palette.e2e.ts`, `home.e2e.ts`, and specs you write. Not
   `journey.e2e.ts` (it starts a paid run).
5. **Restore, always, even after a failure:** stop your `next start`, then
   `docker start harness-rc-frontend-1`, and confirm it shows `healthy`. Report in the hand-back
   that you restored it.

Without `E2E_*` set, the e2e helpers default to the demo stack's ports. Never run them unset.

# TASKS (in order; each ends with its checks passing or an honest status)

**T1. U1 finish.**
a. Bug: `CommandCenter.tsx` fills `projectNames` only when a project id is stored, so on a fresh
   session the home lists show short ids instead of names. Load names whenever projects load.
b. Add the plain-language action to the Runs page rows too: reuse `NEEDS_YOU` from `src/lib/triage.ts`
   so "Waiting for someone to approve the plan" etc. read the same in both places. Do not change
   the Runs page's filters or paging.
c. `e2e/home.e2e.ts`: keep it; it is the U1 acceptance test (oldest pending item opens by keyboard
   in under 10 s). Its keyboard leg will skip on RC until redeploy: mark `NEEDS-REVIEWER-RUN`.
Verify: vitest, tsc, build, `home.e2e.ts` and `a11y.e2e.ts` live (counts must match the API).

**T2. U5 slice: "Try the demo" loop.** On RC, `/demo` → "Try the demo" sends a signed-out visitor
to `/login?next=demo`; "Try the demo" on login returns to the same `/demo` card and nothing starts.
Find the cause (read `app/demo/`, `app/login/`, `src/lib/demoApi.ts`, the demo routes in section4
`demo.py`), then fix it in the frontend if the cause is in the frontend. If the cause is in the
backend or needs a decision (for example, the demo must require sign-in), stop and report the cause
with file:line. Do not start a live run to test it; the demo is a recorded replay and must stay one.
Verify: a Playwright spec `e2e/demo.e2e.ts` that goes from signed-out `/demo` to the replay view.

**T3. U2 run workspace, timeline slice.** On the run page, add a step timeline built from the run's
events (`replayEvents` / the existing event stream): one block per step with its label (`stepLabel`),
time, and a short receipt line when the event carries one (file path, command, result; nothing raw:
no diffs, logs or env values). Add a scrubber (keyboard: `j`/`k` or arrow keys move the selection,
`Home`/`End` jump) that shows the state at the selected step, and a "Follow" switch that keeps the
newest step selected while the run is live and turns off when the user moves the selection.
Pure functions in `src/lib/` for: grouping events into steps, the state at step N, follow on/off.
Verify: vitest with a synthetic 40-step event list (scrub to step 1, 20 and 40; follow turns off on
a manual move and back on when the switch is pressed); axe clean on the run page live
(`a11y.e2e.ts` already visits a run page when the stack has one).

**T4. U3 review, verdict card slice.** At the top of the review view, a verdict card with the same
facts as the Week 8 GitHub comment: plan approver and time, sandbox boundary, tests run and result,
cost (actual vs estimated, unknown when absent), risk flags ("not recorded" vs "none flagged" are
different), and the pre-review concerns when recorded. Read `section4/src/harness_orchestrator/verdict_comment.py`
for the exact wording rules and reuse that vocabulary. Data comes from endpoints that already exist
(review data, section8 risks, run status, cost); if one fact has no endpoint the browser can call,
show it as "not recorded" and list the gap in the hand-back. No approve shortcut, no auto-approval.
Verify: vitest for the fact-to-text function (every unknown case), axe on the review page live.

**T5. Phone width check.** At 375 px wide: home lists, palette, run timeline and verdict card must be
usable with no horizontal scroll. Fix only layout (CSS or utilities). Verify: a Playwright check at
`viewport: { width: 375, height: 812 }` asserting `document.documentElement.scrollWidth <= 375` on
`/`, the run page and the review page, plus screenshots saved to `docs/briefs/evidence/ui/`.

Stop after T5. U4 (trust portal) needs a backend role that does not exist yet, and U6 (presence) is
gated on an owner decision: do not start either.

# HAND-BACK

Write `docs/briefs/kimi-ui-handback-2026-11-13.md` with, per task:
- status: `DONE`, `PARTIAL`, `BLOCKED: <reason>` or `NEEDS-REVIEWER-RUN: <command> → <expected>`
- files changed (paths), and the `Shorter alternative:` line for any diff over 100 lines
- every command you ran and its result line (pass/fail counts, measured times)
- anything in this prompt that turned out wrong, with file:line
- unrelated bugs you saw (one line each, not fixed)
- confirmation that the RC frontend container was restored and is healthy
