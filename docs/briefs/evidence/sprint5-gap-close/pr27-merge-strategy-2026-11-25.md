# PR 27 merge strategy: decision memo for the owner

Date: 2026-11-25. Status: recommendation only. Nothing in PR 27 or PR 28 was changed, and nothing was merged. Verdict stays **NOT READY**.

## Measured facts

| Item | Value |
|---|---|
| PR 27 | `w28-s2-sprints1-4` at `efea1528`, base `master`, GitHub says `mergeable_state: dirty` |
| PR 27 size | 106 commits, 551 files, +21,646 / -1,437 |
| Merge base with master | `96f86fe1` (master is now `5654b53b`, 5 commits ahead: PR 29 R4 + PR 30 CI quoting) |
| Where the 551 files sit | 346 under `docs/` (265 evidence), 58 section7, 41 section1, 39 section4, 13 scripts, 12 clients, 10 experiments, 9 section8, 9 section5, 4 deploy; about 145 are PNG screenshots |
| Migrations carried | section1 `0077` to `0082` (0082 = team member soft remove) |
| PR 28 | draft, base `w28-s2-sprints1-4` (not master), 4 files, +575/-115, head `159786b5`, based on an older tip `bb124ba1` |

Master moved 19 files since the merge base; PR 27 touches 6 of them. A trial merge of master into the PR 27 head (done in a scratch clone, then aborted) gave **2 real conflicts, 3 hunks**:

1. `docs/briefs/evidence/sprint5-gap-close/task-44-ci-changes-job.md`: add/add, 2 hunks. Master and PR 27 each added this file (the Task 44 CI fix landed on both sides). Trivial: keep one version.
2. `section7/src/components/workspace/ReviewPane.tsx`: 1 hunk. Master's R4 "since last iteration" change vs PR 27's edits. Needs a human read; this is the only code conflict.

Auto-merged cleanly but worth a second look: `.github/workflows/ci.yml` (both sides fixed the same `resolve` quoting bug, so check for a duplicated or doubled fix), `section7/app/globals.css`, `section7/app/projects/[projectId]/runs/[runId]/page.tsx`, `section7/src/components/workspace/DiffPane.tsx`.

So "dirty" is mostly cosmetic. The cost of merging PR 27 is review size and unproven live steps, not conflict resolution.

## Options

### (a) Merge the mega-PR after a rebase and conflict plan
- Pros: one move; keeps 106 commits of history; Sprint 5 and PR 28's base come with it; conflict work is small (2 files).
- Cons: 551 files cannot be reviewed as one unit; carries unproven items (Task 36 member removal e2e still FAIL until re-run; `make gate` and section1 Postgres not run at `55ed0da`/tip; migration 0082 not applied on the RC stack so readiness-drift stays red); production-affecting migrations 0077 to 0082 land together; a forward-only migration set is hard to back out once deployed.
- Needs: a named conflict owner (resolve ReviewPane, dedupe task-44 doc, re-check ci.yml), a merge (not rebase) of master into the branch so the 106 SHAs cited in evidence docs stay valid, and a fresh full CI run after.

### (b) Split by section
- Pros: smaller reviews; section1 migrations could be reviewed alone.
- Cons: the changes are cross-coupled (migrations 0078 to 0082 with section4/section7 consumers, collab `viewed_detail` across section4/7/8); 346 of 551 files are evidence docs that reference commit SHAs which would change; high effort, and splitting 106 interleaved commits invites new conflicts and new bugs. Evidence would need regenerating.

### (c) Hold until the Mac phase is complete (recommended)
- Pros: zero risk now; the owner's Mac runs (`make gate`, section1 PG suite, Task 36 re-run, 0082 migrate plus stack smoke, Tasks 43/45b/50) turn "claimed" into "proven" before anything lands; PRs 31 to 34 land first and shrink the conflict surface to what is already known; PR 28 can then be retargeted once.
- Cons: PR 27 keeps drifting from master, so every new master commit risks more overlap; the long-lived branch holds real work that exists only on the branch.
- Mitigation: re-run the trial merge after each of PRs 31 to 34 lands (one command, below), and merge master into the branch only when the owner asks.

### (d) Abandon and cherry-pick tasks
- Pros: smallest incremental risk per change.
- Cons: highest effort and error risk; tasks are interleaved with shared evidence, STATUS and ledger edits, so cherry-picks conflict with each other; loses the evidence trail that cites the branch SHAs; migrations 0077 to 0082 must still be taken as a set. Only worth it if the owner decides some Sprint work should not ship.

## Recommendation

**HOLD (option c).** Do not merge PR 27 until: Mac `make gate` and section1 Postgres are green at the pinned commit, Task 36 member removal e2e is re-run and passes, migration 0082 is applied and readiness-drift clears, and PRs 31 to 34 have landed on master. Then re-run the trial merge and decide between (a) and (b).

If the owner prefers to merge sooner, option (a) is acceptable **only** with an explicit conflict owner named in writing and the owner's written "merge PR 27". Nobody else should merge it.

PR 28 stacks on PR 27 (base `w28-s2-sprints1-4`). Keep it draft and do not retarget to master before PR 27 lands. Its head `159786b5` was cut from `bb124ba1`, so after PR 27's tip moves it also needs master/PR 27 merged in.

## Reproduce the conflict check

```
git fetch origin master w28-s2-sprints1-4
git checkout --detach origin/w28-s2-sprints1-4
git merge --no-commit --no-ff origin/master   # expect 2 conflicts today
git diff --name-only --diff-filter=U
git merge --abort
```

## Still unproven / FAIL (not changed by this memo)
- Task 36 member removal e2e: FAIL until re-run on the Mac.
- `make gate` and section1 PG at the branch tip: not run in cloud.
- Migration 0082 on the RC stack: not applied; readiness-drift red.
- Blind-approval rate: UNPROVEN, no pilot data.
- Verdict: NOT READY.
