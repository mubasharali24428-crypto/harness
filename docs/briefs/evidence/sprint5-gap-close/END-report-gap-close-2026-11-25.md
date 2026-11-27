# Sprint 5 gap-close — END report addendum (cloud Sonnet lane)

- Date (UTC): 2026-11-25. Branch `w28-s2-sprints1-4` (PR #27, **not merged**). Baseline `dee59679`.
- Extends `../sprint5/END-report-2026-11-24.md`. Covers Tasks 41–49 as far as the cloud Sonnet thread went. Cloud has no Docker: nothing here is a live PASS.
- Owner decisions applied: **D2 = soft-remove memberships**, **D3 = `userId` on roster items**.

## Correction to the earlier report
The 2026-11-24 report says GitHub Actions is "blocked by billing / no CI run". That is **no longer true**: CI runs on PR #27 (runs 298–301 on 2026-11-25, repo is public). Earlier runs failed because of the `changes` job bug fixed in Task 44, not billing. The Mac run remains local truth, but CI now exists.

## Task ledger (this lane)

| Task | Result | Commit | Evidence |
|---|---|---|---|
| 41 D3 `userId` contract + UI | **PASS** (cloud: pytest 39, vitest 1245, tsc, check-ts-dist, mirror check; live not run) | `48c2ccf` | `task-41-d3-userId.md` |
| 42 D2 soft-remove | **NOT DONE here** — Opus lane (separate thread) | — | — |
| 43 un-`test.fail` the B-09 live spec | **BLOCKED** — owner Mac Playwright, after 42 | — | — |
| 44 CI `changes` job | **PASS** — fixed shell-quoting bug in `resolve` step; job green on run 45613454005 | `bce3aa8` | `task-44-ci-changes-job.md` |
| 45a readiness-drift commands | **DONE** (docs only; manifest untouched) | `356d7fe` | `task-45-readiness-drift.md` |
| 45b manifest refresh after redeploy | **BLOCKED** — owner Mac, redeploy needed | — | `task-45-readiness-drift.md` |
| 46 this report + STATUS + ledger | this commit | (this commit) | — |
| 47 PR #27 review pack | done | (this commit) | `pr-27-review.md` |
| 48 checkpoint-1 (stretch) | **SKIPPED** — Opus-only, not started; gated on 41–44 + CI green and D2 | — | — |
| 49 cloud self-test | **PASS with env gaps** (see file; 1 sqlite-lane error unrelated to the change) | `92d2ca3` | `task-49-cloud-gate.md` |
| 50 Mac truth | **BLOCKED** — owner Mac, after 42 | — | — |

## CI state (PR #27, run 45613454005 at `356d7fe`)
All jobs green — `changes`, static gates, vitest+tsc, security scan, e2e compose journey, migrations on real PG, mcp, hx, and all nine pytest legs — **except** `readiness drift (manifest vs HEAD)`: manifest 0076 vs disk 0081, expected until the owner redeploys and rewrites the manifest.

## Still open
1. **D2 (Task 42)**: a member who has acted still cannot be removed on Postgres (audit FK RESTRICT). Product is **NOT READY** for any multiplayer pilot until it lands and the live spec passes on the Mac.
2. Task 43/50 live Playwright + `make gate` / `test-all` at the final SHA (owner Mac).
3. Task 45b: redeploy RC, `make readiness-manifest` **after** Task 42's migration lands.
4. Owner review of PR #27; not merged from cloud.

Verdict unchanged: **NOT READY**. No 10.11 line moved; P-D7 DEFERRED; payment and global P-D6 not enabled.
