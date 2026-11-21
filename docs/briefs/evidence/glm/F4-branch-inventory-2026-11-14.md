# F4 — Branch, worktree and unique-commit inventory — 2026-11-14 (GLM 5.3 Flash)

Plan: 0.2 (Phase 0 inventory). Dispositions only — no destructive cleanup, no branch or worktree
was created, deleted, pruned, merged, checked out, fetched, repacked or gc'd; the only writes are
this file and `docs/briefs/evidence/glm/F4-handback.md`.

Base document: `docs/plans/branch-inventory-2026-11-11.md` (delta over 2026-11-06; that file's
§C classes and home-path policy carry forward). Repo:
`/Users/user/untitled folder 2/harness`, primary checkout on `w28-s1-delegate` at `6207006`.

## Provenance

- Snapshot 2026-11-14, ~09:40 GMT+5. **No fetch was run.** All ahead/behind counts are relative to
  last-fetched remote-tracking refs, not to the remote today.
- "Total" = `git rev-list --count <base>..<branch>` (commits not reachable from the base tip).
  "Unique" = `git cherry <base> <branch> | grep -c '^+'` (excludes patch-equivalent commits).
  Note: `rev-list` counts merge commits; `git cherry` output has no `+` lines for merges, so
  unique ≤ total and the gap is merges plus patch-equivalent commits.
- Worktrees under `/Users/user/.cline/` were **not entered** (home-path policy carried forward from
  the base inventory); their dirty counts remain `not run (home path)`.
- Commands used (all read-only): `git rev-parse --short HEAD`, `git branch -a`, `git branch -vv`,
  `git worktree list`, `git for-each-ref refs/heads` / `refs/remotes`,
  `git rev-list --count <base>..<ref>`, `git cherry`, `git log --oneline --graph`,
  `git merge-base --is-ancestor`, `git branch -a --contains`, `git log -1 --format=...`,
  `git -C <worktree> status --short | wc -l` (non-home paths only), `git status --short` (primary).

## A. Counts at snapshot

| Item | 2026-11-11 inventory | 2026-11-14 this snapshot | Evidence |
|---|---|---|---|
| Local branches | 42 | **41** (`git branch -a` lists 41 local names) | `git branch -a` |
| Remote-tracking branches (excl `origin/HEAD`) | 31 | **33** (`claude/path-to-9-finish-ax6hie`, `cloud/path-to-9-finish`, `docs-w17-shipped`, `imgbot`, `pilot-drills-and-legal`, `pilot-remediation-batch6` have no local counterpart) | `for-each-ref refs/remotes` |
| Worktrees | 25 | **25** (8 detached-HEAD fleet workbenches) | `git worktree list` |
| Local branches with unique>0 vs local `master` | 12 | **12** (`codex/*` 6, `integration/local`, `scratch/integration-test`, `key-pool-s1`, `flaky-sqlite-busy-timeout`, `provider-registry`, `w28-s1-delegate`; plus `cline/84158`, `cline/b0895` if measured against local `master` only — both zero vs `origin/master`) | `git cherry` loop |
| Primary checkout dirty entries | 23 | **33** | `git status --short | wc -l` |
| Open PR seen in repo docs | PR #24 open | not re-verified (no `gh` call in this task) | — |

Delta note: the 2026-11-11 file said 42 local branches; counting this snapshot's `git branch -a`
output gives 41 names. No branch deletion was performed by this task; the discrepancy is in the
earlier documents' counts, not in this snapshot. Every name in this snapshot is inventoried below.

Snapshot drift: the primary dirty count was 33 when measured (~09:40) and 44 by ~09:52 — other
lanes modified 11 tracked files concurrently (`git status --short` shows ` M` on `.env.example`,
`docs/plans/ui-overhaul-plan-2026-11-13.md`, `section4/src/harness_orchestrator/{reviewer,service,signals}.py`,
`section4/tests/conformance/test_orchestrator_conformance.py`, `section4/tests/conftest.py`,
`section4/tests/stress/test_section4_stress.py`, `section4/tests/test_done_signal.py`,
`section4/tests/test_stateless_orchestrator_n1.py`, `section7/e2e/phone.e2e.ts`). Not mine, not
inspected further; the 33 figure is the snapshot-time count used in the table above.

`origin/master` = `1753171`. Local `master` = `b822923`, **behind 5** (`git rev-list --count
master..origin/master` = 5), **0 ahead**. The 5 commits local `master` lacks:
`d63426e` (docs W28 delegation-gate work order), `618d747` (W28 S1a delegate verb), `2d018b1`
(W28 S1a document HARNESS_SUBAGENTS), plus merges `5e84edc` (PR #23) and `1753171` (PR #22).
`git cherry master origin/master` shows exactly the 3 non-merge ones as `+`.

## B. Local branches — unique commits not on master

Base for "not on master": local `master` (`b822923`). "Unique" = `git cherry master <b>` `+` lines
(non-merge, not patch-equivalent). All 41 local branches appear; zero-unique ones are grouped.

### B1. Branches with unique commits

| Branch | Tip | Total not on master | Unique | Unique commit IDs (short) | Upstream / unpushed | Worktree |
|---|---|---|---|---|---|---|
| `w28-s1-delegate` ★current | `6207006` | 83 | **80** | `d63426ee6d 618d7478f0 2d018b11a0 9d7d67c2cc 6b69c735e9 15e6679faf 4b83a772cb 6f892d9c1a f071ff2891 6869790a46 4888deaa14 580696879b 4ec9da45cb b8efcb36c9 11da497f21 41fc7337e9 ba30c3e584 eb53563857 24fc0eb5e4 0c08b2f7ba 655b2de7eb b013b44f1f c40b7977fd 03c6b8d880 d6768e92c1 00e1313201 0e7276bd0d d93a203394 1e184196b5 269ea0b942 68c52207b4 ced6d4c9b2 2e05c6e6b5 bda7b7ccee 1320d54c27 9b5e353f45 4fd8b3ae35 f5b298a2c0 57b466e8cc bcf79d7ef0 4d1cfbee32 e59f7abc46 0c57564fec 7cdd365e9b 0f55edbea0 19f5caeff9 7c1acf4cdc 0e3442137a fef98f2365 480a4c4093 5b75b4ab13 9047179290 e57b87e2cc deea6c6caf fd41dbc520 4637a62d38 6f20058261 17d81d4208 d491121588 a664a65b8c ea337a134d cd3a90fcdc 9d4fab2be9 31ba854a48 58bf8b8273 576f9c10a0 6d51e1261b f7c740aa5e d304f56441 13cf97bee6 7c04f16558 31b77b6ca9 8836dfacb0 ee9f1997aa 04b819b51b e24b8309a5 97506067d2 ed62fd1c85 e15bcef9ec 6207006586` | `origin/w28-s1-delegate`, **34 unpushed** (0 behind) | primary |
| `key-pool-s1` | `4f37538` | 55 | **52** | `d63426ee6d 618d7478f0 2d018b11a0 9d7d67c2cc 6b69c735e9 15e6679faf 4b83a772cb 6f892d9c1a f071ff2891 6869790a46 4888deaa14 580696879b 4ec9da45cb b8efcb36c9 11da497f21 41fc7337e9 ba30c3e584 eb53563857 24fc0eb5e4 0c08b2f7ba 655b2de7eb b013b44f1f c40b7977fd 03c6b8d880 d6768e92c1 00e1313201 0e7276bd0d d93a203394 1e184196b5 269ea0b942 68c52207b4 ced6d4c9b2 2e05c6e6b5 bda7b7ccee 1320d54c27 9b5e353f45 4fd8b3ae35 f5b298a2c0 57b466e8cc bcf79d7ef0 4d1cfbee32 e59f7abc46 0c57564fec 7cdd365e9b 0f55edbea0 19f5caeff9 7c1acf4cdc 996975ad65 9803f55348 014452fe96 c84befbf5b 4f37538e81` | **no upstream — nothing pushed** | `harness-wb-kp` (dirty 0) |
| `codex/harness-a5-deploy-fixes` | `77c658c` | 31 | **26** | `39a35dadaa dc4d9634be 3fc99087a9 0d81f36daf c12b36397a 408cb4cbc7 cc886c870e 73f6cd5e86 e0446797cc 383494d97b b334cebd1d eecb308363 2e7ec2828a 39c7380aad 78797d4daa af55ecb5c7 86e481492b 5458e63f20 a191b09578 44f3c29ecb 7f852d506a 03551cbf39 36f1565e85 65453db5ee cd4049d765 77c658ce1d` | none (`branch -vv` shows no `[origin/...]`), effectively unpushed | `harness-a5-deploy-fixes` (dirty 0) |
| `integration/local` | `c2a6e34` | 30 | **25** | identical set to `scratch/integration-test` below (`diff` of the two `cherry` sets = SETS-IDENTICAL) | none | `harness-integration` (dirty 0) |
| `scratch/integration-test` | `ffb8bd5` | 30 | **25** | `39a35dadaa dc4d9634be 3fc99087a9 0d81f36daf c12b36397a 408cb4cbc7 cc886c870e 73f6cd5e86 e0446797cc 383494d97b b334cebd1d eecb308363 2e7ec2828a 39c7380aad 78797d4daa af55ecb5c7 86e481492b 5458e63f20 a191b09578 44f3c29ecb 7f852d506a 03551cbf39 36f1565e85 65453db5ee cd4049d765` | none | `harness-integration-test` (dirty 1) |
| `codex/harness-a4` | `5458e63` | 9 | **9** | `0d81f36daf c12b36397a 408cb4cbc7 cc886c870e 73f6cd5e86 2e7ec2828a 39c7380aad 86e481492b 5458e63f20` | `origin/codex/harness-a4`, 0 unpushed | `harness-a4` (dirty 6) |
| `codex/harness-a1` | `cd4049d` | 6 | **6** | `39a35dadaa 383494d97b 78797d4daa a191b09578 03551cbf39 cd4049d765` | `origin/codex/harness-a1`, **1 unpushed** (`cd4049d`) | `harness-a1` (dirty 3) |
| `codex/harness-a3` | `65453db` | 6 | **6** | `3fc99087a9 eecb308363 af55ecb5c7 44f3c29ecb 36f1565e85 65453db5ee` | `origin/codex/harness-a3`, **2 unpushed** (`36f1565`, `65453db`) | `harness-a3` (dirty 0) |
| `codex/harness-a5` | `57a1dea` | 3 | **3** | `e0446797cc 7f852d506a 57a1dea0e1` | **no upstream** (`for-each-ref upstream=` empty; `branch -vv` "ahead 1" is stale metadata; `rev-list --count origin/codex/harness-a5..codex/harness-a5` = 1) | `harness-a5` (dirty 0) |
| `codex/harness-a2` | `b334ceb` | 2 | **2** | `dc4d9634be b334cebd1d` | `origin/codex/harness-a2`, 0 unpushed | `harness-a2` (dirty 1) |
| `flaky-sqlite-busy-timeout` | `2348db1` | 1 | **1** | `2348db1ee6` | `origin/...`, 0 unpushed | not checked out |
| `provider-registry` | `49cfa55` | 1 | **1** | `49cfa5551e` | `origin/provider-registry`, **1 unpushed** (`49cfa55`) | not checked out |
| `cline/84158` | `2d018b1` | 2 (vs local `master` only) | 2 vs local master; **0 vs `origin/master`** | `618d7478f0 2d018b11a0` — both merged to `origin/master` via PR #23 | `branch -vv` shows no upstream; `rev-list --count w28-s1-delegate..cline/84158` = 0 | `~/.cline/worktrees/84158/harness` (not entered) |
| `cline/b0895` | `2d018b1` | 2 (vs local `master` only) | 2 vs local master; **0 vs `origin/master`** | same two IDs | same as above | `~/.cline/worktrees/b0895/harness` (not entered) |

★ `w28-s1-delegate` and `key-pool-s1` overlap: `key-pool-s1` contains `origin/w28-s1-delegate`
(`merge-base --is-ancestor` YES), so 47 of its 52 unique IDs are shared with `w28-s1-delegate`;
the 5 commits only on `key-pool-s1` are `996975a` "S1: rate-limit handling and key failover for the
model router", `9803f55` "S1b: per-key requests and tokens per minute limits", `014452f` "Book the
provider-reported cost for models with no rate", `c84befb` "Refuse real calls to models with no
rate and no reported cost", `4f37538` "Starter operator rate card for native Anthropic models"
(`git branch -a --contains` shows only `key-pool-s1` holds them). Loss risk if that worktree is
ever cleaned.

### B2. Local branches with 0 commits not on master

All verified with `git rev-list --count master..<b>` = 0 (and = 0 vs `origin/master` too, except
where noted). None carries unique work.

`byo-claim-exec-started` (`c91c92d`), `ci-nightly-greens` (`5d0d7bd`),
`cline/34efb` (`31bf407`), `cline/71253` (`31bf407`), `cline/73859` (`31bf407`),
`cline/9e606` (`e8c9e30`), `cline/a402e` (`328622e`), `h1-tool-diagnostics` (`1d9a14a`),
`live-pg-probes` (`3f387eb`), `master` (`b822923`, trunk), `nightly-context-roundtrip` (`0c4fe9e`),
`portable-container-proof-tests` (`730b771`), `s12/enforcement-state` (`b12d839`),
`s12/scale-ops` (`abca369`), `status-2026-11-04` (`fd51529`), `status-byo-merged` (`6cb86e4`),
`status-db-boot-forensics` (`9cdda27`), `team-model-prefs` (`a2fd243`),
`ui-audit-batch-a` (`e351ddc`), `w1-baseline-2026-11-04` (`4864a11`),
`w1-day0-egress` (`83e8562`), `w1-engine-tools` (`f9b205a`),
`w17-lost-lease-redispatch` (`76317e4`), `w24-window-2026-11-03` (`300a300`),
`w28-delegation-gate` (`d63426e` — 1 vs local `master`, 0 vs `origin/master`: merged via PR #22),
`w30-candidate-audit` (`fe68f09`), `w31-scheduler-experiment` (`d805458`),
`w32-seam-analysis` (`7a64d9b`).

## C. Remote-tracking branches — commits not on master

Base `origin/master` = `1753171`. 33 refs (incl. `origin/HEAD` pointing at it); zero-unique grouped.

| Ref | Tip | Total not on origin/master | Unique | Unique commit IDs | Note |
|---|---|---|---|---|---|
| `origin/w28-s1-delegate` | `19f5cae` | 49 | **46** | the 46 IDs of B1 `w28-s1-delegate` up to `19f5caeff9` (everything except the 34 unpushed) | PR #24 per 11-11 file; not re-verified live |
| `origin/codex/harness-a4` | `5458e63` | 9 | 9 | same set as B1 `codex/harness-a4` | 0 unpushed locally |
| `origin/codex/harness-a1` | `03551cb` | 5 | 5 | B1 set minus `cd4049d765` | local is 1 ahead |
| `origin/codex/harness-a3` | `44f3c29` | 4 | 4 | B1 set minus `36f1565e85 65453db5ee` | local is 2 ahead |
| `origin/codex/harness-a2` | `b334ceb` | 2 | 2 | `dc4d9634be b334cebd1d` | matches local |
| `origin/codex/harness-a5` | `7f852d5` | 2 | 2 | `e0446797cc 7f852d506a` | local is 1 ahead (`57a1dea` unpushed) |
| `origin/flaky-sqlite-busy-timeout` | `2348db1` | 1 | 1 | `2348db1ee6` | matches local |
| `origin/imgbot` | `2e832ae` | 1 | **1** | `2e832ae21f` "[ImgBot] Optimize images" (2026-10-20) | remote-only, bot-managed |
| `origin/master` | `1753171` | 5 (vs local master) | 3 | `d63426ee6d 618d7478f0 2d018b11a0` (the other 2 are merges) | ahead of local master |
| `origin/w28-delegation-gate` | `d63426e` | 1 (vs local master) | 1 | `d63426ee6d` | merged to `origin/master` via PR #22 — patch-equivalent everywhere else |
| `origin/claude/path-to-9-finish-ax6hie` | `5f964cf` | 0 | 0 | — | remote-only |
| `origin/cloud/path-to-9-finish` | `bd48c41` | 0 | 0 | — | remote-only |
| `origin/docs-w17-shipped` | `bc5c22f` | 0 | 0 | — | remote-only |
| `origin/pilot-drills-and-legal` | `85bd2a9` | 0 | 0 | — | remote-only, no local counterpart |
| `origin/pilot-remediation-batch6` | `46294b2` | 0 | 0 | — | remote-only, no local counterpart |

Zero-unique remote refs not repeated in the table (mirror their local twin at the same tip):
`origin/byo-claim-exec-started` `c91c92d`, `origin/ci-nightly-greens` `5d0d7bd`,
`origin/live-pg-probes` `3f387eb`, `origin/nightly-context-roundtrip` `0c4fe9e`,
`origin/portable-container-proof-tests` `730b771`, `origin/provider-registry` `e0017f4`
(local is 1 ahead), `origin/status-2026-11-04` `fd51529`, `origin/status-byo-merged` `6cb86e4`,
`origin/status-db-boot-forensics` `9cdda27`, `origin/team-model-prefs` `a2fd243`,
`origin/ui-audit-batch-a` `e351ddc`, `origin/w1-engine-tools` `f9b205a`,
`origin/w17-lost-lease-redispatch` `76317e4`, `origin/w24-window-2026-11-03` `300a300`,
`origin/w30-candidate-audit` `fe68f09`, `origin/w31-scheduler-experiment` `d805458`,
`origin/w32-seam-analysis` `7a64d9b`.

## D. Worktrees — 25, with provenance

Worktrees under `/Users/user/.cline/` were not entered (home-path policy). Dirty = `git -C <wt>
status --short | wc -l`. Unique commits are the attached branch's (§B1); the 5 detached workbenches
plus `harness-wb-codex` sit at `7c1acf4`, which is contained in `w28-s1-delegate` and
`key-pool-s1` only.

| # | Worktree path | HEAD | Branch | Dirty | Unique-commit provenance |
|---|---|---|---|---|---|
| 1 | `/Users/user/untitled folder 2/harness` | `6207006` | `w28-s1-delegate` ★primary | 33 | 80 unique (34 unpushed) |
| 2 | `~/.cline/worktrees/34efb/harness` | `31bf407` | `cline/34efb` | not run | 0 unique |
| 3 | `~/.cline/worktrees/71253/harness` | `31bf407` | `cline/71253` | not run | 0 unique |
| 4 | `~/.cline/worktrees/73859/harness` | `31bf407` | `cline/73859` | not run | 0 unique |
| 5 | `~/.cline/worktrees/84158/harness` | `2d018b1` | `cline/84158` | not run | 0 unique vs origin/master |
| 6 | `~/.cline/worktrees/9e606/harness` | `e8c9e30` | `cline/9e606` | not run | 0 unique |
| 7 | `~/.cline/worktrees/a402e/harness` | `328622e` | `cline/a402e` | not run | 0 unique |
| 8 | `~/.cline/worktrees/b0895/harness` | `2d018b1` | `cline/b0895` | not run | 0 unique vs origin/master |
| 9 | `.../harness-a1` | `cd4049d` | `codex/harness-a1` | 3 | 6 unique (1 unpushed) |
| 10 | `.../harness-a2` | `b334ceb` | `codex/harness-a2` | 1 | 2 unique |
| 11 | `.../harness-a3` | `65453db` | `codex/harness-a3` | 0 | 6 unique (2 unpushed) |
| 12 | `.../harness-a4` | `5458e63` | `codex/harness-a4` | 6 | 9 unique |
| 13 | `.../harness-a5` | `57a1dea` | `codex/harness-a5` | 0 | 3 unique (1 unpushed, no upstream) |
| 14 | `.../harness-a5-deploy-fixes` | `77c658c` | `codex/harness-a5-deploy-fixes` | 0 | 26 unique, effectively unpushed |
| 15 | `.../harness-baseline` | `4864a11` | `w1-baseline-2026-11-04` | 19 | 0 unique |
| 16 | `.../harness-integration` | `c2a6e34` | `integration/local` | 0 | 25 unique, unpushed |
| 17 | `.../harness-integration-test` | `ffb8bd5` | `scratch/integration-test` | 1 | 25 unique, unpushed |
| 18 | `.../harness-s12` | `b12d839` | `s12/enforcement-state` | 4 | 0 unique |
| 19 | `.../harness-s12-b` | `abca369` | `s12/scale-ops` | 0 | 0 unique |
| 20 | `.../harness-wb-codex` | `7c1acf4` | detached | 7 | commits contained in `w28-s1-delegate` |
| 21 | `.../harness-wb-hy3` | `7c1acf4` | detached | 2 | same |
| 22 | `.../harness-wb-hy4` | `7c1acf4` | detached | 44 | same |
| 23 | `.../harness-wb-kp` | `4f37538` | `key-pool-s1` | 0 | 52 unique (5 exclusive to this branch), **no upstream** |
| 24 | `.../harness-wb-oc` | `7c1acf4` | detached | 15 | commits contained in `w28-s1-delegate` |
| 25 | `.../harness-wb-qwen` | `7c1acf4` | detached | 35 | commits contained in `w28-s1-delegate` |

Detached-HEAD note: `7c1acf4` ("D1: a workspace-root scope covers every relative in-workspace path",
2026-11-06) is not an ancestor of `master` or `origin/master`, but is contained in
`w28-s1-delegate` (and `key-pool-s1`), so the detached workbenches hold no commit outside those
branches. (`...` = `/Users/user/untitled folder 2/`.)

## E. Dispositions — proposals only, reviewer decides

Nothing below is executed. "reviewer decides" applies to every row.

### Branches with unique work

| Item | Disposition proposed | Reason (one line) |
|---|---|---|
| `w28-s1-delegate` | **keep (active)** — reviewer decides | Current lane, 34 unpushed commits, active work; PR status per docs not re-verified. |
| `key-pool-s1` | **merge candidate (highest priority)** — reviewer decides | 52 unique commits with 5 found nowhere else and no upstream push; loss risk; needs owner review then push/merge. |
| `codex/harness-a5-deploy-fixes` | **merge candidate** — reviewer decides | 26 unique commits including all of a1–a4's work plus its own deploy fixes; no upstream; supersedes the per-a branches if integrated. |
| `integration/local` | **abandon candidate** — reviewer decides | 25 unique commits identical (patch-for-patch) to `scratch/integration-test`; one of the two names can go. |
| `scratch/integration-test` | **merge candidate (preferred twin)** — reviewer decides | Same 25 commits; keep whichever name the owner prefers; this one has the newer merge tip. |
| `codex/harness-a1` | **merge candidate** — reviewer decides | 6 unique commits (1 unpushed `cd4049d`); already contained inside the a5-deploy-fixes set; merge the superset then drop. |
| `codex/harness-a2` | **merge candidate** — reviewer decides | 2 unique commits, pushed and clean; same superset logic. |
| `codex/harness-a3` | **merge candidate** — reviewer decides | 6 unique commits (2 unpushed `36f1565 65453db`); push before any cleanup. |
| `codex/harness-a4` | **merge candidate** — reviewer decides | 9 unique commits, pushed; 6 dirty entries in its worktree need owner triage first. |
| `codex/harness-a5` | **merge candidate** — reviewer decides | 3 unique commits, tip `57a1dea` unpushed and branch has no upstream; push first. |
| `flaky-sqlite-busy-timeout` | **merge candidate** — reviewer decides | 1 unique pushed test-fixture fix worth keeping. |
| `provider-registry` | **merge candidate** — reviewer decides | 1 unique unpushed docs commit `49cfa55`; push, then reviewer decides. |
| `cline/84158` | **abandon candidate** — reviewer decides | 0 unique vs `origin/master` (both commits merged via PR #23); local-only pointer. |
| `cline/b0895` | **abandon candidate** — reviewer decides | Same as `cline/84158`; worktrees 5/8 attach to these. |

### Zero-unique local branches

| Group | Items | Disposition proposed | Reason |
|---|---|---|---|
| Status/CI-record branches | `status-2026-11-04`, `status-byo-merged`, `status-db-boot-forensics`, `ci-nightly-greens`, `nightly-context-roundtrip` | **abandon candidate** — reviewer decides | 0 unique; their commits are merged; the historical record lives in the commits themselves. |
| Merged feature branches | `byo-claim-exec-started`, `h1-tool-diagnostics`, `live-pg-probes`, `portable-container-proof-tests`, `team-model-prefs`, `ui-audit-batch-a`, `w1-engine-tools`, `w1-day0-egress`, `w17-lost-lease-redispatch`, `w24-window-2026-11-03`, `w28-delegation-gate`, `w30-candidate-audit`, `w31-scheduler-experiment`, `w32-seam-analysis`, `s12/enforcement-state`, `s12/scale-ops`, `w1-baseline-2026-11-04` | **abandon candidate** — reviewer decides | 0 unique; tips are ancestors of `origin/master` (spot-verified for `w28-delegation-gate`, `s12/*`, `w1-baseline-2026-11-04`). |
| `cline/34efb`, `cline/71253`, `cline/73859`, `cline/9e606`, `cline/a402e` | 5 branches | **abandon candidate** — reviewer decides | 0 unique; tips `31bf407`/`e8c9e30`/`328622e` reachable from `origin/master`; 5 of the 7 `.cline/` worktrees attach here. |
| `master` | trunk | **keep** — reviewer decides | Never clutter; note it is 5 behind `origin/master` with 0 ahead. |

### Remote-only refs

| Ref | Disposition proposed | Reason |
|---|---|---|
| `origin/imgbot` | **keep** — reviewer decides | 1 unique bot commit (image optimization); bot-managed. |
| `origin/claude/path-to-9-finish-ax6hie`, `origin/cloud/path-to-9-finish`, `origin/docs-w17-shipped`, `origin/pilot-drills-and-legal`, `origin/pilot-remediation-batch6` | **abandon candidate** — reviewer decides | 0 unique commits; merged clutter on the remote; deletion is a remote-side owner action, not run here. |

### Worktrees

| Item | Disposition proposed | Reason |
|---|---|---|
| Primary `harness` (33 dirty entries) | **keep** — reviewer decides | Active lane; the 33 dirty entries are other lanes' live work (the 11-11 file's do-not-clean list, grown from 23). |
| `harness-wb-kp` | **keep** — reviewer decides | Only home of the 5 key-pool exclusive commits; clean tree; resolve the branch first. |
| `harness-a1` (3), `harness-a2` (1), `harness-a4` (6), `harness-baseline` (19), `harness-integration-test` (1), `harness-s12` (4) | **keep until triaged** — reviewer decides | Dirty counts 1–19; live uncommitted work, owner triage per base-document policy. |
| `harness-wb-hy4` (44), `harness-wb-qwen` (35), `harness-wb-oc` (15), `harness-wb-codex` (7), `harness-wb-hy3` (2) | **keep until triaged** — reviewer decides | Detached fleet workbenches with heavy dirty state; contents are uncommitted, so git cannot recover them. |
| `harness-a3`, `harness-a5`, `harness-a5-deploy-fixes`, `harness-integration`, `harness-s12-b` | **remove candidates after branch disposition** — reviewer decides | Clean trees; removable once their branches are merged/abandoned. |
| 7 `~/.cline/worktrees/*` | **undetermined** — reviewer decides | Not entered (home-path policy); dirty state unknown. |

## F. Done-when check

Every ref from `git branch -a` (41 local + 33 remote-tracking incl. `origin/HEAD` noted) and every
worktree from `git worktree list` (25) appears in §B/§C/§D with a count and, where >0, explicit
commit IDs. Dispositions for all are in §E, each marked "reviewer decides".
