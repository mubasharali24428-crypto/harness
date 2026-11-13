# Branch & worktree inventory — 2026-11-11 (delta over 2026-11-06)

Read-only delta inventory for task **P-02 / F-02** of `docs/plans/90-day-execution-plan-2026-11-11.md`.
Nothing here is a decision: no branch was deleted, pruned, checked out, merged, fetched, pulled, repacked
or gc'd; no worktree was added or removed. Base document: `docs/plans/branch-inventory-2026-11-06.md`
(41 branches, 23 worktrees, full per-branch table and commands). This file records only what changed,
what is new, and the untracked-work disposition list. Carry forward the base document's §C classes
except where updated below.

## Provenance

- Repository: `/Users/user/untitled folder 2/harness` (primary worktree), snapshot 2026-11-11.
- **No fetch was run.** `origin/master` = `17531711babf1ab69ec18379e4c375f252266068`, verified unchanged
  from the 2026-11-06 snapshot (`git rev-parse origin/master`). All behind/ahead counts are relative to
  last-fetched refs, not to the remote today.
- Worktrees under `/Users/user/.cline/` were **not entered** (home-path policy from the base inventory).
  Their dirty counts remain `not run (home path)`; the 5 UNDETERMINED rows in base §C stay UNDETERMINED.
- Branch tips verified unchanged since 2026-11-06 via `git for-each-ref --sort=-committerdate` (all tips
  ≤ 2026-11-06 except the new `key-pool-s1`), so base §A unique counts carry forward.
- Commands: `git status --short`, `git for-each-ref refs/heads`, `git worktree list`,
  `git rev-list --left-right --count origin/master...<branch>`, `git cherry origin/master <branch>`,
  `git log origin/w28-s1-delegate..HEAD --oneline`, `git -C <worktree> status --short | wc -l`
  (non-home paths only), `gh pr list/checks` (one `gh pr view` attempt failed: TLS handshake timeout;
  PR head hash below is from the RC plan, not re-verified live).

## A. Deltas since 2026-11-06

| Item | 2026-11-06 | 2026-11-11 | Evidence |
|---|---|---|---|
| Local branches | 41 | **42** (`key-pool-s1` new) | `for-each-ref` |
| Worktrees | 23 | **25** (`harness-wb-hy4`, `harness-wb-kp` new) | `git worktree list` |
| `w28-s1-delegate` vs origin/master | 0 behind / 45 ahead, no PR | 44 commits pushed via **PR #24 (OPEN)**; **1 unpushed**: `7c1acf4` "D1: a workspace-root scope covers every relative in-workspace path" | `git log origin/w28-s1-delegate..HEAD` |
| PR #24 CI | green at `19f5cae` (RC plan) | **15/15 checks pass**, run 42195189977 (e2e compose journey, hx checker, mcp, real-PG migrations, pytest sections 1/2/3/4/5/6/8/10/11, security scan, static gates) | `gh pr checks 24`, 2026-11-11 |
| Primary checkout dirty | 6 | **23** (list in §C below) | `git status --short` |
| `harness-wb-qwen` dirty | 0 | **35** | per-worktree status |
| `harness-wb-oc` dirty | 0 | **15** | per-worktree status |
| `harness-wb-hy4` | did not exist | **44 dirty** (detached at `7c1acf4`) | per-worktree status |
| `harness-wb-codex` / `wb-hy3` | 7 / 2 | 7 / 2 (unchanged) | per-worktree status |
| `master` | 5 behind origin/master, 0 ahead | unchanged; not checked out anywhere | `rev-list --count` |

**Active uncommitted fleet work exists in `wb-hy4` (44), `wb-qwen` (35), `wb-oc` (15), `wb-codex` (7),
`wb-hy3` (2), `harness-baseline` (19), `harness-a4` (6), `harness-s12` (4), `harness-a1` (3),
`harness-a2` (1), `harness-integration-test` (1).** Do not clean, reset, or remove any of these;
disposition is the owner's (F-02 is inventory-only).

## B. New branch: `key-pool-s1` — highest-priority review

| Field | Value |
|---|---|
| Tip | `4f37538`, 2026-11-06, "Starter operator rate card for native Anthropic models" |
| vs origin/master | 0 behind / **50 ahead**, **49 unique** (`git cherry`) |
| Upstream | **none — nothing pushed** |
| Worktree | `harness-wb-kp`, clean (0 dirty) |
| Suggested class | **unique-needs-human-review** — 49 unique commits exist only locally; loss risk if the worktree is ever cleaned. Owner: review, then push or record a disposition. |

Remote-only branches (no local checkout): `origin/claude/path-to-9-finish-ax6hie` (0 unique),
`origin/cloud/path-to-9-finish` (0), `origin/docs-w17-shipped` (0), `origin/imgbot` (1 unique — bot
image optimization). Suggested: merged-clutter candidates for the three 0-unique refs; imgbot keep
(bot-managed). Owner decides; nothing pruned.

## C. Untracked work in primary checkout (23 entries) — suggested dispositions

| Group | Entries | Suggested disposition (owner decides) |
|---|---|---|
| Binding planning docs | `FUTURE PLAN.md`, `docs/plans/` (base inventory, two-week RC plan, 90-day plan), `docs/decisions/2026-11-06-scale-tiers.md` | **Commit.** Note: FUTURE PLAN E-04 cites the scale-tiers decision as binding, but it is untracked — a binding decision should be in history. |
| Accelerator materials | `docs/yc/` (APPLICATION-PLAN.md, VIDEO-PLAN.md, prepared 2026-11-01), `docs/demo/techstars-local-demo.md`, `scripts/demo/techstars_fixture.py` | Owner lane (YC W27 / Techstars). Commit or keep private per owner; `docs/demo/yc-demo-runbook.md` is already tracked. |
| Fleet handbacks / batches | `day1-cal-hy4-handback.md`, `hy4-b8-handback-K1…K4 + SUMMARY`, `hy4-master-prompt-batch6/7/8`, `oc-pulse-alpha-batch7`, `qoder-batch9-2026-11-07`, `qwen-batch7/8`, `work-order-rc-bulk-1-2026-11-07` | Commit as the evidence record (project culture: handbacks are provenance), or archive per owner. |
| Evidence | `docs/briefs/evidence/rc/` | Commit with the RC record. |
| **New tests (untracked)** | `section5/tests/unit/test_shell_gate_real_boundary.py`, `scripts/ops/test_stack_tags_exit.py` | **Route to test agent** (week-01 handoff), then owner review + commit. The shell-gate test is A-01-adjacent. |

## D. Class changes since base §C (suggestions only)

- `key-pool-s1` → **unique-needs-human-review** (new, §B).
- `w28-s1-delegate` → **active** (PR #24 open, 1 unpushed commit, dirty primary checkout).
- Nov-6 "active by 48 h recency" rows now >48 h with unique=0 and no dirty checkout →
  **merged-clutter candidates**: `ci-nightly-greens`, `flaky-sqlite-busy-timeout` (unique=1 → keep
  unique-needs-human-review), `live-pg-probes`, `nightly-context-roundtrip`,
  `portable-container-proof-tests`, `w28-delegation-gate`, `w30-candidate-audit`,
  `w31-scheduler-experiment`, `w32-seam-analysis`. `master` stays the trunk — never clutter.
- `cline/84158`, `cline/b0895` (Nov-6 active by recency) → **UNDETERMINED** like their home-path
  siblings: unique=0, dirty count not run.
- All other rows carry forward base §C unchanged.

## E. Counts

- 42 local branches; unique>0: **12** (base 11 + `key-pool-s1` 49).
- 25 worktrees; 7 under `.cline/` (not entered), 18 under `untitled folder 2/`.
- Open PRs: **1** (#24, `w28-s1-delegate` → master, 15/15 checks green).
- Untracked primary entries: 23 (§C). Unpushed unique work: `w28-s1-delegate` 1 commit;
  `key-pool-s1` 49 commits, no upstream.

Acceptance (P-02): owner reviews this inventory with the base document and records a disposition per
branch/worktree group. No cleanup happens in this task.