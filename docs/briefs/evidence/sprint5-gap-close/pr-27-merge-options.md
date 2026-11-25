# PR 27 merge options (cloud prep, 2026-11-25)

**Recommendation: HOLD.** Verdict stays **NOT READY**. Nothing here merges, rebases, or edits PR 27 or PR 28. This file refreshes `/plans/pr27-merge-strategy-2026-11-25.md` (memo on branch `claude/pr27-merge-memo`) after the stack PRs landed.

## Measured facts (fetched 2026-11-25 ~17:50 UTC)

| Item | Value |
|---|---|
| `origin/master` | `84ee71d` (PR 32 merge). PRs 34, 31, 36, 33, 32 are all on master. |
| `origin/w28-s2-sprints1-4` (PR 27) | `5793ce9` (Task 36: `test.fail` removed) |
| Merge base | `96f86fe10b08a4171728b103dfaff8b34e2556e1` |
| Distance | PR 27 is 107 commits ahead of master; master is 19 commits ahead of PR 27 (26 files, +1456/-24) |
| PR 28 | draft, base is PR 27's branch, not master. Do not retarget or merge before PR 27 is settled. |

## Trial merge of master into the PR 27 head (scratch checkout, aborted)

Exactly **2 conflicted files, 3 hunks**:

| File | Kind | Resolution |
|---|---|---|
| `docs/briefs/evidence/sprint5-gap-close/task-44-ci-changes-job.md` | add/add, 2 hunks | Keep the PR 27 side (it says PASS with run 45613454005); master's copy still says "green run NOT yet confirmed". Drop master's wording. |
| `section7/src/components/workspace/ReviewPane.tsx` | content, 1 hunk | **Needs a human read. See below.** |

Auto-merged without conflict, but re-check after the merge: `section5/src/harness_sandbox/shell_gate.py` (PR 33 glob fix vs PR 27), `section7/app/globals.css`, `section7/app/projects/[projectId]/runs/[runId]/page.tsx`, `section7/src/components/workspace/DiffPane.tsx`. `.github/workflows/ci.yml` merged clean this time (the `resolve` fix landed through PR 30 on master and Task 44 on the branch); still diff it for a doubled fix.

### ReviewPane.tsx: section7 R4 versus Sprint 5 (the one real conflict)

- **Master (R4, PR 29):** wraps the diff in `<IterationDiffScope key={runKey ?? runId} ... onDiffScopeChange>` so the Diff pane opens on "since last iteration" and toggles to the full PR. Also adds `projectId`, `runKey`, `onDiffScopeChange` props and imports.
- **PR 27 (Task 18, X-07 KPI):** wraps `<DiffPane>` in `<div ref={diffRef}>` with an `IntersectionObserver` that sets `diffSeen`, and `approvePr(prId, reviewer, token, diffSeen)` sends it.
- The conflict is only the JSX at the bottom of the component. The props, imports and `diffSeen` state auto-merge. Suggested resolution (nest, keep both):

```tsx
<div ref={diffRef}>
  <IterationDiffScope key={runKey ?? runId ?? ""} projectId={projectId} runId={runId} token={token} compact
    onDiffScopeChange={onDiffScopeChange}>
    <DiffPane view={view} compact onComment={ready && canDecide ? comment : undefined} />
  </IterationDiffScope>
</div>
```

- **Decision for the owner, not for the resolver:** `diffSeen` becomes true when the diff element is on screen, including when the pane is on the "since last" scope and only the newest checkpoint's hunks are shown. Whether that counts as "diff seen" for the blind-approval KPI is a product call. Until decided, the blind-approval rate stays **UNPROVEN**.
- After resolving, re-run: section7 vitest + `tsc`, `section7/e2e/r4-since-last-iteration.e2e.ts`, `scripts/r4-since-last-check.cjs`, and the R4 RC rows 6-12 (still pending on the Mac). The merge can silently break R4 on the RC stack even when it compiles.

## Paths

### HOLD (recommended, default)
Wait until all of these are green, then re-run the trial merge:
1. Task 45b: RC redeploy at schema head `0082`, manifest committed, CI job `readiness drift (manifest vs HEAD)` green. Status: **BLOCKED waiting on the Mac** (`task-45b-blocked-waiting-on-mac.md`).
2. Mac `make gate`, section1 Postgres suite, Task 36 Playwright row, Task 50 (`TEST_ALL_DOCKER=1 make test-all`). All **TBD**.
3. Session 4 RC rows 6-12 for R4 (manual, Mac). Pending.

Cost of holding: the branch drifts from master. Mitigation: re-run the trial merge (below) after each new master merge; it is one command.

### MERGE SOON (only on the owner's written "merge PR 27", with a named conflict owner: `owner` or `claude`)
1. Prefer **merging master into the branch** over rebasing: 107 commits cite SHAs in evidence docs, and a rebase rewrites every one of them.
2. Resolve the 2 conflicts as above. Name who resolves ReviewPane.tsx.
3. Push, wait for CI. **Stop if a required check is red** (today the known red job is `readiness drift`, which stays red until 45b).
4. Owner merges. Cloud does not merge.
5. Afterwards retarget PR 28 to master (or merge master into it) once.

## Rebase / merge checklist (files only; do not run without the owner)

```sh
git fetch origin master w28-s2-sprints1-4
git merge-base origin/master origin/w28-s2-sprints1-4          # expect 96f86fe1... until master moves again
git checkout -b pr27-sync origin/w28-s2-sprints1-4             # scratch branch, never PR 27's own
git merge --no-commit --no-ff origin/master                    # expect exactly 2 conflicts today
git diff --name-only --diff-filter=U                           # task-44 doc + ReviewPane.tsx
# resolve (see above), then:
git diff origin/w28-s2-sprints1-4 -- .github/workflows/ci.yml  # check for a doubled resolve fix
git merge --abort                                              # for a dry run; otherwise commit
```
After a real sync, on the Mac: `make gate`, `cd section1 && HARNESS_TEST_PG=1 ./.venv/bin/python -m pytest tests/ -q`, `cd section7 && npx playwright test e2e/r4-since-last-iteration.e2e.ts`. Cloud never claims full-gate PASS.

## Still open / unproven (unchanged by this file)
- Task 36 Playwright PASS: owner Mac saw 1 passed after 0082; cloud evidence refresh pending a Mac re-run.
- `make gate`, section1 PG, `make test-all` at the final SHA: not run.
- 45b manifest: BLOCKED.
- F-05 and blind-approval rate: OPEN / UNPROVEN.
- P-D7 DEFERRED, payment and global P-D6 not enabled.
