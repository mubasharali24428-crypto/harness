# UI master prompt: rebuild the Harness web UI as one working workspace (2026-11-14)

Paste everything below the line into a coding agent whose working directory is the `harness/` repo root.
One phase per session is the intended use: say "do phase R0" (or R1 … R5) after the prompt.

---

You are rebuilding the web UI of the AI Engineering Harness. The owner's judgement of the current UI is that it
is not good enough, and the UI is the main thing people will see. Your job is a cleaner, simpler UI that is
fully functional against the real backend. A screen that looks finished but shows placeholder data, a static
picture, or a dead button counts as not built.

## Read first

1. `docs/plans/ui-overhaul-plan-2026-11-13.md`, §8 in full (test findings F1–F10, the three-pane target, phases
   R0–R5). Where §8 disagrees with §4–§5, §8 wins.
2. `section7/src/components/AppShell.tsx`, `CommandCenter.tsx`, `RunTabs.tsx`, `WorkingArea.tsx`,
   `ChangeReview.tsx`, `CommandPalette.tsx`, `HomeTriage.tsx`, and `section7/src/lib/` (`api.ts`, `auth.ts`,
   `runTimeline.ts`, `triage.ts`, `verdictFacts.ts`, `useEventStream.ts`, `humanize.ts`, `eventLabels.ts`).
   Reuse these; most of the data plumbing already exists and is tested.
3. `section7/e2e/README.md` and `e2e/support.ts` for how the Playwright journeys sign in and reach a stack.

## Where things are

- Frontend: `section7/` (Next.js 15, React 19, Tailwind v4 on the existing CSS variables in
  `app/globals.css`, `cmdk`, CodeMirror 6). No other UI library is installed; do not add one without asking.
- Commands, run from `section7/`: `npm run typecheck`, `npm test` (vitest), `npm run e2e` (Playwright + axe),
  `npm run build`.
- Dev server: launch config `harness-ui-dev` (port 3020) points at the RC backend on `127.0.0.1:18001/18004/18005`.
  Develop there. The RC frontend container is `:3011`; the `:3010` demo stack runs an old image.
- Other people and agents share the running stacks. Do not run `docker compose down`, never `down -v`, and do
  not rebuild or redeploy a stack unless the phase says so and the owner has confirmed in this session.

## The product you are building

One workspace, three panes, replacing nine nav destinations and five run tabs:

- **Sidebar: the run list**, grouped Needs you / Working / Done, per project. Each row: the run's goal as its
  title, a state dot, spend, age. Project switcher at the top; Cost, Meeting, Settings in a footer menu and the
  palette.
- **Centre: the run** as one scrolling column of step blocks. A block is one line when collapsed (verb, target,
  duration, cost) and opens to its receipt. Approval requests appear inline at the point they occur, with their
  buttons. The verdict card sits at the end of a finished run. A scrubber and Follow stay available.
- **Composer** at the bottom of the centre: the only input on the run. New goal on an empty run; direction or a
  comment on a live one; `/pause`, `/stop`, `/retry`, `/checkpoint`; `@path` file mentions. Two small controls
  on it: model (searchable, grouped by provider, only models with a configured key) and reasoning effort.
  Direction sent mid-run shows as queued until the backend reports it took effect.
- **Plan pinned above the composer**: steps tick as they complete. Opening it shows an editable list with
  Approve. No JSON on screen.
- **Right pane, one thing at a time, closable**: Diff, Plan, Terminal, Evidence. Review is the Diff pane with the
  verdict card above it, inline comments that become direction to the agent, and "since last iteration" as the
  default view.
- **When a run stops**: a two-sentence recap, one question, one or two buttons.
- **Command palette and shortcuts** on every screen, hints shown in menus and tooltips.

## Visual rules

- Neutral greys and one accent. The accent is for the primary action and focus only.
- No grid background, no fake window buttons, no clock, no decorative animation. Borders, not stacked cards: at
  most one card level.
- Four type sizes. Monospace only for code, paths and terminal output.
- Internal ids never appear as headings or labels. Runs are titled by their goal; ids live in a "Copy id" menu
  and the Evidence pane. Backend state names (`pr_opened`, `plan_proposed`) are never shown raw; use
  `humanize.ts` / `eventLabels.ts`.
- Money: two decimals from one cent up, "< $0.01" below it, "unknown" when not recorded. Never "$0" for unknown.
- Light and dark both pass contrast (the `themeContrast` test must keep passing).
- Motion: 150–200 ms, opacity and transform only, respects `prefers-reduced-motion`. Content must be visible
  without any animation having run.

## Rules that decide whether work counts

1. **Real data only.** Every value on screen comes from an API call or the event stream. No hardcoded sample
   rows, no lorem text, no screenshots standing in for components, no `TODO` buttons. If the backend cannot
   supply something the design needs, stop and report the missing endpoint; do not fake it in the client.
2. **Every state is designed**: loading (skeleton within 100 ms), empty (says what to do next, with the action),
   error (plain sentence, the cause if known, a retry), and unknown (shown as unknown). A header must never say
   "loading" beside an error.
3. **Every control works** or is not rendered. A disabled control says why on hover and focus.
4. **Nothing is lost.** Everything the old tabs did stays reachable in at most one click or shortcut from the
   run, or moves to an Advanced drawer / Settings and is listed in your report. Old routes redirect.
5. **Honesty labels stay**: REPLAY on demo runs, the plan gate, risky paths requiring explicit approval. The
   autonomy control may display tiers but must not auto-approve anything (owner decision P-D6 is open).
6. **Stability**: no console errors or failed requests on any walked screen; the event stream reconnects after a
   dropped connection and the UI says so; a reload on any URL lands on the same view; back/forward work; no
   layout shift when data arrives.
7. **Keyboard and accessibility**: every action reachable by keyboard, visible focus, no single-key shortcut
   firing inside an input, axe clean, no horizontal scroll at 375 px.
8. **Small diffs.** Change what the phase needs. Do not reformat or rename untouched code. Delete components
   only when your change made them unused, and list them.

## Phases

Do the one phase you are asked for. Each ends with the exit check passing and the evidence listed below.

| Phase | Build | Exit check |
|---|---|---|
| **R0 Demo truth** | The replay run shows its diff and its verdict without a GitHub connection (F1, F2); the Review header cannot show "loading" with an error; Runs footer count is correct and the project column is consistent (F6). Find the cause in the demo seed / review data path before changing UI. Report whether `:3010` should be rebuilt or retired (F4); do not do either yourself | A signed-out visitor reaches a run with a non-empty diff and a populated verdict card in ≤ 2 clicks; a Playwright journey asserts both |
| **R1 Look** | The three key screens (sidebar + run; run + diff pane; review) as real routes behind `/preview/*`, reading the seeded demo run, in the final token set with the visual rules applied. Remove the grid, window chrome and clock from the shell | Owner approves screenshots (dark and light, 1440 and 375) before R2 starts. Stop and ask |
| **R2 Shell** | Three-pane layout, sidebar run list, right pane, tabs removed, old routes redirect, Advanced drawer for the moved controls | Every former tab's content reachable in ≤ 1 click or shortcut; the run view has ≤ 3 visible inputs; axe clean; a recorded screen capture of navigation in a real browser window shows no blank frames |
| **R3 Composer and plan** | Composer, slash commands, `@path`, searchable model picker (F5), pinned plan, plan editor, inline approvals, queued direction, next-step box | On a live (non-replay) run: propose → edit plan → approve → steer → stop, keyboard only, in one Playwright journey |
| **R4 Review in pane** | Verdict card + diff in the right pane, inline comment → direction, per-file accept, "since last iteration" | A reviewer approves a run from the verdict card and diff alone; a risky path still requires explicit approval (negative test) |
| **R5 Evidence and portal** | Trace view, cost page with one headline number (F9), export, the read-only client portal (P-17) | P-17 acceptance; cross-member negative tests pass |

## How to work

- Before code: state your assumptions in one line, and a numbered plan with a check per step. If the request
  has two reasonable readings, or the backend lacks something, stop and ask.
- Read the existing component and its tests before replacing it. Prefer moving a working component into the new
  layout over rewriting it.
- After each slice: `npm run typecheck && npm test`, then the relevant Playwright journey. Open the page in a
  browser, use it, and read the console. Tests passing is not the same as the screen working.
- Commit per slice with a message that says what a user can now do. Do not push.

## Report at the end of the phase

- What a user can now do that they could not before, in plain sentences.
- The exact commands you ran and their results (counts, not "passes").
- Before/after screenshots in `docs/briefs/evidence/ui/` (dark and light, 1440 and 375) taken from the running
  app, plus the Playwright journey file name.
- Anything you left out, could not verify, or faked nowhere but had to stub (there should be none), and any
  backend gap you hit.
- Components deleted or moved, and where the moved controls now live.
