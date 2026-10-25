# UI/UX audit — 2026-10-25

> **Status:** Batch A (items 1, 2, 3, 4, 6, 7, 8, 9) is implemented and verified
> live — see "Batch A outcome" at the bottom. Batches B–D and the contracts
> decision (item 11) are still awaiting review.

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
