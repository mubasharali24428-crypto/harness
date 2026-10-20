# Cloud pass report (Session 11, 2026-10-20)

Branch: `claude/path-to-9-finish-ax6hie` (the designated branch for this session; see "Deviations"). Nothing was pushed to `master` or to
the owner's `cloud/path-to-9-finish`; no pull request was opened; auto-merge was not touched. The owner was away; decisions that are theirs are
listed under "Owner action items" and the work carried on.

## Result in one paragraph

Matrix: **verified 34 → 44**, implemented 11 → 3, partial 14 → 12, missing 0, blocked 2 (10 rows flipped: 01, 02, 15 to 19, 34, 35 and 12). Part 56
and the other partial rows stay partial; 03, 37, 52, 53, 58 and 61 stay capped or blocked by outside parties. One real PostgreSQL-only bug was
found and fixed (export of a run with no sessions). One thing matters more than any row: **the pushed history does not import section 4 on a clean
checkout** (files that exist only on the owner's machine), so every section 4 number below comes from the owner's tree shape plus verification-only
stand-ins. A real-browser pass of the run page found and fixed six layout, keyboard and naming defects. The readiness decision in
`final-deliverables.md` stays **NO-GO** for broad rollout.

## Deviations from the brief (read first)

1. **The checkout did not match the brief.** It started at `master` (`9f85590`, the v1.0.0-candidate of 2026-08-26), not at `84703cb`. The
   expected history lives on `origin/cloud/path-to-9-finish` (82 commits ahead; tip `bd48c41`). The designated branch was fast-forwarded onto that
   tip (no history rewritten). The brief's new branch `cloud/path-to-9-finish` was not used: it already exists on the remote with the owner's commit.
2. **Tasks 1 and 3 had already landed** in `bd48c41`; they were re-verified, not redone.
3. **`docs/master-prompts/` is not in the repository.** Directive wording could not be re-read against the source (`process-parts.md`).
4. **Section 4 cannot be imported from the pushed history** (`clean-checkout-gaps.md`). Results below say which stand-ins were used.
5. **The section 1 run with `HARNESS_TEST_PG` set was stopped by the operator** at about 74% and is recorded as not run.

## Commits on the branch since `bd48c41`

`fa085cd` PostgreSQL empty IN-list fix and live-PG evidence · `8078c0e` process parts, final deliverables, state brief, doc tests ·
`3f2b19a` section 1 gate findings · `b4ef073` real-browser check, six fixes, row 12 to verified.

## What was verified (all run in this session unless marked)

| Check | Result |
|---|---|
| section 4, SQLite, pushed tree + `container_runtime` stub only | 16 failed, 1414 passed, 22 skipped. The 16 are the owner-only files: `confine.py` container mode (item 2) and the `boss` fixture (item 3): 15 tests; plus the crash case (item 4): 1 |
| section 4, SQLite, stub + two shims (`boss`, `wrap_command` kwargs) | 1 failed (`test_lifecycle_recovery`, item 4), 1429 passed, 22 skipped |
| section 4, all 7 live-PG modules, PostgreSQL 16.15 (own cluster, port 5544) | 18 passed |
| section 4, whole suite with PG enabled, stub only | 17 failed, 1409 passed, 4 skipped (the 16 above plus one timing flake in `test_limit_sweeper` that passed on re-runs; polling budget raised) |
| section 1, SQLite, stub installed, no `HARNESS_TEST_PG` | 1 failed (`test_slim_bus`, a stale fake: item 6), 941 passed, 29 skipped |
| `deploy/runners/migrate_selftest.py` | PASS, 55 migrations (0001 to 0056), idempotent re-run |
| `section4/tests/unit/test_program_docs.py` (22) | passed after the final doc edits (matrix, counts, links, evidence paths, brief length) |
| `test_collab_contract.py`, `test_threat_closeouts.py` | 50 passed, 2 failed (`KeyError: 'boss'`, the known fixture gap) |
| `spec_drift` | OK |
| `ruff` on the changed Python files | clean |
| section 7: `tsc --noEmit` | clean (after the last edit) |
| section 7: `npx vitest run` | 72 files, 831 tests passed (824 at the start of the pass) |
| section 7: `next build` + `next start`, Chromium 141 check | 0 hard failures (`browser-check.md`, mutation-checked) |
| `ci_route_module_guard.sh routes` | OK, 22 route modules |
| `ci_route_module_guard.sh isolation` | **2 violations remain, neither mine** (below). My own third one (`stateLabel.test.ts` read a section 4 file) was found and removed |

## What was fixed

* **section 4 `search.py`**: empty `IN` lists were typed integer; PostgreSQL rejected them, so exporting a run with no sessions failed
  (`EXPORT_FAILED`) on PostgreSQL only. New `test_search_in_list_typing.py` fails 4 of 6 on the old code. Two wrong assertions in
  `test_session6_live_pg.py`, a stale key set in `test_run_get.py`, and a tight polling budget in `test_limit_sweeper.py` were corrected
  (`live-pg-search-export.md`).
* **section 7 UI** (`browser-check.md`, `ui-plain-language.md`):
  1. plain-language errors (26 codes) and run states (every `AgentState`), parts 12 and 24;
  2. the shell stayed a flex row at 900 px and below, so the page was 1002 to 1014 px wide on a phone (now none);
  3. the run page's inline two-column grid beat the media query (now `.working-grid` with `minmax(0, 1fr)`);
  4. two code editors had no accessible name;
  5. the history log lost its list role and its scroller was not keyboard-reachable; "Jump to latest" now focuses the newest entry;
  6. the "Open shared run" link used the default blue; the terminal output box could not be focused.
* **Docs**: process parts 01, 02, 15 to 19, 34, 35 (`process-parts.md`, `final-deliverables.md`); `PROJECT-STATE.md` rewritten to 99 lines
  (history moved to `state-history.md`); `accessibility.md` limits corrected to what the browser run did and did not cover;
  `test_program_docs.py` pins links, brief length, 61 rows, evidence paths, external caps and counts.

## Remaining partial rows (Task 6): why each stays partial

| Row | Why it did not move |
|---|---|
| 31 Data and privacy | needs a product feature (account-wide deletion; Section 5 command allow-list in the SDK shell), not evidence |
| 49 Integrations | no issue tracker, chat provider or sandboxed extensions: absent features; calling them out of scope is the owner's decision |
| 51 Administration | physical residency is deployment-wide: an infrastructure decision |
| 54 Scalability | the only benchmark needs the Docker loadtest stack (`BASELINE.md`); a fresh run was not possible. WebSocket capacity and multi-replica are also unmeasured |
| 55 Observability | needs a Grafana import and an alert destination; no Grafana, `promtool` or `amtool` here |
| 56 Accessibility | browser keyboard, reflow and axe now run on two pages; still no screen reader, 7 contrast failures open, other pages not walked, English only |
| 57 Billing and quotas | in-flight spend is estimate-based and warning delivery is not wired; wiring it touches the reserved notifier |

## Not run, and why

* The pilot smoke (`deploy/pilot/run_smoke.py`), the compose stack, any image build (`deploy/backend.Dockerfile`): no Docker daemon in this box.
* The 4 section 1 tests that call `docker exec harness-db-1` (`test_fk_deletion_story.py` live class, `test_runs_index_tuning.py` live class).
* The full section 1 suite with `HARNESS_TEST_PG` set: started, stopped by the operator at about 74%, no result.
* A screen reader (VoiceOver, NVDA, JAWS, TalkBack), Lighthouse, zoom to 200 to 400%, touch input, any browser other than Chromium, any page other
  than `/login` and the run page.
* The Playwright suites `section7/e2e/journey.e2e.ts` and `multiplayer.e2e.ts` (they need the section 1 and 4 backends); `real.e2e.ts` is reserved.
* `pip-audit`, Linux Landlock, an independent penetration test, a real backup restore and point-in-time recovery.
* `test_lifecycle_recovery.py::test_worker_crash_blocks_honestly_and_stays_resumable` with a crash case added to the stand-in worker (reserved file).
* T13b (OTP redeem race in `auth.py`): document-only, as instructed.
* The benchmark in `docs/benchmarks/BASELINE.md`: not re-run (Docker). The numbers in it are from 2026-08-25.

## Still externally blocked

Part 03 (real pilot cohort and baseline), 37 (live identity provider), 52 and 58 (pen test, counsel), 53 (real restore), 61 (full acceptance).
Part 57 is no longer capped (no Stripe, by owner direction).

## Owner action items

1. **Make the pushed history importable.** Commit `container_runtime.py`, `model_relay.py`, `pilot_metrics.py`, the `confine.py` container mode and
   their tests together, or revert the `engine.py` wiring. Commit the `boss` entry in `tests/unit/test_collab_backend.py` `USERS` and the
   `standin_worker.py` crash case. Until then a clean checkout fails at `import harness_orchestrator` (`clean-checkout-gaps.md`, items 1 to 4).
2. **Fix the stale fake** in `section1/tests/test_slim_bus.py`: keep one callback per channel (item 6).
3. **Resolve the dangling citations**: matrix row 06 (`test_collab_denial_audit.py`), row 09 (`p0-container-isolation.md`), and the two files
   `docs/runbooks/pilot.md` links to (item 5).
4. **T13b decision**: apply the guarded `UPDATE ... WHERE consumed_at IS NULL` once `auth.py` is clean of your SMTP edits.
5. **Two isolation-law violations that are not mine** (`make guard-isolation` fails on them today and CI runs it):
   `section4/tests/unit/test_session6_live_pg.py:61` (reads `section1/migrations`) and `section7/app/notifications/notifications.test.tsx:105`
   (reads a section 1 source file). Move the shared fact into `contracts/` or drop the cross-read.
6. **Colour decision**: the secondary-text colour `#6b7280` is 3.3 to 3.9:1 on the dark panels (4.5 needed). Raising it changes the look of the whole
   product, so it was left. Also the 24 px target size on breadcrumb links, two selects and one checkbox.
7. **Decide whether `section7/scripts/browser-check.mjs` goes into CI** (it needs a built server and a Chromium; the usage is in its header).
8. **Provide**: a Docker host for the pilot smoke, the section 1 live-PG tests and the benchmark; a screen-reader pass; the outside evidence for
   parts 37, 52, 53, 58 and a real pilot cohort for part 03.

## Environment notes

Verification-only stand-ins (a `.pth` stub for `container_runtime` in the section 4 and section 1 venvs, a pytest shim plugin) live outside the
repository and are not committed. The two `build/` directories my installs left behind were deleted. The `next start` server on port 3111 and my own
PostgreSQL on port 5544 were stopped at the end. No Postgres on port 5432 was touched.

## Final status

Complete. Work is committed and pushed to `claude/path-to-9-finish-ax6hie` (tip is the commit that adds this report). No pull request was opened.
