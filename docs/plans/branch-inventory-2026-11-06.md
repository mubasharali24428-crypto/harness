# Branch & worktree inventory — 2026-11-06

Read-only inventory. Nothing here is a decision: no branch was deleted, pruned, checked out, merged, fetched, pulled, repacked or gc'd, and no worktree was added or removed. The only file written by this task is this one.

## Provenance

- Repository queried: `/Users/user/untitled folder 2/harness` (the primary worktree, `git worktree list` row 1). The task's `WORKTREE:` value arrived as a literal placeholder and no `harness-wb-hy4` / `harness-wb-hy3` path exists, so this checkout was used, matching the previous hand-back (`docs/briefs/day1-cal-hy4-handback.md`).
- `origin/master` = `17531711babf1ab69ec18379e4c375f252266068` (`git rev-parse --verify origin/master`, rc 0). It was used exactly as the existing local ref; **no fetch was run**, so "behind" is relative to the last-fetched `origin/master`, not to the remote today.
- Snapshot taken 2026-11-06 02:37–02:41 PST (= 2026-11-06T10:37–10:41Z). The 48-hour window used `NOW=1793961710` (2026-11-06T10:41:50Z), cutoff epoch `1793788910` = 2026-11-04T10:41:50Z.
- Worktrees under `/Users/user/.cline/` were **not** entered (that is the real home directory). For those 7 rows the dirty-file count is `not run (home path)`; path, branch and head hash come from refs read in the primary checkout (`git worktree list`, `git rev-parse`).
- This checkout is shared with other concurrent workers: its dirty-file count went 5 → 6 during the run (`?? docs/briefs/hy4-master-prompt-batch6-2026-11-06.md` appeared). Counts are a snapshot, not a stable property.
- Column A's "last commit" uses `git log -1 --format='%cs %s'`; `%cs` is date-only (no time). The 48-hour test therefore used the committer timestamp `%ct` instead. Three branches whose `%cs` reads `2026-11-04` fall just outside the 48 h window by `%ct`: `byo-claim-exec-started` (1793785432), `w1-baseline-2026-11-04` (1793786385), `status-byo-merged` (1793786582) vs cutoff 1793788910.

Exact commands used (run from `/Users/user/untitled folder 2/harness`, or with `git -C "<path>"` for worktree rows):

```
git for-each-ref --format='%(refname:short)' refs/heads
git rev-parse --abbrev-ref "<branch>@{upstream}"            # rc 128 => "none"
git rev-list --left-right --count origin/master..."<branch>"
git cherry origin/master "<branch>" | grep -c '^+'           # grep rc 1 when the count is 0
git log -1 --format='%cs %s' "<branch>"                      # subject cut to 60 characters
git log -1 --format='%ct' "<branch>"                         # for the 48-hour test only
git worktree list
git worktree list --porcelain                                # used to rebuild B after a parse glitch
git -C "<path>" status --short | wc -l                        # skipped for /Users/user/.cline/ paths
```

## A. Local branches (41)

`behind/ahead` is `git rev-list --left-right --count origin/master...<branch>`, which prints **BEHIND** (commits in origin/master not in the branch) first, then **AHEAD** (commits in the branch not in origin/master). `unique` is `git cherry origin/master <branch> | grep -c '^+'` — commits with no patch-equivalent in origin/master, so it can be lower than AHEAD when patches were already applied (e.g. merge commits, re-applied changes).

| branch | upstream | behind/ahead (BEHIND / AHEAD) | unique | last commit (`%cs %s`, subject ≤60 chars) |
|---|---|---|---|---|

| `byo-claim-exec-started` | `origin/byo-claim-exec-started` | 33 / 0 | 0 | `2026-11-04 section1: drop the unused exception binding ruff flags (F841` |
| `ci-nightly-greens` | `origin/ci-nightly-greens` | 30 / 0 | 0 | `2026-11-04 gitleaks: allowlist verified synthetic full-history fixtures` |
| `cline/34efb` | `none` | 394 / 0 | 0 | `2026-10-17 fix(ci): trim gitleaks inline annotations under the 100-col ` |
| `cline/71253` | `none` | 394 / 0 | 0 | `2026-10-17 fix(ci): trim gitleaks inline annotations under the 100-col ` |
| `cline/73859` | `none` | 394 / 0 | 0 | `2026-10-17 fix(ci): trim gitleaks inline annotations under the 100-col ` |
| `cline/84158` | `none` | 3 / 0 | 0 | `2026-11-04 W28 S1a: document HARNESS_SUBAGENTS in .env.example` |
| `cline/9e606` | `none` | 49 / 0 | 0 | `2026-11-03 Handoff: W17 lost-lease re-dispatch gap for owner` |
| `cline/a402e` | `none` | 75 / 0 | 0 | `2026-10-31 Decision record: Hx is the Harness language surface, Hermes ` |
| `cline/b0895` | `none` | 3 / 0 | 0 | `2026-11-04 W28 S1a: document HARNESS_SUBAGENTS in .env.example` |
| `codex/harness-a1` | `origin/codex/harness-a1` | 394 / 6 | 6 | `2026-10-17 router: opt-in live OpenRouter test (free models only, skipp` |
| `codex/harness-a2` | `origin/codex/harness-a2` | 394 / 2 | 2 | `2026-10-17 fix(a2): serialize in-process engine env mutation (cross-run` |
| `codex/harness-a3` | `origin/codex/harness-a3` | 394 / 6 | 6 | `2026-10-17 sandbox: live Docker tests for timeout kill, memory and pid ` |
| `codex/harness-a4` | `origin/codex/harness-a4` | 394 / 9 | 9 | `2026-10-17 chore(deps): electron 44.9.0 and latest packager (clears aud` |
| `codex/harness-a5` | `none` | 394 / 3 | 3 | `2026-10-17 fix(a5): extensions hardening - agent identity from auth, hu` |
| `codex/harness-a5-deploy-fixes` | `none` | 394 / 31 | 26 | `2026-10-17 fix(deploy): install security-policy in image+CI, declare as` |
| `flaky-sqlite-busy-timeout` | `origin/flaky-sqlite-busy-timeout` | 32 / 1 | 1 | `2026-11-04 section4 tests: the shared fixture's sqlite engine waits out` |
| `h1-tool-diagnostics` | `none` | 224 / 0 | 0 | `2026-10-25 H1: adopt the Hermes diagnostic contract for tool output` |
| `integration/local` | `none` | 394 / 30 | 25 | `2026-10-17 integrate: codex/harness-a5` |
| `live-pg-probes` | `origin/live-pg-probes` | 28 / 0 | 0 | `2026-11-04 CI: run live-PG probes in nightly and make them deterministi` |
| `master` | `origin/master` | 5 / 0 | 0 | `2026-11-04 Merge pull request #21 from mubasharali24428-crypto/w30-cand` |
| `nightly-context-roundtrip` | `origin/nightly-context-roundtrip` | 20 / 0 | 0 | `2026-11-04 status: record the CI repair wave with its verification runs` |
| `portable-container-proof-tests` | `origin/portable-container-proof-tests` | 27 / 0 | 0 | `2026-11-04 tests: accept EACCES as a kernel refusal for read-only mount` |
| `provider-registry` | `origin/provider-registry` | 225 / 1 | 1 | `2026-10-25 docs: H0 is now two items — teammate names removed, IP pol` |
| `s12/enforcement-state` | `none` | 295 / 0 | 0 | `2026-10-20 Parts 11/29: anchor the audit chain head outside the databas` |
| `s12/scale-ops` | `none` | 290 / 0 | 0 | `2026-10-20 Parts 48/50: leader-elect each maintenance tick on PostgreSQ` |
| `scratch/integration-test` | `none` | 394 / 30 | 25 | `2026-10-17 Merge branch 'codex/harness-a5' into scratch/integration-tes` |
| `status-2026-11-04` | `origin/status-2026-11-04` | 42 / 0 | 0 | `2026-11-03 STATUS: 2026-11-04 session record — PR #7 merged, five CI-` |
| `status-byo-merged` | `origin/status-byo-merged` | 31 / 0 | 0 | `2026-11-04 STATUS: PR #12 merged (4864a11) — BYO claim marks the run;` |
| `status-db-boot-forensics` | `origin/status-db-boot-forensics` | 40 / 0 | 0 | `2026-11-03 STATUS: db post-initdb bounce — the Docker-level look, and` |
| `team-model-prefs` | `origin/team-model-prefs` | 231 / 0 | 0 | `2026-10-25 Let each team choose its model and reasoning effort` |
| `ui-audit-batch-a` | `origin/ui-audit-batch-a` | 230 / 0 | 0 | `2026-10-25 Fix the eight UI faults the 2026-10-25 audit measured` |
| `w1-baseline-2026-11-04` | `none` | 32 / 0 | 0 | `2026-11-04 Merge pull request #12 from mubasharali24428-crypto/byo-clai` |
| `w1-day0-egress` | `none` | 223 / 0 | 0 | `2026-10-25 W1 Day 0: the egress sidecar was never running, and nothing ` |
| `w1-engine-tools` | `origin/w1-engine-tools` | 44 / 0 | 0 | `2026-11-03 CI: name the section7 job what branch protection requires` |
| `w17-lost-lease-redispatch` | `origin/w17-lost-lease-redispatch` | 40 / 0 | 0 | `2026-11-03 W17 lost-lease re-dispatch: attempt-keyed runner jobs, verif` |
| `w24-window-2026-11-03` | `origin/w24-window-2026-11-03` | 40 / 0 | 0 | `2026-11-03 W24 window evidence: the real-window task passes at master H` |
| `w28-delegation-gate` | `origin/w28-delegation-gate` | 4 / 0 | 0 | `2026-11-04 docs: W28 delegation-gate work order` |
| `w28-s1-delegate` | `origin/w28-s1-delegate` | 0 / 45 | 44 | `2026-11-06 D1: a workspace-root scope covers every relative in-workspac` |
| `w30-candidate-audit` | `origin/w30-candidate-audit` | 6 / 0 | 0 | `2026-11-04 docs: close stage 7 with written W30 candidate decisions and` |
| `w31-scheduler-experiment` | `origin/w31-scheduler-experiment` | 10 / 0 | 0 | `2026-11-04 experiment: report regenerated against the split Scheduler t` |
| `w32-seam-analysis` | `origin/w32-seam-analysis` | 8 / 0 | 0 | `2026-11-04 docs: ratify the W31 pilot (ACCEPTED, pilot scope only) and ` |

## B. Worktrees (23)

Head hash and branch label come from `git worktree list` / `git worktree list --porcelain` run in the primary checkout. Dirty count is `git -C "<path>" status --short | wc -l` (cross-checked against `grep -c .`, identical for every permitted path). The 4 `harness-wb-*` trees are detached at `7c1acf4…`, which is also the tip of `w28-s1-delegate`; a detached tree is not "checked out" for any branch row in section C.

| worktree path | branch (or detached) | head hash | dirty files |
|---|---|---|---|

| `/Users/user/untitled folder 2/harness` | `w28-s1-delegate` | `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5` | 6 |
| `/Users/user/.cline/worktrees/34efb/harness` | `cline/34efb` | `31bf4077e9b567b1f50ecb99818d501baab3115a` | not run (home path) |
| `/Users/user/.cline/worktrees/71253/harness` | `cline/71253` | `31bf4077e9b567b1f50ecb99818d501baab3115a` | not run (home path) |
| `/Users/user/.cline/worktrees/73859/harness` | `cline/73859` | `31bf4077e9b567b1f50ecb99818d501baab3115a` | not run (home path) |
| `/Users/user/.cline/worktrees/84158/harness` | `cline/84158` | `2d018b11a0f478b9e73e597719985b76e9df8fbc` | not run (home path) |
| `/Users/user/.cline/worktrees/9e606/harness` | `cline/9e606` | `e8c9e300911259b0327ed1a406f569e603710ed7` | not run (home path) |
| `/Users/user/.cline/worktrees/a402e/harness` | `cline/a402e` | `328622eb0d4c491dcf1c01a8d508337f2c9ca163` | not run (home path) |
| `/Users/user/.cline/worktrees/b0895/harness` | `cline/b0895` | `2d018b11a0f478b9e73e597719985b76e9df8fbc` | not run (home path) |
| `/Users/user/untitled folder 2/harness-a1` | `codex/harness-a1` | `cd4049d765da8c724d544661d9776dd6d6f738b8` | 3 |
| `/Users/user/untitled folder 2/harness-a2` | `codex/harness-a2` | `b334cebd1d422f03850b56abcdcf3638cbb13e56` | 1 |
| `/Users/user/untitled folder 2/harness-a3` | `codex/harness-a3` | `65453db5ee2bd4f8f785a4bdca9b07505c3421fe` | 0 |
| `/Users/user/untitled folder 2/harness-a4` | `codex/harness-a4` | `5458e63f2004f5fe5972d86b84267b9a7ec589fa` | 6 |
| `/Users/user/untitled folder 2/harness-a5` | `codex/harness-a5` | `57a1dea0e119ada38f814213a4b2a851fe799ac7` | 0 |
| `/Users/user/untitled folder 2/harness-a5-deploy-fixes` | `codex/harness-a5-deploy-fixes` | `77c658ce1d2aa0b143c68096b29e20f3a37124e6` | 0 |
| `/Users/user/untitled folder 2/harness-baseline` | `w1-baseline-2026-11-04` | `4864a11754983b44f272580bca94cb1c3e9f7ac7` | 19 |
| `/Users/user/untitled folder 2/harness-integration` | `integration/local` | `c2a6e3459fe2a397d6c157718d394636e459f77a` | 0 |
| `/Users/user/untitled folder 2/harness-integration-test` | `scratch/integration-test` | `ffb8bd5616a57dd159b88c5831ef95caaec4bbb4` | 1 |
| `/Users/user/untitled folder 2/harness-s12` | `s12/enforcement-state` | `b12d8398f1136df27964462eb3d8234f599b6e31` | 4 |
| `/Users/user/untitled folder 2/harness-s12-b` | `s12/scale-ops` | `abca369ad2e0dd92eba0d0d160a54c444976091a` | 0 |
| `/Users/user/untitled folder 2/harness-wb-codex` | detached | `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5` | 7 |
| `/Users/user/untitled folder 2/harness-wb-hy3` | detached | `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5` | 2 |
| `/Users/user/untitled folder 2/harness-wb-oc` | detached | `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5` | 0 |
| `/Users/user/untitled folder 2/harness-wb-qwen` | detached | `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5` | 0 |

## C. SUGGESTED class per branch (suggestions only — nothing is decided here)

Rules as given, applied with precedence `active` → `unique-needs-human-review` → `merged-clutter`:

- **active** — checked out in a worktree with dirty files, OR the last commit is within 48 hours of the snapshot.
- **unique-needs-human-review** — `unique` > 0 and not already active.
- **merged-clutter** — `unique` = 0 and the branch is not checked out in a worktree with dirty files, and not active.
- **UNDETERMINED** — added by this inventory, not by the brief: `unique` = 0, older than 48 h, and the only checkout is under `/Users/user/.cline/`, where the dirty count was not run. The merged-clutter rule's second condition cannot be evaluated from refs alone. These 5 rows are `merged-clutter` **if** their home worktree is clean and `active` **if** it is dirty; that needs a human or a permitted run in the home path.

| branch | unique | checked out in a dirty worktree? | last commit within 48 h? | SUGGESTED class |
|---|---|---|---|---|

| `byo-claim-exec-started` | 0 | no (not checked out) | no | **merged-clutter** |
| `ci-nightly-greens` | 0 | no (not checked out) | yes | **active** |
| `cline/34efb` | 0 | unknown (home path: /Users/user/.cline/worktrees/34efb/harness) | no | **UNDETERMINED** |
| `cline/71253` | 0 | unknown (home path: /Users/user/.cline/worktrees/71253/harness) | no | **UNDETERMINED** |
| `cline/73859` | 0 | unknown (home path: /Users/user/.cline/worktrees/73859/harness) | no | **UNDETERMINED** |
| `cline/84158` | 0 | unknown (home path: /Users/user/.cline/worktrees/84158/harness) | yes | **active** |
| `cline/9e606` | 0 | unknown (home path: /Users/user/.cline/worktrees/9e606/harness) | no | **UNDETERMINED** |
| `cline/a402e` | 0 | unknown (home path: /Users/user/.cline/worktrees/a402e/harness) | no | **UNDETERMINED** |
| `cline/b0895` | 0 | unknown (home path: /Users/user/.cline/worktrees/b0895/harness) | yes | **active** |
| `codex/harness-a1` | 6 | yes (3) | no | **active** |
| `codex/harness-a2` | 2 | yes (1) | no | **active** |
| `codex/harness-a3` | 6 | no (0) | no | **unique-needs-human-review** |
| `codex/harness-a4` | 9 | yes (6) | no | **active** |
| `codex/harness-a5` | 3 | no (0) | no | **unique-needs-human-review** |
| `codex/harness-a5-deploy-fixes` | 26 | no (0) | no | **unique-needs-human-review** |
| `flaky-sqlite-busy-timeout` | 1 | no (not checked out) | yes | **active** |
| `h1-tool-diagnostics` | 0 | no (not checked out) | no | **merged-clutter** |
| `integration/local` | 25 | no (0) | no | **unique-needs-human-review** |
| `live-pg-probes` | 0 | no (not checked out) | yes | **active** |
| `master` | 0 | no (not checked out) | yes | **active** |
| `nightly-context-roundtrip` | 0 | no (not checked out) | yes | **active** |
| `portable-container-proof-tests` | 0 | no (not checked out) | yes | **active** |
| `provider-registry` | 1 | no (not checked out) | no | **unique-needs-human-review** |
| `s12/enforcement-state` | 0 | yes (4) | no | **active** |
| `s12/scale-ops` | 0 | no (0) | no | **merged-clutter** |
| `scratch/integration-test` | 25 | yes (1) | no | **active** |
| `status-2026-11-04` | 0 | no (not checked out) | no | **merged-clutter** |
| `status-byo-merged` | 0 | no (not checked out) | no | **merged-clutter** |
| `status-db-boot-forensics` | 0 | no (not checked out) | no | **merged-clutter** |
| `team-model-prefs` | 0 | no (not checked out) | no | **merged-clutter** |
| `ui-audit-batch-a` | 0 | no (not checked out) | no | **merged-clutter** |
| `w1-baseline-2026-11-04` | 0 | yes (19) | no | **active** |
| `w1-day0-egress` | 0 | no (not checked out) | no | **merged-clutter** |
| `w1-engine-tools` | 0 | no (not checked out) | no | **merged-clutter** |
| `w17-lost-lease-redispatch` | 0 | no (not checked out) | no | **merged-clutter** |
| `w24-window-2026-11-03` | 0 | no (not checked out) | no | **merged-clutter** |
| `w28-delegation-gate` | 0 | no (not checked out) | yes | **active** |
| `w28-s1-delegate` | 44 | yes (5) | yes | **active** |
| `w30-candidate-audit` | 0 | no (not checked out) | yes | **active** |
| `w31-scheduler-experiment` | 0 | no (not checked out) | yes | **active** |
| `w32-seam-analysis` | 0 | no (not checked out) | yes | **active** |

## D. Counts

- active: 19 branches
- unique-needs-human-review: 5 branches
- merged-clutter: 12 branches
- (outside the three classes) UNDETERMINED — home-path dirty count not run: 5 branches (`cline/34efb`, `cline/71253`, `cline/73859`, `cline/9e606`, `cline/a402e`); 19 + 5 + 12 + 5 = 41 = all local branches.
- Branches with `unique` > 0 (11): `codex/harness-a1` (6), `codex/harness-a2` (2), `codex/harness-a3` (6), `codex/harness-a4` (9), `codex/harness-a5` (3), `codex/harness-a5-deploy-fixes` (26), `flaky-sqlite-busy-timeout` (1), `integration/local` (25), `provider-registry` (1), `scratch/integration-test` (25), `w28-s1-delegate` (44).

Facts worth stating alongside the counts: 30 of the 41 branches have AHEAD = 0 and the same 11 branches have either AHEAD > 0 or `unique` > 0 (the two sets coincide, but the numbers differ where `git cherry` found patch-equivalents: `codex/harness-a5-deploy-fixes` 31/26, `integration/local` 30/25, `scratch/integration-test` 30/25, `w28-s1-delegate` 45/44). No branch has BEHIND 0 and AHEAD 0, i.e. none is identical to the last-fetched `origin/master`. `master` is 5 behind `origin/master`, ahead 0, and is not checked out in any worktree. No `git log origin/master..<branch>` range was inspected, so nothing in this file states what any branch is for.
