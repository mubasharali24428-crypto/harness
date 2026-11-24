# Harness Sprint 2 report — 2026-11-24

Branch `w28-s1-delegate`, local only (**not pushed**). Start `fae78d70` (owner's X-21 plan row,
committed separately in pre-flight); end = the Task 20 commit. Docker daemon down all day.

## Gate at `2c8abe1d` (`make gate` + the stages it never reached after the first red)

| Stage | Result |
|---|---|
| static-gates (route law, isolation, compose config, TS mirror, spec drift, eval check, env knobs) | green |
| lint (sections 1–6, 8, 10, 11), lint-mcp, lint-language | green |
| section1 / 2 / 3 | 1299 (62 skipped) / 61 / 149 passed |
| section4 | **2436 passed, 2 failed**: `test_forbidden_path_mounts::test_real_container_readonly_bind_blocks_bypass`, `test_scope_mounts::test_real_container_scope_mounts_refuse_the_m01_bypass`; both run `docker run` and the daemon is down (same 2 at Sprint 1 baseline) |
| section5 / 6 / 8 / 10 / 11 | 553 / 123 / 108 / 96 / 58 passed |
| test-mcp / test-language | 18 / 367 passed |
| frontend (tsc + vitest) | green, 1221 tests |
| check-ts-dist | dist matches a fresh build |

`make gate` itself exits non-zero because of the two Docker rows; it halts at the first red, so
the later stages were run one by one with the same make targets.

## All tasks (Sprints 1 + 2)

| Task | Commit | State |
|---|---|---|
| 1 Owner register defaults + Day-90 ledger | `c9d40d74` | done; owner rows BLOCKED on signature |
| 2 Collab no-oracle test; B-09 ledgered | `978698e3` | done |
| 3 Phase 7 assignment-plan API (0078) | `89e1da93` | done |
| 4+5 Plain sentence + severity grid | `5d72398e` | done (one commit, shared code) |
| 6 Simplicial store prototype | `eeba36d1` | done (experiment) |
| 7 W31 tier/TTL CP-SAT | `cf044cca` | done (experiment) |
| 8 `python -c` pre-exec parse | `f8b5188c` | done; Hermes out of scope by ADR |
| 9 Case-base memory (pure) | `24af8774` | done |
| 10 External adapter v1 (0079) | `fdf6d787` | done |
| 11 X-07/X-21 browser verification | `dcadc02c` | **Verified at `dcadc02c`** (Chromium, mocked API); fixed 375 px clipping |
| 12 Phase 7 planning pages | `0a5f7852` | done; browser check (mocked API) caught + fixed a hydration error |
| 13 Gate 6 / B-09 evidence pack | `be486857` | done; build **BLOCKED** (P-D7 deferred, no stack) |
| 14 Denial cases persisted + recalled (0080) | `17c742fd` | done |
| 15 W31 proposals on runs (0081, flag-off) | `a0600cb8` | done; usage stats caller-supplied (no telemetry exists) |
| 16 X-14 dry benchmark | `39050091` | done; verdict **DEFER** |
| 17 X-08 envelope classification | `47f3c8ad` | done; display only, P-D6 off; batch review not built |
| 18 Blind-approval KPI | `eccc4b04` | done; no pilot data yet |
| 19 Adapters UI + SDK regen | `2c8abe1d` | done |
| 20 Closure (this report, STATUS, ledger delta) | Task 20 commit | done |

## BLOCKED (owner / environment)
- Owner: SOWs, revenue, P-D2 rail, F-SEC-05 redeploy, paid benchmark budget (X-D2), P-D7 and Gate 6
  sign-off, P-D6 envelopes, signatures on every DEFAULT register row.
- Environment: live-stack e2e (multiplayer.e2e, approval round-trip), two section4 Docker tests.
- Migrations 0078–0081 are committed but applied to **no** live database.

## FUTURE PLAN gates touched
Phase 7: C-01..C-04 usable end to end in section7 (not C-05..C-08) · Gate 6: evidence pack only ·
X-07: browser-verified UI half + KPI wired · X-08: classification only · X-14: DEFER ·
W31/C-08: proposal store, flag-off · 10.11: **no line moved**.

## Uncommitted files (not Sprint work; exclude from any push)
32 modified `docs/briefs/evidence/ui/r2-*.png`, untracked `docs/briefs/evidence/eval/code-search-default-*.json`,
`docs/briefs/evidence/quality/ci-local/`, `.openclaw/`.

## Push recommendation
**Hold until the owner says push.** The branch is code-ready by the gate above, but it is now
~55 commits ahead of origin and carries four unapplied migrations; push only by explicit request.
