# R4 evidence: "Since last iteration" in the Diff pane

Date: 2026-11-25. Built on `master` at `96f86fe`; merged as #29 (`5654b53`). Scope: `section7/` only, no `contracts/` change.

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

Updated 2026-11-25 after #29 merged (`5654b53`). "Cloud" = Claude Code cloud session: it has the Docker
client but no Docker daemon (no `/var/run/docker.sock`), so no compose stack can run there. "CI" = GitHub
Actions `e2e-compose` job, which boots the full compose stack. "Stubbed browser" = real Chromium against
`next dev` with every backend answered by `scripts/r4-since-last-check.cjs`.

### Automated

| # | Check | Where | Result |
|---|-------|-------|--------|
| 1 | `npx tsc --noEmit` | cloud; CI `vitest + tsc (section7)` on #29 | PASS |
| 2 | `npx vitest run tests/unit/sinceLastIteration.test.ts tests/unit/diffScope.test.ts` (+ `sinceLastIterationDiff.test.tsx`) | cloud | PASS, 19 tests in 3 files |
| 3 | `npx vitest run` (full) | cloud; CI on #29 | PASS, 1220 tests in 115 files (baseline 1201 in 112) |
| 4 | Stubbed browser check, 10 assertions | cloud | PASS 10/10, no page errors |
| 5 | `npx playwright test e2e/r4-since-last-iteration.e2e.ts --reporter=line` | cloud | NOT RUN: no Docker daemon, global setup exits 246 (stack unreachable) |
| 5 | same command, against the compose stack | CI, [PR #31](https://github.com/mubasharali24428-crypto/harness/pull/31), run [45645352207](https://github.com/mubasharali24428-crypto/harness/actions/runs/45645352207/job/139314893279), head `30ac5a3` | PASS: 3 passed, 1 skipped. Passed: default since-last with decision visible, no snapshot history and axe clean; toggle to full PR and back; revert body `{ hunks: [{ path, index }] }`. Skipped: "switching runs", because the demo seed returns the same run for a team, so there is no second run to switch to. Needs #31's spec stub fix (CORS headers on the sandbox stubs) |

### Manual on RC (`:3011`), for the owner

| # | Check | Stubbed browser (reference) | RC result |
|---|-------|-----------------------------|-----------|
| 6 | Diff pane opens on "Since last iteration" by default | PASS | |
| 7 | Toggle to "Full pull request" shows the PR diff and the snapshot history; toggle back hides it | PASS | |
| 8 | "Your decision" stays visible while since-last is shown | PASS | |
| 9 | No snapshot-history (ChangeReview) flash while the pane loads | PASS | |
| 10 | Switching to another run opens on since-last again | PASS (full navigation only) | |
| 11 | Reverting a ticked hunk works and the newest snapshot becomes the revert | PASS (body checked) | |
| 12 | Pane readable and usable at 1440 and 375, light and dark | PASS (axe, light) | |

### Section 1

| # | Check | Where | Result |
|---|-------|-------|--------|
| 13 | `./.venv/bin/python -m pytest tests/ -q` | cloud, fresh venv, Python 3.13, SQLite 3.53 | 1261 passed, 63 skipped, 8 errors, all in `tests/test_runs_index_tuning.py` (SQLite "too many SQL variables" in the fixture). R4 does not touch `section1/` |
| 13 | same | owner's Mac | |
| 14 | `./.venv/bin/ruff check src tests scripts` | cloud | PASS |
| 14 | same | owner's Mac | |

PR 27 intentionally not merged — reason TBD.

Screenshots (stubbed browser, light): `r4-since-last-1440.png`, `r4-full-pr-1440.png`,
`r4-since-last-375.png`.

## Found on the way (not fixed here)

`ChangeReview.loadSnapshots` picks `preferred ?? sha ?? list.items[0]?.sha`. `sha` starts as `""`,
which `??` keeps, so on first load no snapshot is selected and the history says "No textual changes in
this snapshot" until a person picks one (visible in `r4-full-pr-1440.png`). A one-character fix (`||`),
left out to keep this PR to R4.
