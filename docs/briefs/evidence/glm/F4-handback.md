# Hand-back: F4
Model: GLM 5.3 Flash   Date: 2026-11-14   Revision: 6207006
Status: DONE
Token use (estimate): 110000

Full data with every ref and every commit ID: `docs/briefs/evidence/glm/F4-branch-inventory-2026-11-14.md` (new, 227 lines). This hand-back quotes the proving outputs in short form; the reviewer can rerun each command verbatim.

## Files changed (uncommitted)

- `docs/briefs/evidence/glm/F4-branch-inventory-2026-11-14.md` (new) — the inventory: 41 local branches, 33 remote-tracking refs, 25 worktrees, per-ref unique-commit IDs, dispositions.
- `docs/briefs/evidence/glm/F4-handback.md` (new) — this file.

Both files are in the packet's editable list. No other file was touched; no branch, worktree, or git ref was changed. `git rev-parse --short HEAD` before and after writes: `6207006` (verified at start; the writes are untracked files, so HEAD cannot move).

## Checks run

1. **HEAD revision.** `git -C "/Users/user/untitled folder 2/harness" rev-parse --short HEAD` → `6207006`.
2. **All branches.** `git branch -a` → 41 local names + 33 `remotes/origin/*` (incl. `origin/HEAD -> origin/master`). Full output quoted in inventory §A/§B/§C. 6 remote refs have no local twin: `claude/path-to-9-finish-ax6hie`, `cloud/path-to-9-finish`, `docs-w17-shipped`, `imgbot`, `pilot-drills-and-legal`, `pilot-remediation-batch6`.
3. **All worktrees.** `git worktree list` → 25 rows: primary + 7 under `~/.cline/worktrees/` + 17 under `untitled folder 2/`, of which 5 detached at `7c1acf4` (`harness-wb-codex`, `wb-hy3`, `wb-hy4`, `wb-oc`, `wb-qwen`).
4. **Unique commits per branch.** Loop of `git rev-list --count master..<b>` and `git cherry master <b> | grep -c '^+'` over all 41 local branches, repeated against `origin/master`. Raw loop output quoted in the session; results table in inventory §B1/§B2. Branches with unique>0: `w28-s1-delegate` 80, `key-pool-s1` 52, `codex/harness-a5-deploy-fixes` 26, `integration/local` 25, `scratch/integration-test` 25, `codex/harness-a4` 9, `codex/harness-a1` 6, `codex/harness-a3` 6, `codex/harness-a5` 3, `codex/harness-a2` 2, `flaky-sqlite-busy-timeout` 1, `provider-registry` 1. Sample raw lines: `w28-s1-delegate total_not_reachable=83 truly_unique=80`, `key-pool-s1 total_not_reachable=55 truly_unique=52`, `provider-registry total_not_reachable=1 truly_unique=1`.
5. **Commit IDs.** `git cherry master <b> | grep '^+' | cut -c3-12` per unique branch; full ID lists in inventory §B1 (e.g. `codex/harness-a2` → `dc4d9634be b334cebd1d`).
6. **key-pool exclusivity (loss risk).** `git rev-list --count key-pool-s1 --not w28-s1-delegate` → `5`; `git log --format='%h %s' key-pool-s1 --not w28-s1-delegate` → `4f37538 Starter operator rate card...`, `c84befb Refuse real calls to models with no rate and no reported cost`, `014452f Book the provider-reported cost...`, `9803f55 S1b: per-key requests and tokens per minute limits...`, `996975a S1: rate-limit handling and key failover for the model router`. `git branch -a --contains 996975a` → only `+ key-pool-s1`. `git rev-parse` upstream check: `key-pool-s1 NO-UPSTREAM`.
7. **Twin integration branches.** `diff <(git cherry master integration/local | grep '^+' | cut -c3-12) <(git cherry master scratch/integration-test | grep '^+' | cut -c3-12)` → `SETS-IDENTICAL` (25 identical IDs).
8. **Unpushed work.** `git rev-list --count origin/<b>..<b>`: `w28-s1-delegate unpushed=34` (0 behind), `codex/harness-a1 unpushed=1`, `codex/harness-a3 unpushed=2`, `provider-registry unpushed=1`, `codex/harness-a5 unpushed=1`, `key-pool-s1 NO-UPSTREAM`. `codex/harness-a5-deploy-fixes`, `integration/local`, `scratch/integration-test` have no upstream ref at all.
9. **Worktree dirty counts** (home-path policy excludes `~/.cline/`): `git -C <wt> status --short | wc -l` → `harness-a1 dirty=3`, `harness-a2 dirty=1`, `harness-a3 dirty=0`, `harness-a4 dirty=6`, `harness-a5 dirty=0`, `harness-a5-deploy-fixes dirty=0`, `harness-baseline dirty=19`, `harness-integration dirty=0`, `harness-integration-test dirty=1`, `harness-s12 dirty=4`, `harness-s12-b dirty=0`, `harness-wb-codex dirty=7`, `harness-wb-hy3 dirty=2`, `harness-wb-hy4 dirty=44`, `harness-wb-kp dirty=0`, `harness-wb-oc dirty=15`, `harness-wb-qwen dirty=35`; primary checkout `git status --short | wc -l` → `33`.
10. **Trunk lag.** `git rev-list --count master..origin/master` → `5`; `git cherry master origin/master` → `+ d63426e...`, `+ 618d747...`, `+ 2d018b1...` (3 non-merge; the other 2 are PR merges `5e84edc`, `1753171`); `git rev-list --count origin/master..master` → `0`.
11. **Detached workbench containment.** `git merge-base --is-ancestor 7c1acf4 master` → `NOT-ancestor`; `git branch --all --contains 7c1acf4` → `+ key-pool-s1`, `* w28-s1-delegate` only; `git log -1 ... 7c1acf4` → `D1: a workspace-root scope covers every relative in-workspace path 2026-11-06`.
12. **Merged-tip spot checks.** `git merge-base --is-ancestor d63426e origin/master` → YES (so `w28-delegation-gate`'s 1 "unique vs local master" commit is already on `origin/master` via PR #22); same YES for `4864a11` (`w1-baseline-2026-11-04`), `b12d839` (`s12/enforcement-state`), `abca369` (`s12/scale-ops`).
13. **Read-first done.** `docs/plans/branch-inventory-2026-11-11.md` (full) and `docs/briefs/PROJECT-STATE.md` §4–§5 read; carried forward its home-path policy and do-not-clean list.

## Checks not run

- **No fetch.** All origin comparisons use last-fetched remote-tracking refs; the remote may have moved.
- **`~/.cline/` worktree dirty counts** — not entered, per the base inventory's home-path policy; 7 rows marked `not run`.
- **PR status** — PR #24 (from the 11-11 doc) not re-verified with `gh`; no `gh` call in this packet.
- **No tests/CI** — inventory-only task; nothing built or run beyond git reads.
- **imgbot commit content** — only ID/subject/date quoted (`2e832ae`, "[ImgBot] Optimize images", 2026-10-20); image diffs not reviewed.

## Decisions I made (reviewer may overrule)

1. Defined "unique" as `git cherry` `+` lines (patch-equivalents and merges excluded) and "total" as `rev-list --count`; both are quoted so the reviewer can re-derive either. The prompt said "commit IDs that are not on master"; `master` (local) is 5 behind `origin/master`, so I measured against both and flagged the difference — the code wins where the two disagree (e.g. `w28-delegation-gate` is 1 vs local `master`, 0 vs `origin/master`).
2. Measured against local `master` first, then re-ran the unique loop against `origin/master` for every branch because of that 5-commit lag.
3. Proposed `codex/harness-a5-deploy-fixes` as the superset merge candidate (its 26 unique IDs contain a1–a4's unique sets plus its own `77c658c`), instead of merging five branches separately.
4. Between the identical twins `integration/local` / `scratch/integration-test`, proposed keeping `scratch/integration-test` (newer merge tip) and abandoning `integration/local`.
5. Treated `branch -vv`'s "ahead 1" on `codex/harness-a5` as stale metadata because it has no upstream line; verified with `git rev-list --count origin/codex/harness-a5..codex/harness-a5` → 1 and `for-each-ref upstream=` empty.
6. Kept the base document's home-path policy: did not enter `~/.cline/` worktrees.
7. Recorded the 42-vs-41 local-branch-count discrepancy against the 11-11 doc without trying to explain it (prompt-vs-code rule: today's `git branch -a` output is the truth).

## Open questions

1. Where should `w28-s1-delegate`'s 34 unpushed commits land — is PR #24 still the vehicle, or a new PR after 6207006?
2. Push policy for `key-pool-s1`'s 5 exclusive commits (currently only in `harness-wb-kp`, no upstream) — push to a remote branch before any review?
3. Which integration twin should survive, `integration/local` or `scratch/integration-test`?
4. Was a local branch deliberately deleted between 2026-11-11 (documented 42) and today (41)?
5. `origin/imgbot`'s 1 unique commit — keep per bot policy or abandon with the other merged remote refs?

## Unrelated issues seen

- `git branch -vv` shows stale tracking metadata on `codex/harness-a5` ("[origin/codex/harness-a5: ahead 1]" with no upstream configured per `for-each-ref`) — cosmetic, but it misleads.
- Primary-checkout dirty entries grew 23 (11-11 doc) → 33 at my ~09:40 snapshot → 44 by ~09:52, from other lanes' concurrent tracked-file edits (11 ` M` entries incl. `.env.example`, `section4/src/harness_orchestrator/*.py`, `section4/tests/*`, `section7/e2e/phone.e2e.ts`); not inspected beyond the count, per lane boundaries.
- `git status --short` collapses my two new files into the pre-existing untracked dir entry `?? docs/briefs/evidence/glm/` (which also holds `probe-glm-5.3-flash-handback.md`, dated 08:56, before this session — not mine, untouched).
- Local `master` is 5 behind `origin/master` with 0 ahead — trivially fast-forwardable by the owner, not done here (no git writes allowed).
- The 11-11 doc's remote count (implied 31) vs today's 33: two remote refs (`pilot-drills-and-legal`, `pilot-remediation-batch6`) were apparently fetched since; no fetch was run by this task.

## Known limits of this change

- Inventory and proposals only; no branch, worktree, or remote was changed, and no disposition was executed.
- All origin-relative numbers are against last-fetched refs (no fetch allowed/run).
- `~/.cline/` worktrees (7 of 25) were not entered, so their dirty state is unknown.
- Commit IDs are quoted as 10-char shorts; full SHAs are recoverable with `git rev-parse <short>`.
- Worktree dirty counts are entry counts (`status --short | wc -l`), not a content review; uncommitted changes were not read.
