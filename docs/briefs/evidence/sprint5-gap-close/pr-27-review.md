# PR #27 review pack (cloud, 2026-11-25)

PR #27 `w28-s2-sprints1-4` → master. **Do not merge from cloud**; owner decides. Verdict stays **NOT READY**. Tip when written: `92d2ca3` (+ the Task 46/47 docs commit). GitHub reports ~105 commits, ~550 files, +21.8k/−1.4k lines against master (git three-dot, as of this writing); mostly docs/evidence, tests and section1/7/8 changes.

## 1. Scope summary
| Slice | What it contains | Evidence |
|---|---|---|
| Sprints 1–2 | Phase 7 admin-reviewed assignment planning (API + pages); Plain / Decision-card approval renderings (Chromium, mocked API); blind-approval KPI; X-08 classification (display only, P-D6 off); case memory on denial stops; W31 tier proposals (flag-off); observer-only external adapters; X-14 → DEFER | `sprint2/END-report-2026-11-24.md` |
| Sprint 3 | `migrate_selftest` 79 migrations on real PG; self-service rendering setting; similar denials on the approval card; flag-gated W31 panel; 15 unsigned §17 DRAFTs | `sprint3/END-report-2026-11-24.md` |
| Sprint 4 | Compose stack live, migrations 0076–0081, compose journey 13/13, live approval e2e, `make gate` green, C-05/C-07 | `sprint4/END-report-2026-11-24.md` |
| Sprint 5 (Tasks 0, 31–40) | Collab `viewed_detail` + audit fields + blind KPI; denial-learning read-only offer; live KPI/planning/similarCases PASS on Mac; **Task 36 live invite→revoke FAIL** (D2, D3; D1 fixed); Mac `test-all` + `make gate` green at `a55ba75f` | `sprint5/END-report-2026-11-24.md`, `task-36-…`, `mac-gate-a55ba75f.md` |
| Gap-close (cloud Sonnet) | Task 41 D3 `userId` on roster + UI; Task 44 CI `changes` job fix; Task 45a readiness commands; Task 49 cloud self-test; reports | `sprint5-gap-close/*` |
| Gap-close (not yet) | Task 42 D2 soft-remove (Opus), 43, 45b, 50 (Mac) | see below |

## 2. Owner gate checklist (what is still BLOCKED)
- [ ] **D2 / Task 42** — soft-remove memberships; until built, removing a member who has acted 500s on Postgres. *(Opus thread)*
- [ ] **Task 43** — `test.fail` removed from `section7/e2e/invite-revoke-live.e2e.ts` (cloud edit); Mac journey passed after 0082 (DELETE 204, tokens dead) but **Playwright PASS awaits the owner's Mac re-run without the marker**, `--retries=0`.
- [ ] **Task 45b** — redeploy RC, `make readiness-manifest`, commit manifest; CI job `readiness drift` turns green. Do this **after** Task 42 (new migration). Commands: `task-45-readiness-drift.md`.
- [ ] **Task 50** — `make gate` and `HARNESS_TEST_PG=1 TEST_ALL_DOCKER=1 make test-all` at the final SHA; append to `mac-gate-<sha>.md`.
- [ ] Owner-only, untouched: 10.11 gate lines, P-D2 rail, P-D7 (DEFERRED), SOW signatures, F-SEC-05 redeploy, P-13 benchmark budget, G1/G2 acceptance. Payment and global P-D6 stay off.

## 3. CI state (run 45613454005 at `356d7fe`)
Green: `changes`, static gates, vitest+tsc, security scan, e2e compose journey, migrations on real PG, mcp, hx, pytest legs 1–6, 8, 10, 11 (sections 2–6, 8, 10, 11 and 1). Red: **`readiness drift (manifest vs HEAD)`** — 0076 vs 0081, expected. Earlier runs on this branch all failed at the `changes` job (quoting bug, fixed in `bce3aa8`), so this is the first run where the matrix legs actually executed on PR #27.

## 4. Merge risks
1. **Red required check?** `readiness drift` stays red until 45b; if it is a required check, merge is blocked or needs an owner override. Check branch protection before planning the merge.
2. **D2 absent**: merging without Task 42 ships a roster Remove button that works for the id (D3) but still 500s for any member with audit history. Fine for a non-pilot branch, wrong for a multiplayer pilot.
3. **Open e2e**: the B-09 live spec is `test.fail` (expected failure); the first Mac run after D2 must flip it, or the marker will hide a regression.
4. **Migration vs manifest ordering**: Task 42 adds a migration; redeploying before it lands means a second manifest rewrite.
5. **Size**: ~550 files; review by slice (table above), not by diff. Generated files (specs, `clients/ts/dist`, Python SDK) change together on API edits — `make check-ts-dist` guards them.
6. **Cloud evidence is not live evidence**: cloud had no Docker; one sqlite-lane error (`test_runs_index_tuning`, "too many SQL variables") was seen in the cloud section1 run and not re-run on the pre-change baseline.

## 5. Suggested merge order
1. Task 42 (D2) from the Opus thread onto the PR branch; rebase/merge, keep one commit per task.
2. Owner Mac: Task 43 (un-`test.fail`) → Task 50 (`make gate`, `test-all`).
3. Owner Mac: RC redeploy → Task 45b (manifest) so `readiness drift` is green.
4. Confirm every required check green on the final SHA.
5. Owner review of PR #27 and merge (not from cloud).
