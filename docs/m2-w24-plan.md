# Marathon 2.0 W24 — everyday loop: review and editor (M2-W24)

Started 2026-10-31 as the second week of [Roadmap 3.0](roadmap-3.0.md) **Stage 3**
(W23–W24, W7), after W23's follow-ups landed in `236aa7d`. Scope and exit come from
the W24 row of [the continuation plan](m2-continuation-plan.md). This is a plan and
record, **not** a readiness claim; `STATUS.md` stays NOT READY.

## Scope (from the continuation plan)

1. A review summary listing changed files, tests run, failures, cost, elapsed time
   and remaining uncertainty.
2. VS Code `.vsix` packaging, a real-window smoke test and keyboard-first diff review.
3. Recheck the open items in [the UI/UX audit](ui-ux-audit-2026-10-25.md) against the
   current code.

**Exit evidence:** an installed `.vsix` used in a real window for one task, end to
end; UI audit items marked closed or still open. Stage 3's exit adds: the installed
extension completes one task through policy, approval and reviewed diff.

## Gate position carried in

The W7 rule applies: an unchecked gate stays open when a dependent week begins.

* **W23:** met on one host. Still open: the separate-machine fresh-setup run (a
  person has to run it; steps are in [the W23 plan](m2-w23-plan.md)) and F2's check
  that the provider accepts the model (needs a spend rule from the owner).
* **Stage 1** (trust and recovery): the exit gate is still open. Nothing here
  closes or relaxes it.
* **W7:** the packaged `.vsix` has a smoke only; the end-to-end task was never run.

## Inventory at `f9bbee5`

| Item | What exists | Gap |
|---|---|---|
| Review summary | Section 8 `ReviewSummary` is a model-written narrative on the PR review page. The run page shows state, cost and the last 100 events. The engine result carries `toolCalls` (`{callId, tool, target, outcome}`) and `usage`; server runs persist the calls as `agent_tool_calls` rows. | Nothing lists files, tests, failures, cost, elapsed time and what is still unknown in one place. `reviewer.evaluate_changes` is called without a test result (`service.py:2949`), so its `test_results_summary` takes the default `"passed"`: a "Review passed" verdict says nothing about tests. |
| IDE local run | `deploy/runners/local_engine.py` runs the confined engine and returns the engine result and the diff. | A local run is not metered in `cost_ledger`, and its scratch directory (the transcript included) is deleted when it ends. The confirm prompt shows a file count and a diff size, nothing else. |
| VS Code package | `clients/vscode` 0.1.0; `npm run package` builds a 46 KB `.vsix`. W7 smoke: installed in a real window, activated, four commands registered (`docs/briefs/evidence/w7/vsix-smoke.md`). | No task was run in a window: the shell cannot type into an editor and nothing took a screenshot. Review is a modal per file, with no keybindings. |
| UI audit | 25 items. Batch A (1, 2, 3, 4, 6, 7, 8, 9) was implemented on 2026-10-25. Items 12, 15 and 18 describe surfaces W23 changed. | No reconciliation against the current code since 2026-10-25. |

## Slices, in order

1. **Review-summary builder.** One pure function over recorded facts, with tests for
   every uncertainty rule. "Tests run" means shell calls whose command looks like a
   test runner; the outcome is `ok` or `error` only, because the engine records no
   pass counts. When the engine surfaced no tool calls the summary says *unknown*, not
   *none*. Cost is never defaulted to zero.
2. **Local runner and editor.** The runner returns the summary with its result. The
   extension shows it at the confirm step, prices the tokens through the rate-card
   endpoint (`unpriced` stays unpriced), and reviews with keyboard commands: apply,
   skip, skip all remaining, with the editor's own next-difference keys between hunks.
3. **Package and real-window run.** Rebuild the `.vsix`, install it into a throwaway
   profile, and run one task end to end on the live stack through a real VS Code window.
   Keyboard events are sent to the window, so the run is automated, not a human
   session; a hand-run checklist is recorded for an owner to repeat.
4. **Server and web.** `GET /api/v1/orchestrator/runs/{runId}/review-summary` for
   queued runs, built from the same function, and a card on the run page.
5. **UI audit reconciliation.** Each of the 25 items marked closed or still open, with
   `file:line` evidence from the current code.

## Results

Recorded 2026-10-31. Nothing here is a readiness claim.

### 1. Review summary (met)

`section4/src/harness_orchestrator/review_summary.py` builds one summary from recorded facts
only: changed files with line counts, test commands with how each ended, failures, cost,
elapsed time and a list of what is still unknown. Unknown stays unknown: a cost that cannot
be priced is `None` (never `$0`), a run whose tool calls were not reported has *unknown*
tests (never *none*), a test with no exit status is *unknown* (never *passed*), a denied
command is *refused*, the latest run of each test command decides, and a write after the last
test is flagged. The harness re-runs nothing; `basis` says so. 57 unit tests cover the rules
(`test_review_summary.py` 49, `test_artifacts_review_summary.py` 8).

Exit status comes from the engine: `_exit_code()` in `engine.py` reads the plugin's
`[exit code: N|timeout]` marker. The server has no column for it, so queued runs store it as
a `(exit N)` suffix in `agent_tool_calls.summary` (`call_summary` / `exit_code_from_summary`,
no migration). Rows written before this change have no suffix and read *unknown*.

### 2. Local runner and editor (met)

* `deploy/runners/local_engine.py` returns `reviewSummary` and the elapsed time.
* The extension prices the tokens through the rate-card endpoint and shows the summary in the
  confirm dialog (`src/core/summary.ts`); `clients/vscode` is 0.2.0 with 43 tests.
* Review is keyboard-first: `Alt+Y` apply, `Alt+N` skip, `Alt+Shift+N` skip all remaining
  (each skipped file is still recorded as rejected). The keys are live only while a decision
  waits (context key `harness.reviewing`, `src/core/gate.ts`); the same three choices are
  buttons on the notification and palette entries.

### 3. Installed `.vsix` in a real window (met, automated)

`harness-vscode.vsix` 0.2.0 (54,745 bytes, sha256 `4a886a30…a258e42f`, git-ignored) was
installed with `code --install-extension` into a throwaway profile and used in a real VS Code
1.140.0 window (arm64, macOS) against the live stack, through `scripts/w24_window_run.mjs`.
One task, end to end: sign in with an email code, `Harness: Run on selection`, plan dialog
approved, run in the confined container, review summary at the confirm step, Confirm
(`done-signal`), reviewed diff opened, `Alt+Y`, file written. Both runs passed:

| Run | Run id | Decision | Cost (rate card) | Time | Evidence |
|---|---|---|---|---|---|
| 1 | `run-eab4dc03d07e` | `Alt+Y` | $0.004710, 37,832 in / 1,854 out | 31.2 s | `docs/briefs/evidence/w24/w24-window-fef8070b932b.json` |
| 2 (final code) | `run-945fda24e938` | `Alt+Y` | $0.004619, 36,310 in / 1,976 out | 28.4 s | `docs/briefs/evidence/w24/w24-window-456ee015559c.json` |

Each has five screenshots beside it. In both, `calc.py` went from `a - b` to `a + b`, the
workspace's own `python -m unittest` failed before and passed after (run on the host by the
script), and the server shows the change `approved` with a `diffSha` equal to the sha256 of its
patch. The throwaway profile and workspace were removed.

**What this is not.** The keys and clicks were sent to the window over the DevTools protocol,
so this is an automated run, not a person using the editor. The hand-run checklist is in
`clients/vscode/README.md`; an owner should repeat it once by hand.

### 4. Server endpoint and web card (met in tests, not deployed)

`GET /api/v1/orchestrator/runs/{runId}/review-summary` builds the same summary from
`agent_tool_calls`, `cost_ledger` and `agent_events` (another team's rows and agent-reported
calls are never mixed in; no diff yet is a 404). OpenAPI, both SDKs and the tracked TS `dist`
are regenerated, spec drift is clean, and the run page has a `ReviewSummaryCard`
(3 component tests). **The live stack still runs `vw23f24`: this route is not deployed there**,
and no deployed run has been checked against it. A redeploy needs the other session's drills to
be idle.

### 5. UI audit (met)

`docs/ui-ux-audit-2026-10-25.md` has a "W24 recheck" table with `file:line` evidence for all 25
items: **7 closed, 8 partly closed, 10 still open.** The open ones are listed there and are not
claimed.

### Findings kept (not fixed here)

1. **The IDE agent cannot verify its own work on macOS Docker Desktop.** On a host bind mount
   Landlock denies the shell gate every workspace read (`docs/m2-w21-plan.md`, "Drill
   artifact"), so `python -m unittest` and `ls` inside the confined run fail with permission
   denied. The summary reports exactly that (tests *failed*), while the host check passes. In
   both runs the model still produced the right edit. A reviewer will see "tests failed" on a
   correct change until this is fixed.
2. **A server run stays `executing` after an IDE run.** There is no `engine_completed`, because
   the local run is not the server's run. The server-side summary of such a run has no end
   time, so it reports the elapsed time as unknown.
3. **Local runs are not metered** in `cost_ledger` (`costLedgerRowsForThisRun: 0`); the cost
   shown is priced by the extension from the run's own token counts.
4. **The approved plan text is a canned template** ("Inspect codebase / Apply edits / Run unit
   tests", `Tests: pytest tests/`, `Estimated cost: $0.05`), identical for different tasks, and
   it names `pytest` for a repo that uses `unittest`.
5. **`reviewer.evaluate_changes` still defaults `test_results_summary` to `"passed"`**
   (`service.py:2949`): a "Review passed" verdict says nothing about tests. The summary does
   not fix this.
6. The Python-extension recommendation toast appears over the review prompt in a fresh profile;
   it is VS Code's, and the driver ignores it.

### Runs and spend

Two window runs (about $0.0093 together, rate card). **One run was not named in this plan:** a
single diagnostic model run through `diag_local_run.py` (about $0.004) to find out why the
in-container tests failed; it produced finding 1. No drill, redeploy or `down` was run, and
the other session's stack was left as found.

### Verification

Recorded when the checks were last run (see the commit message): section4 unit suite, repo-root
`ruff`, `scripts/spec_drift.py`, `scripts/test_spec_drift.py`, `clients/vscode` typecheck and
tests, section7 tests, `check_ts_dist.sh`.

### Not claimed

* A person using the installed extension: only an automated run.
* The server endpoint on the live stack.
* That the IDE agent's tests mean anything on macOS Docker Desktop (finding 1).
* Pass or fail counts: only exit status is recorded.
* Stage 1's exit gate, W23's separate-machine run, or F2's provider check: all still open.

## Rules carried over

* Do not edit other lanes' uncommitted files (`packages/language`, `docs/m2-continuation-plan.md`).
* New HTTP routes are recorded with `scripts/spec_drift.py --write` and both SDKs regenerated.
* Commit by explicit path; do not push unless asked.
* The live stack is shared with another session's drill batches: no redeploy, and no
  run that is not named in this plan.
* Keep failures. A cost that cannot be priced is reported as unpriced, never as zero.
