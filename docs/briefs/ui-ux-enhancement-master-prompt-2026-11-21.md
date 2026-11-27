# UI/UX enhancement master prompt (2026-11-21)

Paste everything below the line into a coding agent whose working directory is the `harness/` repo root.
Run **one phase per session**: after the prompt, say "do phase E0" (or E1 … E9). Phases E0 and E1 come first, in order;
after that the order in §7 is the default but the owner may reorder.

This prompt **follows** `docs/briefs/ui-master-prompt-2026-11-14.md` (phases R0–R5, all done and browser-verified on the
live `vv90r6` stack on 2026-11-21, `docs/briefs/evidence/90day/ui-walk-vv90r6-2026-11-21.md`). That rebuild fixed
*structure* (one workspace, three panes, real data, honest panels). This one is about *feel and depth*: the owner's
target is a UI that is **smooth and feature-rich like Claude Code, visually as simple as Codex, and built around Harness's
own features** (plan gate, confined execution, human acceptance, metered spend, multiplayer review).

---

You are the UI/UX lead for the AI Engineering Harness web app (`section7/`). The structure is right; the experience is
not yet great. Your job is to make every screen feel fast, calm and obvious, without adding clutter, faking data, or
weakening any safety or honesty rule. The UI is the main thing buyers and design partners see: treat every pixel as
product.

## 1. Read first (in this order, before any code)

1. `docs/plans/ui-overhaul-plan-2026-11-13.md` §8 (findings F1–F10, the three-pane target) and
   `docs/briefs/ui-master-prompt-2026-11-14.md` (the rules below extend it; where they differ, this file wins).
2. Phase reports: `docs/briefs/evidence/ui/r1-report-2026-11-14.md` … `r5-report-2026-11-15.md`,
   `u-leftovers-report-2026-11-15.md`, `u-open-items-report-2026-11-15.md`, `week-ui.md`, `sr-journey-2026-11-15.md`,
   and the live walk `docs/briefs/evidence/90day/ui-walk-vv90r6-2026-11-21.md`.
3. Owner decisions (signed 2026-11-13): `docs/decisions/2026-11-13-du1-component-stack.md` (Tailwind v4 + shadcn/ui on
   Radix, owned source, + cmdk, migrate page by page), `du2-visual-direction.md` (dark-first, one accent, light is a
   supported second theme), `du3-u0-before-p17.md`, `du4-risk-tiers.md` (risk tiers OFF: every approval explicit).
4. `docs/briefs/perf-audit-2026-11-21.md` §1 and §3. **Key UX fact: 98.5% of a run's wall time is the model thinking.**
   The UI cannot make the model faster; it must make waiting legible, interruptible and calm.
5. Code you will touch most:
   - Shell and workspace: `section7/src/components/AppShell.tsx`, `src/components/workspace/*`
     (`RunColumn.tsx`, `Composer.tsx`, `PlanPin.tsx`, `RunPrompts.tsx`, `DiffPane.tsx`, `ReviewPane.tsx`,
     `RunEvidence.tsx`, `PresenceAvatars.tsx`, `useRun.ts`, `useRunList.ts`, `panes.ts`, `runFlow.ts`, `reviewFlow.ts`,
     `trace.ts`).
   - Primitives today: `src/components/ui/*` (hand-written `Button`, `Card`, `Modal`, `Notice`, `Pill`, `Table`, `Brand`)
     and `app/globals.css` (**1,919 lines** of hand-written CSS on CSS variables). `src/components/tokens.ts`.
   - Data plumbing (reuse, do not rewrite): `src/lib/api.ts`, `useEventStream.ts`, `runTimeline.ts`, `deriveView.ts`,
     `humanize.ts`, `eventLabels.ts`, `format.ts`, `verdictFacts.ts`, `triage.ts`, `hunkReview.ts`, `commands.ts`,
     `focus.ts`, `a11y.ts`, `theme.ts`, `intl.ts`, `redact.ts`.
   - Tests: `src/**/*.test.ts(x)` (vitest), `e2e/*.e2e.ts` (Playwright + axe), `e2e/README.md`, `e2e/support.ts`,
     local check scripts `scripts/r2-check.mjs`, `r3-check.mjs`, `r4-check.mjs`, `u-check.mjs`, `sr-journey.mjs`.

## 2. Where things run, and what you must not do

- Commands from `section7/`: `npm run typecheck`, `npm test`, `npm run build`, `npm run e2e`.
- RC stack frontend: `http://localhost:3011` (backend loopback 18001–18008). Verify UI there. The dev server
  `harness-ui-dev` (:3020) **cannot sign in** against RC (RC CORS allows only :3011); use the `scripts/*-check.mjs`
  pattern (prod build on :3100 with request interception) for local checks, then the RC after a rebuild.
- `:3010` is the old demo stack (`vw24b2` image). Never judge the UI there.
- The built-in browser pane does not tick CSS animations: judge motion in a real browser window or with a Playwright
  trace/video, never from a pane screenshot.
- Other sessions and agents share the stacks and the checkout.
  - Never `docker compose down`, never `down -v`, never rebuild or redeploy any stack without the owner's "yes" **in this
    session**. When you rebuild the RC frontend, pass `HARNESS_IMAGE_TAG` without the leading `v`.
  - Back-to-back e2e runs trip the identity login rate limit (429): space them out.
  - `e2e/composer.e2e.ts`, `journey.e2e.ts` and `real.e2e.ts` start **paid** runs: ask before running them.
  - Commit per slice with `git commit --only <paths>`; never `git add -A`; never commit `.openclaw/`, `FUTURE PLAN 2.0.md`,
    `docs/briefs/evidence/eval/*.json` or an `AGENTS.md` carrying a `<claude-mem-context>` block. **Do not push.**
- Adding a dependency: only the ones D-U1 already approved (Radix primitives through shadcn's owned-source pattern,
  `class-variance-authority`/`clsx`/`tailwind-merge` if the shadcn pattern needs them, `lucide-react` for icons). Any
  other package (animation library, virtual list, toast library, date library) needs the owner's yes first, with the
  size cost and the native alternative stated.

## 3. The experience you are building (north star)

Think of three references and take one thing from each:

| Reference | Take this | Do not take this |
|---|---|---|
| Claude Code | The run as a live, scannable transcript: every tool call a one-line block that opens to its receipt; streaming output; interrupt anytime; slash commands; `@` mentions; a plan that ticks; "what happened / what now" recap at the end | Terminal look, monospace everywhere, walls of text |
| Codex app | Visual calm: neutral greys, one accent, lots of space, almost no chrome, one thing in focus, diff-first review | Hiding cost, hiding what the agent did |
| Linear / Superhuman | Speed as a feature: keyboard for everything, instant optimistic UI, ⌘K reaches every action, no spinners where a skeleton or the last known value will do | Dense tables of settings on the first screen |

And the Harness-only parts no competitor has, which must be **the most visible things on screen**, not tucked away:
the plan gate (nothing runs before a human approves), confined execution (what the sandbox allowed and refused, and
why), human acceptance (a human merges; the verdict card is advisory), metered spend (cost per run, live, honest), and
multiplayer review (who is here, who approved, who is waiting on whom).

### Experience principles (each phase is judged against these)

1. **One thing in focus.** At any moment the screen has one obvious next action, drawn with the accent. Everything else
   is neutral.
2. **Waiting is designed.** While the model thinks: show what it is doing in plain words ("Reading `src/retry.py`",
   "Thinking about the plan · 12 s"), elapsed time, spend so far, and an always-visible Stop. Never a bare spinner.
3. **Instant feedback.** Every click or key gives visible feedback in ≤ 100 ms (optimistic state, pressed state, or a
   skeleton). Server-confirmed state replaces optimistic state without a jump; a failure rolls back and says why.
4. **Nothing jumps.** No layout shift when data arrives (reserve space; CLS ≤ 0.05). Autoscroll follows the live run
   only while the user is at the bottom; scrolling up pauses Follow and shows a "Jump to live" pill.
5. **Plain words.** No internal ids, raw state names, JSON or stack traces on screen (use `humanize.ts`,
   `eventLabels.ts`). Ids live behind "Copy id" and in the Evidence pane.
6. **Honest by default.** Unknown is shown as unknown; estimates are labelled estimates; REPLAY is labelled; denials are
   shown with their reason and the alternative the gate offered; the verdict card never says "approved to merge".
7. **Progressive disclosure.** Collapsed by default, one click to the receipt, one more to raw evidence. Power features
   live in the palette and shortcuts, not in more visible buttons.
8. **Same feel everywhere.** Settings, cost, meeting, portal, docs, login, onboarding and pricing use the same tokens,
   primitives, spacing and states as the workspace.

## 4. Visual system (E1 builds it; every later phase uses only it)

- **Tokens** (one source, CSS variables consumed by Tailwind v4 `@theme`): color (neutral scale 0–12, one accent with
  hover/active/subtle, success/warning/danger/info each with fg/bg/border), type (four sizes: 12/14/16/20, plus one
  display size for landing only; two weights; one sans, one mono), spacing (4-px scale), radius (2 values), border
  (1 width, 2 strengths), elevation (popover and modal only), motion (durations 120/180/240 ms, two easings),
  z-index (named layers).
- **Primitives** (shadcn owned source in `src/components/ui/`, replacing the hand-written ones one at a time):
  Button, IconButton, Input, Textarea, Select/Combobox, Checkbox, Switch, Tabs (settings only), Tooltip, Popover,
  DropdownMenu, ContextMenu, Dialog, Sheet (phone right pane), Toast (one region, polite), Badge/Pill, Kbd, Skeleton,
  ScrollArea, Separator, Avatar, Progress, EmptyState, ErrorState.
- **Rules**: neutral greys + one accent (primary action and focus ring only); at most one card level, borders not
  shadows; monospace only for code, paths, terminal; icons 16 px with a text label or an `aria-label`; light and dark
  both pass `themeContrast.test.ts` (AA text 4.5:1, UI 3:1); no grid backgrounds, fake window buttons, clocks or
  decorative animation.
- **Motion**: opacity and transform only; 120–240 ms; enter/exit for popovers, sheets, toasts, pane open/close, step
  block expand; honour `prefers-reduced-motion` (no movement, instant state); content is fully visible and usable with
  zero animation frames.
- **Money and time** (`format.ts`): two decimals from one cent up, "< $0.01" below, "unknown" when not recorded, never
  "$0" for unknown; relative times ("2 min ago") with the absolute time in a tooltip and a `<time>` element.

## 5. Rules that decide whether work counts

1. **Real data only.** Every value comes from an API call or the event stream. No sample rows, lorem, screenshots as
   components, or `TODO` buttons. `workspace/previewData.ts` is for tests and stories only; nothing in a shipped route may
   import it (add a test that fails if one does). If the backend cannot supply something, **stop and report the missing
   endpoint**; do not fake it in the client.
2. **Every state designed**: loading (skeleton ≤ 100 ms), empty (what to do next + the action), error (plain sentence,
   cause if known, retry), unknown (shown as unknown), offline/reconnecting (banner; actions that need the server are
   disabled with the reason), permission-denied (who can do it instead).
3. **Every control works or is not rendered.** A disabled control explains why on hover and focus.
4. **Nothing lost.** Every capability reachable today stays reachable in ≤ 1 click or shortcut. Old routes redirect.
   Your report lists anything moved.
5. **Safety and honesty stay**: plan gate, explicit approval for risky paths, no auto-approve (D-U4), REPLAY labels,
   advisory-only verdict, estimates labelled. A UI change that makes approving easier than reading is a regression.
6. **Stability**: zero console errors and zero failed requests on every walked screen; the event stream reconnects and
   says so; reload on any URL restores the same view (pane, scroll anchor, open block where practical); back/forward work.
7. **Accessibility (WCAG 2.2 AA)**: everything by keyboard, visible focus, no single-key shortcut fires inside an input
   or with a modifier held, axe clean on every route, no horizontal scroll at 320 and 375 px, targets ≥ 24 px, live
   regions for run progress that do not spam (`LiveAnnouncer.tsx`), focus moves on route change (already built: keep it).
8. **Performance budgets** (measured on a prod build, mid-range laptop profile, and a 4× CPU-throttled phone profile):
   LCP ≤ 2.0 s on `/` and a run page, INP ≤ 200 ms on the composer, run column and diff pane, CLS ≤ 0.05, first-load JS
   per route reported and not grown by more than 10% per phase without a stated reason. A run with 2,000 events
   scrolls at 60 fps (virtualise or window if it does not; ask before adding a library).
9. **Small, surgical diffs.** Change what the phase needs. Do not reformat or rename untouched code. Delete only what
   your change made unused, and list it.
10. **Tests move with the code.** Every new behaviour gets a vitest unit test and, for a user journey, a Playwright step.
    Fix stale specs your change breaks in the same slice.

## 6. Known backend gaps (do not fake these; build the UI so it lights up when they land)

| Gap | Effect on UI | What to do |
|---|---|---|
| No per-plan-step progress event | The pinned plan cannot tick steps from server truth | Show steps without ticks, or derive ticks only from an explicit, tested mapping of existing events, labelled "inferred". Report the endpoint/event needed |
| Model catalog has no "key configured" flag | The model picker cannot hide models without a key | Show all, mark the current team's known-good ones from settings if available; report the field needed |
| No "since last iteration" data | Review cannot default to "changes since my last review" | Keep "all changes" as default; report the snapshot diff endpoint needed |
| Runs list paging is cursor-based; no server-side search by goal | Sidebar search is client-side over loaded pages | Say so in the search empty state ("searched N loaded runs · Load more") |
| Guest `/report` session not exercised end-to-end | Portal guest view unverified | E7 builds the journey against a guest grant on RC; owner approves creating the guest |

Any new gap you find goes in this table in your report, with the screen it blocks.

## 7. Phases

Each phase ends with its exit check passing **and** the evidence in §9. Stop at the end of the phase.

| Phase | Build | Exit check |
|---|---|---|
| **E0 Baseline and guard rails** | (a) Route inventory of all 29 `page.tsx` routes with owner component, data source, states covered, primitives used, `globals.css` selectors used. (b) A visual baseline spec (`e2e/visual.e2e.ts`) that screenshots every route at 1440 and 375, dark and light, from the RC stack, masking times and ids. (c) A performance script (`scripts/perf-check.mjs`) that records LCP/INP/CLS and first-load JS per route on a prod build. (d) The "no previewData in shipped routes" test. (e) A short UX audit against §3 principles: top 20 issues ranked by user impact, each with a screenshot. **No UI changes in E0** | Inventory, baseline images, perf numbers and the ranked audit committed under `docs/briefs/evidence/ui-e/`. Owner reads the audit and may reorder E2–E9 |
| **E1 Design system** | Tokens per §4 in one file; shadcn/Radix primitives in `src/components/ui/`; a living reference page at `/dev/ui` (dev builds only, 404 in production) rendering every primitive in every state and theme; migrate the AppShell and the nine settings pages onto them; delete the `globals.css` rules your migration made unused | `globals.css` shrinks (report lines before/after); every primitive has a unit test for its states and keyboard behaviour; axe clean on `/dev/ui`; visual diff of migrated pages reviewed; **owner approves screenshots before E2** |
| **E2 Shell, navigation and speed** | Sidebar: grouped Needs you / Working / Done with counts, project switcher that always shows the current project (fixes the blank switcher on demo runs, `week-ui.md` finding 2), client search over loaded runs, keyboard j/k + Enter, unread dot for runs that changed since last viewed (client-side, from event timestamps). Palette: every action and route, recent items, fuzzy match, shortcut hints. Prefetch run data on hover/focus. Route transitions with no blank frame. Collapsible sidebar (⌘B), resizable right pane with the width remembered per viewer | INP ≤ 200 ms on sidebar and palette; a real-window screen recording (dark + light) of navigating 10 runs by keyboard shows no blank frame; palette reaches 100% of routes (extend the existing route-walk test) |
| **E3 The live run (the Claude Code feel)** | Run column as a transcript of step blocks: one line collapsed (icon, verb, target, duration, cost), opens to its receipt (command + output, file read range, edit as a mini-diff, model turn summary). Streaming model text where the event stream provides it. A **thinking/working indicator** that names the current activity, elapsed time and spend so far. Follow/Jump-to-live pill; scroll anchoring; keyboard: ↑/↓ between blocks, Enter to open, `o` to open in the right pane. **Denials as first-class blocks**: what was refused, the gate's reason, the alternative it offered, and (if present) the scope-request editor inline. Approval requests inline where they occur. Reconnect banner with retry countdown (`backoff.ts`). End-of-run recap: two sentences, one question, ≤ 2 buttons | A 2,000-event recorded run scrolls at 60 fps and opens any block in ≤ 100 ms; reload restores scroll anchor; a dropped stream reconnects and says so (Playwright with network offline/online); keyboard-only walk of a full replay run |
| **E4 Composer power** | Composer as the single input: multiline with auto-grow, ⌘Enter send, Esc blur; slash menu (`/pause`, `/stop`, `/retry`, `/checkpoint`, `/model`, `/effort`, `/help`) with descriptions and shortcuts; `@path` mentions with fuzzy file search from the project tree route; per-run draft kept across reload (per-viewer browser storage, wrapped in try/catch); input history (↑ in empty composer); attachments only if the backend accepts them (else not rendered). Model picker: searchable combobox grouped by provider, deduplicated names (F5), recent models first, current price per 1M tokens if `pricing.ts` has it. Queued direction shows "queued → delivered" from the existing delivered signal | Keyboard-only Playwright: propose → edit plan → approve → steer with a slash command and an `@` mention → stop, on a replay or dry run (ask before any paid run); unit tests for the slash parser and mention matcher |
| **E5 Review and diff** | Diff pane: unified and split views, file tree with per-file status and reviewed ticks, `j/k` files, `n/p` hunks, `r` mark reviewed, comment on a line or a hunk (`hunkReview.ts`), comments become direction with a preview of what the agent will receive. Syntax highlight via the existing CodeMirror setup, large-file guard ("Show 1,240 more lines"), whitespace toggle. Verdict card above the diff, each recorded fact linked to its evidence. Risky-path approval keeps its explicit tick. Approve / Request changes as the primary pair, request-changes disabled until text exists (keep) | A reviewer approves a run from the pane alone by keyboard; a risky path cannot be approved without its tick (negative test kept); a 5,000-line diff stays responsive (INP ≤ 200 ms) |
| **E6 First five minutes** | Landing (`/`), `/pricing`, `/demo`, `/login`, `/onboarding` on the new system. Demo: one click from landing to a running REPLAY with the diff and verdict visible (keep the ≤ 2-click promise), a five-step guided overlay that points at the plan gate, a denial, the diff, the verdict and the cost (dismissible, never shown again once dismissed). Onboarding: connect provider key → first project → first goal, with progress and skip; every empty state in the workspace teaches the next action. Copy pass on all of these (plain, short, honest; keep the existing honesty sentences verbatim) | A first-time visitor reaches a populated demo run in ≤ 2 clicks and finishes the tour in ≤ 90 s (timed Playwright); onboarding completes keyboard-only; LCP ≤ 2.0 s on `/` |
| **E7 Multiplayer and trust portal** | Presence: who is viewing this run (avatars already in `PresenceAvatars.tsx`), who is typing direction, "X approved the plan 2 min ago" in the transcript. Handoff and leader-approval waits shown as plain blocks ("Waiting for Sara to approve · Remind"). Notifications page and an in-app inbox for Needs-you items. Portal: guest `/report` journey end-to-end on RC (guest grant, guest login, read-only view, no write controls rendered, cross-project negative test) | `e2e/multiplayer.e2e.ts` (pilot smoke stack) and a new guest-report journey pass; a guest sees no control that writes; presence updates within the push socket's interval |
| **E8 Accessibility, phone and polish** | Phone (375 px): right pane becomes a bottom Sheet, composer stays reachable above the keyboard, sidebar becomes a drawer, `PhoneTriage` matches the new system. Reduced-motion audit. Screen-reader pass prepared: `scripts/sr-journey.mjs` extended to every route, plus a written script for a human VoiceOver/NVDA run (the human run itself is owner-arranged). High-contrast check. Focus-order audit on every route. Error-copy pass | axe clean on all routes in both themes; no horizontal scroll at 320/375 on any route; sr-journey report covers 100% of routes; reduced-motion snapshot shows identical content |
| **E9 The rest of the house** | `/dashboard/cost` (one headline number, by-day chart only when there is data, per-run and per-model breakdowns, export), `/meeting`, `/docs`, `/notifications`, `/projects/[p]/tree`, checkpoints, settings sections: all on the new primitives with every state designed. Remove dead CSS and components left behind | `globals.css` contains only tokens, resets and genuinely global rules (report final size); E0 visual baseline re-run and every diff explained; perf budgets met on every route |

## 8. How to work (every phase)

1. Start with `git status --short` and `git log --oneline -5`; read the phase row and the files it names.
2. Before code, write one line starting `Assumptions:` and a numbered plan where each step has its own check
   (`1. step → verify: check`). If the phase has two reasonable readings, or a backend gap blocks it, stop and ask.
3. Read the existing component **and its tests** before replacing it. Prefer moving a working component onto new
   primitives over rewriting it.
4. After each slice: `npm run typecheck && npm test`, the relevant Playwright spec, `npm run build`, then open the page
   in a real browser, use it with mouse and keyboard, and read the console and network tab. Tests passing is not the
   same as the screen working.
5. Commit per slice, by path, with a message that says what a user can now do. Do not push. Ask before any RC rebuild.

## 9. Report at the end of each phase

Write `docs/briefs/evidence/ui-e/<phase>-report-<date>.md` containing:

- What a user can now do that they could not before, in plain sentences.
- Exact commands run and their results as counts (tests passed/failed/skipped, axe violations, LCP/INP/CLS numbers,
  JS size before → after).
- Before/after screenshots (1440 and 375, dark and light) from the running app in `docs/briefs/evidence/ui-e/`, and for
  motion work a real-window recording.
- The §3 principles this phase moved, with one example each.
- Anything left out, unverified, or blocked; new backend gaps added to §6; components deleted or moved and where moved
  controls now live.
- Commit hashes (local, not pushed) and whether the RC stack shows this work yet (it does not until an approved rebuild).
