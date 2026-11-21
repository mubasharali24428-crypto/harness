# UI build prompt: the new Harness workspace, one section per session (2026-11-14)

Supersedes the phase table in `ui-master-prompt-2026-11-14.md` for R1–R5 (R0 is done: ed62fd1..f7af86d). Same rules, more detail per section.

**How to use:** paste the SHARED PART into a coding agent whose working directory is the `harness/` repo root, then add one line: `Do section R1.` Each section ends with an owner gate. Do not start the next section until the owner says go.

---

## SHARED PART (paste every session)

You are rebuilding the web UI of the AI Engineering Harness into one working workspace. The owner's verdict on the current UI: it looks like an admin console, not a product. R0 fixed the data (demo diff, verdict, runs list) but not the look. This job is the look and the structure, on real data.

### Read first
- `docs/plans/ui-overhaul-plan-2026-11-13.md` §8 (findings F1–F10, three-pane target).
- `docs/briefs/evidence/ui/` (before shots, and `r0-after-*` shots of the current state).
- `section7/src/components/AppShell.tsx`, `RunTabs.tsx`, `WorkingArea.tsx`, `ChangeReview.tsx`, `CommandPalette.tsx`, `HomeTriage.tsx`, `app/globals.css`, `src/components/tokens.ts`, and `src/lib/` (`api.ts`, `runTimeline.ts`, `triage.ts`, `verdictFacts.ts`, `useEventStream.ts`, `humanize.ts`, `eventLabels.ts`).
- Reuse what exists. Move working components into the new layout before rewriting them.

### Where things are
- Frontend: `section7/` (Next 15, React 19, Tailwind v4 on CSS variables, cmdk, CodeMirror 6). Add no UI library without asking.
- Commands from `section7/`: `npm run typecheck`, `npm test`, `npm run e2e`, `npm run build`.
- Verify UI on the RC stack, frontend `http://localhost:3011`, backends `127.0.0.1:18001/18004/18005`. The `harness-ui-dev` server on `:3020` cannot sign in against RC (RC CORS allows only `http://localhost:3011`). Ask the owner before changing RC's allowed origins or adding any proxy. Until then, check a change by rebuilding only the RC frontend:
  `cd deploy && HARNESS_IMAGE_TAG=<new tag> docker compose -p harness-rc --env-file ../.env -f docker-compose.yml -f docker-compose.rc.yml build frontend` then `up -d --no-deps --no-build frontend`. Ask the owner first in each session before rebuilding; never `down`, never `down -v`, and touch only `harness-rc-*` containers.
- Other sessions edit the same checkout. Commit with `git commit --only <paths>`. Never commit other lanes' files. Do not push.

### The product
One workspace, three panes, replacing nine nav destinations and five run tabs.
- **Sidebar:** run list grouped Needs you / Working / Done, per project. Row = goal as title, state dot, spend, age. Project switcher on top. Cost, Meeting, Settings in a footer menu and the palette.
- **Centre:** the run as one scrolling column of step blocks. Collapsed block = one line (verb, target, duration, cost); opens to its receipt. Approval requests appear inline with their buttons. Verdict card at the end of a finished run. Scrubber and Follow stay.
- **Composer (bottom of centre):** the only input on a run. New goal on an empty run; direction or comment on a live one; `/pause /stop /retry /checkpoint`; `@path`. Two small controls: model (searchable, grouped by provider, only models with a configured key) and reasoning effort. Direction sent mid-run shows as queued until the backend reports it took effect.
- **Plan** pinned above the composer; steps tick as they finish; opening it gives an editable list with Approve. No JSON on screen.
- **Right pane**, one thing at a time, closable: Diff, Plan, Terminal, Evidence. Review = the Diff pane with the verdict card above it, inline comments that become direction, "since last iteration" as default.
- **When a run stops:** two-sentence recap, one question, one or two buttons.
- Command palette and shortcuts on every screen, hints in menus and tooltips.

### Visual rules
- Neutral greys, one accent. Accent only for the primary action and focus.
- No grid background, no fake window buttons, no clock, no decorative animation.
- Borders, not stacked cards: at most one card level.
- Four type sizes. Monospace only for code, paths, terminal output.
- Internal ids never appear as headings or labels. Runs are titled by goal; ids live in a "Copy id" menu and the Evidence pane. Backend state names (`pr_opened`, `plan_proposed`) never show raw; use `humanize.ts` / `eventLabels.ts`.
- Money: two decimals from one cent up, "< $0.01" below, "unknown" when not recorded. Never "$0" for unknown.
- Light and dark both pass contrast (the `themeContrast` test must keep passing).
- Motion 150–200 ms, opacity and transform only, honours `prefers-reduced-motion`; content visible without animation.

### Rules that decide whether work counts
1. **Real data only.** Every value comes from an API call or the event stream. No sample rows, lorem, screenshots standing in for components, or TODO buttons. If the backend cannot supply something, stop and report the missing endpoint; do not fake it in the client.
2. **Every state designed:** loading (skeleton within 100 ms), empty (says what to do next, with the action), error (plain sentence, cause if known, retry), unknown (shown as unknown). A header never says "loading" beside an error.
3. **Every control works or is not rendered.** A disabled control says why on hover and focus.
4. **Nothing is lost.** Everything the old tabs did stays reachable in ≤ 1 click or shortcut from the run, or moves to an Advanced drawer / Settings and is listed in your report. Old routes redirect.
5. **Honesty labels stay:** REPLAY on demo runs, the plan gate, risky paths needing explicit approval. The autonomy control may display tiers but must not auto-approve anything (decision P-D6 is open).
6. **Stability:** no console errors or failed requests on any walked screen; event stream reconnects and says so; reload on any URL lands on the same view; back/forward work; no layout shift when data arrives.
7. **Keyboard and a11y:** every action reachable by keyboard, visible focus, no single-key shortcut firing inside an input, axe clean, no horizontal scroll at 375 px.
8. **Small diffs.** Change what the section needs. Do not reformat or rename untouched code. Delete a component only when your change made it unused, and list it.

### How to work
- Before code: one `Assumptions:` line, then a numbered plan with a check per step. If there are two reasonable readings, or the backend lacks something, stop and ask.
- Read the existing component and its tests before replacing it.
- After each slice: `npm run typecheck && npm test`, then the relevant Playwright journey, then open the page on RC in a browser, use it, and read the console. Passing tests is not the same as the screen working.
- Commit per slice with a message saying what a user can now do.
- Name the exact verification you ran before saying done. If something could not be verified, say so.

### Report at the end of every section
1. What a user can now do that they could not before, in plain sentences.
2. Exact commands run and their counts.
3. Before/after screenshots in `docs/briefs/evidence/ui/` (dark and light, 1440 and 375) from the running app, plus the Playwright journey file name.
4. What you left out, could not verify, or stubbed (should be none), and any backend gap.
5. Components deleted or moved, and where moved controls now live.
6. The gate: what the owner must approve before the next section.

---

## SECTION R1: Look (three key screens, final tokens)

**Goal:** the owner sees the new look before any structure is rebuilt.

**Build**
- Final token set in `app/globals.css` / `tokens.ts`: greys, one accent, four type sizes, spacing, radii, border colours, for dark and light. Contrast checked by the existing test.
- Real routes `/preview/run`, `/preview/diff`, `/preview/review`, each reading the seeded demo run through the same API calls the live pages use (the demo seed gives a project, run, PR). Shown: sidebar run list, run column of step blocks, right Diff pane, verdict card above the diff.
- Strip from the shell: grid background, window buttons, clock. Remove the breadcrumb + banner stack above runs in the preview routes.
- Cover loading, empty, error and unknown states on each route; make them reachable (for example `?state=` is not allowed; use real conditions such as a run id that does not exist and a signed-out visit).

**Exit check:** all three routes render real demo data on RC with zero console errors; axe clean; no horizontal scroll at 375; `themeContrast` passes.

**Gate: STOP.** Send the owner dark and light screenshots at 1440 and 375 for all three routes. Do not start R2 until they approve or send changes.

---

## SECTION R2: Shell (three panes, tabs gone)

**Goal:** the app is one workspace; the old pages are reachable, not primary.

**Build**
- Three-pane layout as the app shell: sidebar run list (Needs you / Working / Done, per project, project switcher), centre run column, closable right pane (Diff / Plan / Terminal / Evidence).
- Replace the five run tabs: Overview → run column; Changes → Diff pane; Plan & work → Plan pane + composer plan strip (R3 completes it); Working → Terminal pane + controls; Team → Evidence pane section or an Advanced drawer. Move the task-statement form, JSON editors, search, export and data-deletion request to an Advanced drawer or Settings.
- Old routes redirect (`/runs`, `/review`, `/dashboard/cost`, tab query strings), with the old `?tab=` values mapping to the right pane.
- Footer menu: Cost, Meeting, Settings. Palette gets the same entries plus pane toggles with shortcut hints.
- Sidebar rows: goal title, state dot, spend, age, from the runs list and event stream. Tab title shows the count of runs needing the user.

**Exit check:** every former tab's content reachable in ≤ 1 click or shortcut; the run view has ≤ 3 visible inputs; axe clean; a recorded screen capture of navigation in a real browser window shows no blank frames (the in-app pane does not tick animation, so use a real window).

**Gate:** owner confirms the shell and the "where did X go" list before R3.

---

## SECTION R3: Composer and plan

**Goal:** a person can drive a live run from one input box.

**Build**
- Composer: new goal on an empty run; direction/comment on a live one; slash commands `/pause /stop /retry /checkpoint`; `@path` mentions from the project tree endpoint.
- Model picker: searchable, grouped by provider, only models with a configured key (fixes F5); reasoning-effort control.
- Queued direction: shown as queued until the backend reports it took effect (existing W23 guidance events).
- Pinned plan strip: steps tick as they complete; opens to an editable list with Approve; no JSON on screen.
- Inline approvals in the step column at the point they occur; risky paths still require explicit approval.
- Next-step box when a run stops: two-sentence recap, one question, one or two buttons, from run state and the existing "Needs you" copy.

**Exit check:** on a live (non-replay) run, one Playwright journey, keyboard only: propose → edit plan → approve → steer → stop. A model call may need a budget; ask the owner before any paid run and prefer a free or cheap model.

**Gate:** owner watches the journey recording before R4.

---

## SECTION R4: Review in the pane

**Goal:** a reviewer can decide a run from the verdict card and diff alone.

**Build**
- Verdict card above the Diff pane; diff from the review service for the run's PR; "since last iteration" default view with a toggle to the full PR diff.
- Inline comments on diff lines that become direction to the agent (uses the existing review comment and steering endpoints; report any gap).
- Per-file accept; approve and request-changes from the pane. Request changes requires a note, as today.
- A risky path still needs explicit approval: a negative test proves approval is refused until it is given.
- Merge stays human, on GitHub; the app never merges.

**Exit check:** Playwright: approve a run from the pane alone; negative test for a risky path; axe clean in the pane at 1440 and 375.

**Gate:** owner reviews one real review end to end before R5.

---

## SECTION R5: Evidence, cost and portal

**Goal:** the trust story is visible and the client can see it read-only.

**Build**
- Evidence pane: trace view (steps, tool calls, receipts), ids and "Copy id" live here.
- Cost page with one headline number and the money rules (F9); no duplicate headings or empty charts; "unknown" when unrecorded.
- Export of the run's evidence.
- Read-only client portal (plan item P-17): a member of the client's team sees runs and verdicts, never controls. Check the P-17 acceptance text in the plan before starting.

**Exit check:** P-17 acceptance met; cross-member negative tests pass (a member of another team cannot read the portal or its runs).

**Gate:** owner sign-off for the release candidate.

---

## After R5 (not part of this prompt)
First-run tour and presence (plan "Later", after decision P-D7). Pushing, tagging and any redeploy beyond the RC frontend need an explicit owner go-ahead.
