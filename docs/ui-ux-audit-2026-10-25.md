# UI/UX audit — 2026-10-25

> **Status:** Batch A (items 1, 2, 3, 4, 6, 7, 8, 9) is implemented and verified
> live — see "Batch A outcome" at the bottom. Batches B–D and the contracts
> decision (item 11) are still awaiting review. **2026-10-31 (W24):** all 25 items rechecked
> against the code — 7 closed, 8 partly, 10 open; see "W24 recheck" at the bottom.

Scope: the `section7` frontend, run against the live compose stack
(`http://localhost:3000`) plus a full read of the run, settings and entry
surfaces. Evidence is either a measured number, a screenshot, or a
`file:line` in code. Benchmark set: Claude Code, OpenAI Codex, Cursor, Devin —
specifically how each shows *what the agent is doing right now*, how it asks
for permission, and how it makes a diff reviewable.

Verdict in one line: the platform's safety story is excellent and the empty
states are unusually honest, but the **run surface reads like a debug console,
not a product** — and in light mode the settings hub is close to unreadable.

---

## Tier 1 — broken or actively misleading (small fixes, high trust payoff)

| # | Finding | Evidence | Proposed fix |
|---|---------|----------|--------------|
| 1 | Internal engineering notes are rendered to end users. The diff panel prints "Section 5's sandbox is request/response only — it doesn't emit in-progress diffs to the event bus, and no EventType for in-progress diffs in the frozen contract yet…"; cost prints "Actual cost: — (no live source yet)"; comments print "no backend endpoint exists yet to persist or deliver these to the agent". | screenshot `/tmp/ui-audit/run_working.png`; `src/components/DiffPanel.tsx`, `PlanPanel.tsx:31-36`, `CommentsNudges.tsx:21-42` | Ship a real empty state ("Diff appears when the agent finishes its first edit batch") and move every contract/section explanation to `docs/`. Honesty is a virtue here — it's just addressed to the wrong audience. |
| 2 | Terminal panel always claims there is no feed, while printing feed lines directly underneath it. | `run_working.png`; `WorkingArea.tsx:88` passes `results={[]}` permanently → `TerminalPanel.tsx:31-35` | Derive results from the event stream, or suppress the empty-state line when `lines.length > 0`. |
| 3 | Light theme: **every link in the Settings hub is 1.76:1 contrast** (`#93c5fd` on `#fdfcfa`) — far under WCAG AA 4.5:1. Phase pill "no-plan" measures 2.54:1. | measured with a headless contrast pass over 12 pages × 2 themes; `app/settings/page.tsx:64-65`, `app/globals.css:317-319` | Replace literal hex with the existing theme tokens (`--link`, `--success`, `--warn`) and add a contrast assertion to the a11y test lane so it can't regress. |
| 4 | Run page overflows horizontally at 390px; the tab bar wraps and clips. | measured `scrollWidth > innerWidth`, screenshot `run_mobile.png` | Let the tab bar scroll-x with snap, collapse breadcrumbs, and retest at 320/390/768. |
| 5 | "Changes" tab says "No snapshots yet… there is simply no change history for this project yet" for a run that produced a diff and an open PR. It is project-scoped, not run-scoped, so it can also restore *another* run's snapshot. | screenshot `run_changes.png`; `page.tsx:204`, `ChangeReview.tsx:261,290` | Scope the snapshot list to the run, and make Revert (currently unconfirmed, unlike Restore) show a consequence modal. |
| 6 | The human-takeover checkbox fires a write the instant it is ticked: `submitHumanEdit(runId, "(unspecified)", …)` before the user typed a path. | `Controls.tsx:55-59` | Toggle should only reveal the editor; submit stays on an explicit button. |
| 7 | Team switcher is a fake control — a `<select>` with one hardcoded option and no `onChange`. | `AppShell.tsx:124-126` | Render it as static text until multi-team switching exists. |
| 8 | Model picker (just added) keeps showing the rejected model after a failed save; status says "Not saved" while the dropdown shows the new value. | `ModelPrefs.tsx:90-110` (no rollback in `.catch`) | Snapshot-and-restore on failure, the way `settings/team` already does. |
| 9 | Review page swallows every load error → permanent "loading diff…" with no retry; and "Request changes" can submit an empty comment array while printing "Decision recorded". | `app/review/page.tsx:90-98,120,217-225` | Surface the error with a retry, and disable the decision button when there is nothing to send. |
| 10 | Approving a plan never shows the plan. The gate prints only the goal; steps/tests/risks/forbidden paths/cost are not rendered, and Reject captures no reason so the agent learns nothing. | `planGate.tsx:345`, `:197-209` | Render the full plan inline in the gate with a one-line consequence summary (files, tests, est. cost) + a reject-reason field. This is the single highest-leverage trust fix in Tier 1. |

## Tier 2 — the harness experience is missing its centre (needs real work)

**11. There is no agent transcript anywhere.** The Overview tab is a raw event
dump: `PR opened · seq 355 · 10/25/2026, 5:59:19 AM`, snake_case names, newest
first, no payload, no duration between steps, monospace, 10 rows deep (and the
"N latest of M" counter is itself capped at 100, so it lies for long runs —
`runDetailAggregate.tsx:104-109`, `runsApi.ts:193`). Meanwhile the root cause is
a contract gap: `contracts/ts/src/events.ts:4-19` has no tool-call /
tool-result / token-count event, so `deriveView.ts:29-52` ignores
`diff_progress` and nothing can render "the agent ran `pytest`, it took 4.2s,
3 passed".

Claude Code, Codex and Cursor all put the transcript in the middle column with
collapsible tool results, and keep a persistent footer of model · tokens ·
cost · elapsed. That is the shape to aim at. **This needs a contracts decision
from you** (the frozen-types law blocks a UI-only fix), so it is item one on the
review list rather than a PR I should just send.

**12. Default tab on a live run is the dead one.** Arriving at an executing run
lands on Overview (3s REST poll, no connection indicator, no elapsed timer, no
cancel); Pause/Stop and the stream live only under "Working"
(`page.tsx:54,159-173`). A running agent looks frozen. Fix: land on the live tab
while `executing`, and put a Live/Stale pill plus elapsed clock in the page
header so it is true on every tab.

**13. Streaming dies quietly.** A dropped socket or failed ticket mint retries
forever with only "Reconnecting…" and no REST fallback
(`useEventStream.ts:214-218,254-273`), while the collab stream already has the
good model (attempt count, "refreshing every 3s", manual retry —
`realtimeCore.ts:186-202`). Port it. Also unmount-on-tab-switch replays the
whole log from `lastSeq = 0` and no list virtualizes (`RunTabs.tsx:119`), so
long runs degrade the page.

**14. "Plan & work" is one infinite column of admin forms.** Screenshot
`run_planwork.png`: empty Task/Success-criteria/Constraints textareas, a
`Plan content (JSON)` box showing `{}`, a raw JSON-patch textarea pre-filled
with `{"op":"replace","path":"/goal","value":""}`, subtasks, artifact upload,
an eight-field search form, "Request deletion of your data", and "Refresh work
history" — all at equal visual weight, all above the fold. Progressive
disclosure: plan + steps first, everything else behind a "Developer tools"
details group.

**15. Mid-run steering is cosmetic.** Direction applies "the next time it starts
or resumes, not mid-step" (`sharedRunPanel.tsx:245`) and lives on the Team tab;
Nudges are browser-local (`CommentsNudges.tsx`); Pause/Cancel "do not stop a
running process yet" (`agentTree.tsx:78`) while still looking enabled. Either
wire interrupt or disable with an honest tooltip — right now the UI promises
control it doesn't deliver.

**16. Nothing is copyable.** Zero `navigator.clipboard` uses repo-wide: no copy
on run id, sha, error text, diff, or PR link. Cheap win, and every reference
harness has it.

**17. The review loop has no keyboard model and no line comments.** Hunk
selection is checkbox-tab-order only, no `j/k` hop between hunks, no
accept/reject shortcuts, and comments attach to threads, not lines
(`discussionView.tsx`). This is exactly where Cursor/Codex feel fastest.

**18. First run is ungated.** A brand-new account lands on a Command Center
whose composer is always enabled; the real prerequisites (provider key →
matching model → repo) live across three screens and the onboarding checklist
is a separate nav item nobody is pointed to. So the first thing a new user
does is propose, approve, start — and fail opaquely at execution
(`app/page.tsx:14-21`, `planGate.tsx:279-342`). Fix: a readiness strip above the
composer ("What's missing: provider key · repo connection") with deep links,
disabling Propose until the blockers are clear. This is the item I'd rank
first for pilot conversion.

## Tier 3 — consistency and polish

19. **401 handling is per-page accidental.** Auto sign-out lives only in
`apiFetch` (`src/lib/api.ts:143-151`); keys, team, billing, governance,
integrations and models each roll their own `request()` that just throws, so an
expired session shows stale data plus a generic error. Same split for the
friendly-error map: `RULES_TOO_LARGE`, `VERSION_CONFLICT`, `SSO_NOT_CONFIGURED`,
`LAST_ADMIN`, `SOLE_ADMIN` aren't in it, so users get dev jargon. One shared
client fixes both.
20. **Destructive actions have four different patterns**: key revoke (inline +
5s undo — good), member removal (inline, no undo), governance role change
(fires a PATCH straight from a `<select>`, no confirm, no undo), webhook delete
(native `window.confirm`). Standardise on confirm + undo window.
21. **Breadcrumbs**: billing and project pages render none; governance,
integrations and notifications hand-roll two levels; keys/models/team use the
three-level `settingsCrumbs`. One helper everywhere.
22. **Run identity in the UI**: the page title is the raw run id
("Run demo-run-1ff20d37c7f8") and cost prints `$0.000057`. Show the goal as the
heading and money in a readable unit with the token count beside it.
23. **No unsaved-changes guard** anywhere, and one global `busy` flag disables
~25 buttons at once (`sessionSixWorkbench.tsx:42,85-90`) so a slow call makes
the whole page look dead. Scope busy state per action.
24. **Service bases default to `localhost`** (`api.ts:9-22,636`), including
`ws://localhost:8001`. They are `NEXT_PUBLIC_*`-overridable, so this is a
deployment-config item rather than a bug — but a build that forgets one env var
fails in a confusing way, and there is no startup assertion that they are set.
25. `window.prompt` for the abandon-run reason; plan/patch textareas `JSON.parse`
raw input and surface `SyntaxError` to the user.

## What I deliberately did not find wrong

The honest empty states, the "Demo replay" labelling, the skip link, focus
rings, 44px targets, reduced-motion support, the SSO callback page's candour,
disabled-but-visible affordances for roles that can't act, and the key-revoke
undo window are all better than the reference products. The problem is
concentration, not care.

## Suggested order for the review

1. Contracts decision first (item 11) — it gates the transcript, cost/token
footer and diff progress, i.e. most of Tier 2.
2. Batch A, one PR, ~1 day: items 1, 2, 3, 4, 6, 7, 8, 9 — all small, all
testable, no backend change.
3. Batch B, ~2–3 days: items 10 and 18 — plan-in-the-gate and the readiness
gate. These are the two that change how a pilot team *feels* about the product.
4. Batch C, ~1 week: items 12, 13, 14, 16 — live-by-default run page, real
reconnect UX, progressive disclosure, copy affordances.
5. Batch D: 15, 17, then Tier 3 as a sweep with a contrast/regression guard.

Screenshots and the measured contrast/overflow pass are under
`/tmp/ui-audit/` for this session; the probe scripts were temporary and are not
in the repo.

---

## Batch A outcome (implemented 2026-10-25)

All eight items landed, verified against the rebuilt local stack rather than
just asserted in code.

| Item | What changed |
|------|--------------|
| 1 | `DiffPanel` now renders `diffEmptyState(changedPaths)`, built from the `diff_progress` events the sandbox already emits — "1 file changed so far: src/textstats/stats.py". The Section 5 / frozen-contract paragraph is gone. `PlanPanel` dropped the `Actual cost: — (no live source yet)` line and its dev-note tooltip; `CommentsNudges` is now **Run notes**, honestly scoped to the browser tab with a pointer to the real "Give direction" control. |
| 2 | `TerminalPanel` shows the false "no live terminal feed" line only when there is genuinely nothing; with transcript lines it prints a line count (and a "latest 200 kept" marker at the cap). |
| 3 | Every pill, phase badge, run-state badge, settings link and breadcrumb separator moved onto theme tokens. `pillStyle()` now mixes its fill with `color-mix()` so a `var()` colour is legal (it previously only worked with hex, which is *why* every pill hardcoded a dark value). Added `--violet`/`--teal` for `reviewing`/`pr_opened`, and `src/lib/themeContrast.test.ts` now asserts AA for 12 token pairs in **both** themes plus a no-literal guard on the run-surface components. |
| 4 | The demo-replay banner was a `.pill` (nowrap) carrying a whole sentence, which forced the run page to 489px at a 390px viewport. It wraps now; measured overflow is 0px at 320/390/768. |
| 6 | `Controls` no longer POSTs a human edit when the checkbox is ticked; the checkbox reveals the editor and "Save edit" performs the pause + edit with a real path. Relabelled "Edit a file myself" with the pause consequence spelled out. |
| 7 | The sidebar TEAM line is a read-only value, not a `<select>` with one option and no handler (a principal carries exactly one `teamId`, so there was nothing to switch). |
| 8 | A rejected model save rolls the picker back to the previous value instead of showing a model the server refused. |
| 9 | The review page separates "PR does not exist yet" (404 → empty state) from "review data failed to load" (message + **Try again**), and "Request changes" now requires a written note and sends it as the review comment — previously it POSTed `[]` and reported a recorded decision. |

**Verification:** `section7` tsc clean, 958 vitest tests pass (14 new),
`npm run build` ok, `make static-gates` ok, and a headless contrast pass over
12 pages × 2 themes now reports **zero AA failures** (it reported 15 before).
Frontend, models and orchestrator images were rebuilt and migration 0062
applied on the local dev stack.

**One correction to the audit:** item 10 overstated the plan-visibility gap —
the Command Center's "CURRENT PLAN" card does render steps, estimate and
forbidden paths. What is still true is that the *gate itself* (where Approve
lives) shows only the goal, so Batch B should surface the plan inside the gate
rather than duplicate it.

---

## W24 recheck (2026-10-31)

All 25 items re-read against the code at `67378d8` plus the working tree (`section7/` has no
uncommitted change except the new review-summary card). **Static reading only**: no browser, no
live page, no new measurement; a "closed" here means the code no longer does what the audit
described, and where a test pins it the test is named. `npm test` in `section7/` passes (98 files,
1062 tests) and `tsc --noEmit` is clean, so every pin named below holds today.

Method note: items 1, 2, 4, 5, 6, 7, 8 and 10 were first ruled by a read-only agent; I re-opened
the cited lines myself for 4, 5 and 10 only. Items 3, 9 and 11–25 are my own reading. Paths below
are under `section7/`.

**Result: 7 closed, 8 partly, 10 open.**

| # | Status | Evidence (where it is now) | What is left |
|---|--------|----------------------------|--------------|
| 1 | closed | `src/components/DiffPanel.tsx:41` empty state from `diff_progress`; `PlanPanel.tsx:32` estimate only; `CommentsNudges.tsx:43` "Private to this browser tab"; pinned by `tests/unit/auditBatchA.test.ts:19,42,49` | The "Give direction" link goes to `#direction` (`CommentsNudges.tsx:44`), which no element defines. Other pages still print internal section names: `app/onboarding/page.tsx:275` ("Section 6, not yet deployed"), `app/review/page.tsx:283` ("via Section 1"). |
| 2 | closed | `src/components/TerminalPanel.tsx:62-66` empty line only when there are no transcript lines; pinned by `tests/unit/terminalPanel.test.tsx:22` | `WorkingArea.tsx:90` still passes `results={[]}`, so the pass/fail view (`TerminalPanel.tsx:72`) never shows in the product. |
| 3 | closed | `app/settings/page.tsx:60-68` uses `var(--accent)` / `var(--red)` tokens; `src/lib/themeContrast.test.ts:53-58` asserts AA (4.5) for token pairs in both themes | none |
| 4 | partly | Banner overflow fixed: `app/projects/[projectId]/runs/[runId]/page.tsx:161` (`whiteSpace: normal`) | Tab bar still wraps instead of scrolling with snap (`src/components/RunTabs.tsx:63-65`); breadcrumbs do not collapse (`src/components/Breadcrumbs.tsx:28`); no test pins the 390 px layout. |
| 5 | open | `app/projects/[projectId]/runs/[runId]/page.tsx:215` mounts `ChangeReview` with the project only; `src/components/ChangeReview.tsx:261` lists every project snapshot | Not scoped to the run (the sandbox's checkpoint API has no run id), Revert still unconfirmed, the empty state still says "for this project". |
| 6 | closed | `src/components/Controls.tsx:68` toggle only sets local state; the single `submitHumanEdit` call is the Save button (`:209`), disabled until a path is typed (`:206`) | none |
| 7 | closed | `src/components/AppShell.tsx:169-170` read-only TEAM value; pinned by `tests/unit/auditBatchA.test.ts:75` | The value is the literal "My team", not the principal's team. |
| 8 | closed | `src/components/ModelPrefs.tsx:106` snapshot, `:126-127` rollback then error | none (the guard is a source-level test, not a rendered one) |
| 9 | closed | `app/review/page.tsx:58-59` load error state, `:128-129` 404 vs failure, `:155-157` note required for "Request changes", `:295-299` "Try again" | none |
| 10 | open | `app/dashboard/planGate.tsx:410` the gate prints only `Goal:`; `:426` Reject takes no reason; `app/dashboard/planGateApi.ts:105` posts `{}` | Plan steps, tests, risks, forbidden paths and estimate are not in the gate; no reject reason. |
| 11 | partly | The contract now has the events: `contracts/ts/src/events.ts:19-20` (`tool_call`, `tool_result`); `src/lib/deriveView.ts:119-132` pairs them; `src/components/WorkingArea.tsx:89` renders "Tool activity" on the Working tab | Overview is still the raw step list, capped ("N latest of M", `runDetailAggregate.tsx:121-130`); no persistent model · tokens · cost · elapsed footer (only a cost line, `:66-69`). |
| 12 | partly | Overview shows a live status and connection state (`runDetailAggregate.tsx:45`, `page.tsx:182`) and tells the user when a refresh failed (`page.tsx:60,107-112`) | Default tab is still Overview (`page.tsx:55`); no elapsed clock found; Pause/Stop still live under Working. |
| 13 | partly | Reconnects back off and surface the attempt count (`src/lib/useEventStream.ts:261-267`) | No REST fallback or manual retry in the stream hook, no list virtualization found. I did not re-check the replay-from-seq-0 claim. |
| 14 | open | `app/projects/[projectId]/runs/[runId]/sessionSixWorkbench.tsx:48` the raw JSON-patch box is still pre-filled; only three `<details>` groups (`:164,167,194`) | Still one long column of admin forms at equal weight. I did not establish whether those three groups predate the audit. |
| 15 | partly | Stop now reports whether the cancel was confirmed (`src/components/Controls.tsx:73`, `app/runs/runsApi.ts:190`) | Direction still applies "the next time it starts or resumes" (`sharedRunPanel.tsx:246`); the agent tree still says pause/cancel do not stop a running process (`agentTree.tsx:78`); nudges are browser-local. |
| 16 | open | `grep clipboard` over `src/` and `app/` finds nothing | No copy affordance for run id, sha, error text, diff or PR link. |
| 17 | open | No key handler in `app/review/page.tsx` or `src/components/ChangeReview.tsx`; `discussionView.tsx:63,88,121,136` handle only cancel | The web review loop has no keyboard model or line comments. (The VS Code extension got one in W24; that is a different surface.) |
| 18 | partly | A readiness view exists on onboarding (`app/onboarding/readinessView.tsx`, `app/onboarding/page.tsx`) | The Command Center (`app/page.tsx:14-21`) has no readiness strip and the composer is not gated. |
| 19 | open | Only `src/lib/api.ts:143` handles 401; seven feature clients keep their own `request()` (`src/lib/contextApi.ts:58`, `demoApi.ts:34`, `modelPrefs.ts:85`, `app/settings/{project/projectRulesApi.ts:20,team/teamApi.ts:58,integrations/integrationsApi.ts:34,governance/governanceApi.ts:43}`) | No shared client; the named error codes appear only in comments (server text is shown verbatim). |
| 20 | partly | Key revoke has an inline confirm and an undo window (`app/settings/keys/revokeCell.tsx:3-5`); member removal is an inline confirm (`app/settings/team/tables.tsx:161-162`) and role changes can be undone (`:131-141`) | Webhook delete still uses `window.confirm` (`app/settings/integrations/page.tsx:104`). I found no role-change `<select>` on the governance page. |
| 21 | partly | `settingsCrumbs` is used by models, team, keys and account | Integrations, governance and notifications use `Breadcrumbs` without it; billing and project settings have no breadcrumb at all. |
| 22 | open | Run page crumb is `Run ${runId}` (`page.tsx:147`); cost prints with `formatCostUsdExact` (`runDetailAggregate.tsx:66-69`) | Heading is not the goal; cost still prints in full precision. |
| 23 | open | One `busy` flag gates the workbench (`sessionSixWorkbench.tsx:42`, 21 uses); no `beforeunload` / unsaved-changes guard anywhere in `src/` or `app/` | Both parts of the item stand. |
| 24 | open | `src/lib/api.ts:9-24` every service base still defaults to `localhost` | No startup assertion found; this remains a deployment-config item, not a bug. |
| 25 | open | `window.prompt` for the abandon reason (`sessionSixWorkbench.tsx:170`); `JSON.parse(planText)` / `JSON.parse(editPatch)` (`:140,145`) with the `SyntaxError` text shown to the user | Both parts of the item stand. |

**Still open after Batch A, in the audit's own order:** Batch B (10 open, 18 partly), Batch C
(12 partly, 13 partly, 14 open, 16 open), Batch D (15 partly, 17 open, and Tier 3: 19, 22, 23,
24, 25 open; 20, 21 partly), plus 5 (open; in no batch) and the rest of 4 (tab bar and breadcrumbs, which Batch A did not do). Item 11's
contract decision has landed; the Overview transcript it asked for has not.
