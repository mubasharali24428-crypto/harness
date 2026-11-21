# Hand-back — Harness UI tasks T1–T5 (2026-11-13)

Branch `w28-s1-delegate`, HEAD `576f9c1`. Work is uncommitted. No git writes, no deploys,
no model/paid API calls, no live run approvals or starts, no `journey.e2e.ts`, no demo-stack
containers. U4 and U6 were not started.

RC frontend restored and healthy: `harness-rc-frontend-1 Up (healthy) 127.0.0.1:3011->3000/tcp`,
frontend answers `HTTP 200` on `http://localhost:3011/`.

## Status summary

| Task | Status |
| --- | --- |
| T1 — U1 finish | **DONE** (live home + a11y passed, keyboard leg did not skip) |
| T2 — demo loop | **DONE** (live demo spec passed) |
| T3 — run timeline | **DONE** (unit + live a11y passed) |
| T4 — verdict card | **PARTIAL** — card built and unit-tested; real review-page data is blocked by Section 8 GitHub OAuth |
| T5 — phone width | **DONE** — all four surfaces measured at 375 px; review leg uses one documented stub |

---

## T1 — U1 finish

**Status: DONE.**

Files (these three were already modified in the working tree when this session started as
in-progress U1 work; they were preserved and verified, not rewritten):
- `section7/src/components/CommandCenter.tsx` — `listProjects` now runs whenever a token exists;
  `projectNames` is filled even when no project id is stored (fresh session), so the home lists
  show names instead of short ids.
- `section7/app/runs/runsTable.tsx` — renders `NEEDS_YOU[status]` under the status pill, reusing
  `src/lib/triage.ts`; filters and paging untouched.
- `section7/app/runs/runsTable.test.tsx` — asserts the home action for the three needs-you states
  and asserts an unknown status gets no invented action.

Verification:
- `npx tsc --noEmit` → clean.
- `npx vitest run` → **105 files / 1134 tests passed**, 3.70 s.
- `npx next build` (isolated copy, RC env) → completed; route table emitted for `/`, `/runs`,
  `/review`, run detail.
- Live: `E2E_COMPOSE_PROJECT=harness-rc E2E_BASE_URL=http://localhost:3011 E2E_API_URL=http://localhost:18001 E2E_IDENTITY_URL=http://localhost:18001 E2E_ORCHESTRATOR_URL=http://localhost:18004 npx playwright test e2e/demo.e2e.ts e2e/home.e2e.ts e2e/a11y.e2e.ts --retries=0`
  → **5 passed (10.0 s)**.
  - home: needs-you/working counts equal the API; oldest pending item focused and opened with
    keyboard in **0.16 s** (limit 10 s). The leg did **not** skip on this run.
  - a11y login page: **458 ms**; signed-in pages: **4.8 s**; run page: **1.5 s**.

Shorter alternative: no (all T1 edits are well under 100 lines).

---

## T2 — “Try the demo” loop

**Status: DONE.**

Cause: in the `?next=demo` flow the login page offered a second “Try the demo” button that pushed
`/demo` again while still signed out; `/demo` then sent the visitor back to `/login?next=demo`, so
nothing ever seeded.

Files changed:
- `section7/app/login/page.tsx` — when `next=demo`, the looping button is replaced by a note that
  the email form is the one step; after a successful login it navigates to `/demo?start=1`.
- `section7/app/demo/page.tsx` — `?start=1` auto-seeds the recorded replay once, and only when a
  session already exists (signed-out visitors still go through login).
- `section7/e2e/demo.e2e.ts` — new spec: signed-out `/demo` → seed click → `/login?next=demo` →
  no “Try the demo” button → email login → `/projects/.../runs/...?demo=1` → replay banner.

Verification:
- `npx tsc --noEmit` → clean; `npx vitest run` → 105 / 1134 passed.
- Live demo spec → **1 passed (638 ms)** in the combined run (an earlier isolated run passed in
  737 ms).

Shorter alternative: no.

---

## T3 — U2 run timeline slice

**Status: DONE.**

Files changed:
- `section7/src/lib/runTimeline.ts` (new) — pure rules: `groupEvents` (oldest-first; pairs
  `tool_call` with its `tool_result`; orphan results stay as their own block), `stateAtStep`,
  `scrubIndex` (`j`/`k`, arrows, `Home`/`End`), `reduceFollow` (off on manual move, on via switch).
  Receipts are one short line: file path, command or tool result. Env assignments and bearer tokens
  are stripped; output bodies, diffs and logs never enter a receipt.
- `section7/src/lib/runTimeline.test.ts` (new) — 40-step synthetic run: scrub to steps 1, 20 and 40;
  follow off on manual move and back on; pairing, failure text, secret redaction, clamping.
- `section7/app/projects/[projectId]/runs/[runId]/runTimeline.tsx` (new) — native range scrubber
  with `aria-valuetext`, step list buttons with `aria-current`, live region for the selected step,
  Follow checkbox that is only enabled while the run is live.
- `section7/app/projects/[projectId]/runs/[runId]/page.tsx` — fetches replay events, filters them to
  the current run, refreshes with the existing 3 s poll; failure keeps the aggregate fallback.
- `section7/app/projects/[projectId]/runs/[runId]/runDetailAggregate.tsx` — renders `RunTimeline`
  when events exist; the old compact “latest ≤100” list remains the fallback.

Verification:
- `npx tsc --noEmit` → clean.
- `npx vitest run` → **105 files / 1134 tests passed**, including 13 `runTimeline` tests.
- Live run page axe (a11y spec, demo replay) → **passed (1.5 s)**.
- Build → completed; run route emitted.

Shorter alternative: **No** — the 271-line library is six small pure functions and the 248-line
view is the rendering shell. Merging them would make the 40-step scrub/follow contract browser-only
and bury the receipt-redaction rules that keep raw payloads off screen.

---

## T4 — U3 review verdict card

**Status: PARTIAL.**

What is done:
- `section7/src/lib/verdictFacts.ts` (new) — six facts in Week 8 comment order: plan approval,
  sandbox boundary, tests, cost, risk flags, pre-review concerns. Vocabulary follows
  `verdict_comment.py`: “not recorded”, “none flagged”, “unknown”, exact scale-6 money. A missing
  result is never rendered as “passed”, a missing cost is never `$0`, and absent risk analysis is
  never “safe”.
- `section7/src/lib/verdictFacts.test.ts` (new) — 19 tests, every unknown/partial case.
- `section7/app/review/verdictCard.tsx` (new) — card renders the facts in the existing `Card`
  primitive, before the summary and diff. No approve shortcut, no auto-approval.
- `section7/app/review/page.tsx` — builds the facts from review data and the run aggregate; reads
  Section 8’s camelCase fields as well as the older snake_case ones.
- `section7/src/lib/api.ts` — `ReviewDto` now accepts `prUrl`, `changedFiles`, `riskFlags`,
  `testResults`, `approvedBy` alongside the old optional snake_case fields.

Verification:
- `npx tsc --noEmit` → clean.
- `npx vitest run` → **105 files / 1134 tests passed**, including 19 verdict-fact tests.
- Build → completed; `/review` emitted.
- Live review layout/axe: **passed** in `e2e/phone.e2e.ts` against one stubbed review response
  (375 px, WCAG 2.2 A/AA tags, zero violations).

Why PARTIAL: the synthetic demo PR cannot hydrate through Section 8 live. `/review?pr=<demo-pr>`
answers `github not connected for this user; complete the OAuth flow first`. The verdict card does
not depend on that call for its rules (unit-tested), but a real end-to-end review-page axe run
needs a user with GitHub connected. The stub is at `section7/e2e/phone.e2e.ts:53-76` and starts
nothing.

Shorter alternative: **No** — six independent unknown branches; a generic formatter would erase
the tested “not recorded” vs “none flagged” distinction.

---

## T5 — phone width at 375 px

**Status: DONE.**

Files changed:
- `section7/e2e/phone.e2e.ts` (new) — viewport 375×812; asserts
  `document.documentElement.scrollWidth <= 375` on home, command palette, run timeline and review
  page; runs axe on the review page; saves four screenshots.
- `docs/briefs/evidence/ui/phone-{home,palette,run,review}-375.png` — captured.

Verification:
- Phone spec → **1 passed (4.0 s total, 2.4 s test)**.
- Width checks passed on all four surfaces. No layout CSS change was needed.
- Axe review clean after the fix described below.

One test-side issue found and fixed: the first phone run reported 25 `color-contrast` violations
with foregrounds like `#989ea9` on `#fbfbfd` — i.e. the page’s 420 ms entrance fade was still
running when axe sampled computed colours. `home.e2e.ts` already documents this trap. The spec now
calls `page.emulateMedia({ reducedMotion: "reduce" })` before navigating; `globals.css` collapses
those animations to 0.01 ms for that media query. Re-run: **0 violations**. No colour token or
layout rule was weakened.

The review leg still uses the one stubbed response from T4 because the live demo PR needs GitHub
OAuth; the gap is the same one recorded there.

Shorter alternative: no.

---

## Commands actually run (final pass)

```text
cd "/Users/user/untitled folder 2/harness/section7" && npx tsc --noEmit && npx vitest run
→ tsc clean; 105 test files passed, 1134 tests passed, 3.70 s

# isolated production build with RC public env vars
S=/tmp/kimi-ui-build && rm -rf "$S" && mkdir -p "$S" && \
  cd "/Users/user/untitled folder 2/harness" && \
  rsync -a --exclude node_modules --exclude .next --exclude test-results section7/ "$S/section7/" && \
  rsync -a contracts/ts/ "$S/contracts/ts/" && \
  ln -sfn "/Users/user/untitled folder 2/harness/section7/node_modules" "$S/section7/node_modules" && \
  cd "$S/section7" && \
  NEXT_PUBLIC_IDENTITY_BASE_URL=http://localhost:18001 \
  NEXT_PUBLIC_ORCHESTRATOR_BASE_URL=http://localhost:18004 \
  NEXT_PUBLIC_SANDBOX_BASE_URL=http://localhost:18005 \
  NEXT_PUBLIC_MEETING_URL=http://localhost:18006 \
  NEXT_PUBLIC_REVIEW_URL=http://localhost:18008 \
  NEXT_PUBLIC_IDENTITY_WS_URL=ws://localhost:18001 \
  NEXT_PUBLIC_GITHUB_URL=http://localhost:18002 \
  NEXT_PUBLIC_MODELS_BASE_URL=http://localhost:28003 \
  NEXT_PUBLIC_CONTEXT_BASE_URL=http://localhost:18011 \
  NEXT_PUBLIC_ONBOARDING_BASE_URL=http://localhost:18010 \
  npx next build 2>&1 | tail -40
→ build completed; emitted the route table (/review 4.58 kB, run detail 166 kB)

E2E_BASE_URL=http://localhost:3011 npx playwright test e2e/phone.e2e.ts --retries=0
→ 1 passed (4.0 s)

E2E_COMPOSE_PROJECT=harness-rc E2E_BASE_URL=http://localhost:3011 \
E2E_API_URL=http://localhost:18001 E2E_IDENTITY_URL=http://localhost:18001 \
E2E_ORCHESTRATOR_URL=http://localhost:18004 \
npx playwright test e2e/demo.e2e.ts e2e/home.e2e.ts e2e/a11y.e2e.ts --retries=0
→ 5 passed (10.0 s); home keyboard leg 0.16 s
```

The 60–70 s wait between Playwright invocations was observed; no sign-in failures occurred.

---

## Prompt vs. code

1. **T4 says the facts come from “endpoints that already exist”.** They do not all exist for the
   browser. The review DTO carries no run id, so run status/cost are only available when the live
   event bus names a run (`section7/app/review/page.tsx:113-135`). Plan approver/time, sandbox
   boundary and pre-review concerns have no browser-callable endpoint; the card says “not recorded”
   (`section7/src/lib/verdictFacts.ts:40-75,136-153`).
2. **T4/T5 say to run axe on the review page live.** The live synthetic demo PR cannot hydrate:
   Section 8 answers `github not connected for this user; complete the OAuth flow first`. The
   review checks therefore use one stubbed response, documented at
   `section7/e2e/phone.e2e.ts:53-76`; the card’s rules are covered by unit tests.
3. **T1c predicted the home keyboard leg would skip on RC until redeploy.** It did not skip on this
   run: the API and the page both reported a needs-you item and the keyboard leg passed in 0.16 s.
   Either the RC stack already has the newer list behavior or the proposed plan is reflected; the
   spec compares against the API, so this is a real pass.
4. The prompt names commit `58bf8b8` or later; the checkout is `576f9c1` on `w28-s1-delegate`.

## Unrelated bugs / observations (not fixed)

- `section7/app/projects/[projectId]/runs/[runId]/runDetailAggregate.tsx` fallback heading says
  “latest N of M” while `timeline` is already `stepsNewestFirst(...)`; pre-existing wording, left
  untouched.
- The review page’s live data path depends on a Section 8 GitHub connection for the demo PR; this
  is the T4 live-verification gap above, not a frontend data bug.
- `e2e/home.e2e.ts` intentionally leaves one proposed plan on RC (`plan-af1303fc` this run) as its
  needs-you fixture; it does not start or approve anything.

## RC restoration

- Local `next start` on 3011 was stopped (port freed; Docker proxy now owns it).
- `docker start harness-rc-frontend-1` → `Up 6 seconds (healthy)`, later
  `Up 40 seconds (healthy) 127.0.0.1:3011->3000/tcp`.
- `curl http://localhost:3011/` → `HTTP 200`.

Not started: U4 (trust portal) and U6 (presence), per the prompt.
