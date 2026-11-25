# R4 evidence: "Since last iteration" in the Diff pane

Date: 2026-11-25. Base: `master` at `96f86fe`. Scope: `section7/` only, no `contracts/` change.

## What changed

- The Diff pane opens on **Since last iteration**: the hunks of the newest sandbox checkpoint
  (preferring one tagged with this run's id; otherwise the project's newest, and the pane says it is
  not tagged with this run). A toggle switches to **Full pull request** (the existing DiffPane).
- With no checkpoints (404 `CHECKPOINT_REPOSITORY_NOT_FOUND`) the pane opens on the full pull request,
  the since-last button is disabled, and one line says why.
- The snapshot history (`ChangeReview`) under the Diff pane shows only on the full-PR scope. While the
  scope is being decided it stays hidden, so it never flashes in and out.
- Per-hunk revert works in the since-last view through the existing endpoint, body
  `{ hunks: [{ path, index }] }` only.
- A new run (run key) starts again on the default scope.
- The DiffPane footer no longer says the since-last view is not available.

Files: `src/lib/sinceLastIteration.ts`, `src/lib/diffScope.ts`,
`src/components/workspace/IterationDiffScope.tsx`, `src/components/workspace/SinceLastIterationDiff.tsx`,
patches to `ReviewPane.tsx`, `DiffPane.tsx`, `app/projects/[projectId]/runs/[runId]/page.tsx`,
`app/globals.css`. Tests: `tests/unit/{sinceLastIteration,diffScope}.test.ts`,
`tests/unit/sinceLastIterationDiff.test.tsx`, `e2e/r4-since-last-iteration.e2e.ts`.
Browser check scaffolding: `scripts/r4-since-last-check.cjs`.

Source note: the Qwen v3 paste named in the session brief was not in the repo, the docs or the project
files, so this was implemented from the repo's own ReviewPane, DiffPane, ChangeReview, `api.ts` and
`review.e2e.ts`, to the R4 spec in the brief.

## Results

"Cloud" = Claude Code cloud session (no Docker, no compose stack). "Stubbed browser" = real Chromium
against `next dev`, every backend answered by `scripts/r4-since-last-check.cjs`.

| # | Check | Where | Result |
|---|-------|-------|--------|
| 1 | `npx tsc --noEmit` | cloud | PASS |
| 2 | `npx vitest run tests/unit/sinceLastIteration.test.ts tests/unit/diffScope.test.ts` (+ `sinceLastIterationDiff.test.tsx`) | cloud | PASS, 19 tests in 3 files |
| 3 | `npx vitest run` (full) | cloud | PASS, 1220 tests in 115 files (baseline before the change: 1201 in 112) |
| 4 | `npx playwright test e2e/r4-since-last-iteration.e2e.ts --reporter=line` | cloud | NOT RUN: global setup skipped, exit 246, no stack reachable. Pending a run against the stack |
| 5 | Stubbed browser check, 10 assertions | cloud | PASS 10/10, no page errors |
| 6 | Diff pane opens on since-last by default | stubbed browser | PASS. RC: pending |
| 7 | Toggle to full PR shows the PR diff and the snapshot history; toggle back hides it (keyboard) | stubbed browser | PASS. RC: pending |
| 8 | "Your decision" (review-decide) visible while since-last is primary | stubbed browser | PASS. RC: pending |
| 9 | No ChangeReview flash on load (MutationObserver never saw it) | stubbed browser | PASS. RC: pending |
| 10 | Run switch re-defaults to since-last | stubbed browser (full navigation) | PASS. Client-side sidebar switch is in the e2e only. RC: pending |
| 11 | Hunk revert body is exactly `{ hunks: [{ path, index }] }` | stubbed browser | PASS. RC: pending |
| 12 | axe (wcag2a/aa, 21aa, 22aa) clean in the pane at 1440 and 375 | stubbed browser | PASS. RC: pending |
| 13 | Section 1: `./.venv/bin/python -m pytest tests/ -q` | cloud, fresh venv, Python 3.13, SQLite 3.53 | 1261 passed, 63 skipped, 8 errors, all in `tests/test_runs_index_tuning.py` (fixture fails with SQLite "too many SQL variables"). This PR does not touch `section1/`; re-run on the owner's Mac |
| 14 | Section 1: `./.venv/bin/ruff check src tests scripts` | cloud | PASS |

Screenshots (stubbed browser, light): `r4-since-last-1440.png`, `r4-full-pr-1440.png`,
`r4-since-last-375.png`.

## Found on the way (not fixed here)

`ChangeReview.loadSnapshots` picks `preferred ?? sha ?? list.items[0]?.sha`. `sha` starts as `""`,
which `??` keeps, so on first load no snapshot is selected and the history says "No textual changes in
this snapshot" until a person picks one (visible in `r4-full-pr-1440.png`). A one-character fix (`||`),
left out to keep this PR to R4.
