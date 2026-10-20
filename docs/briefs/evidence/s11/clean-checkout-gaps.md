# What a clean checkout of the pushed history cannot do (found 2026-10-20)

**Resolved 2026-10-20 (local merge):** the owner's uncommitted files were committed after their suites passed (`d181456` section 4 isolation, relay and pilot metrics, `d7700b3` section 1 including the `test_slim_bus.py` fake, `95bb6b2` the master prompt). The list below is kept as found.

The cloud session started from `origin/cloud/path-to-9-finish` (commit `bd48c41`). Its working tree contains only pushed commits. Several
files the committed code needs exist only in the owner's local working tree. None of them is a file this session may create, so they are
**listed here for the owner; nothing below was fixed by guessing at their contents**.

## 1. Section 4 does not import (since commit `7fd8f88`, 2026-10-18)

`section4/src/harness_orchestrator/engine.py:39` does `from harness_orchestrator.container_runtime import ContainerBoundary`.
`container_runtime.py` is in no commit on any branch (`git log --all -- section4/src/harness_orchestrator/container_runtime.py` is empty).
`harness_orchestrator/__init__.py` imports `engine`, so **`import harness_orchestrator` raises `ModuleNotFoundError`** and
`pytest section4/tests` reports `102 errors during collection` (11 tests collected). An image built from the committed tree would be
expected to fail the same way (`deploy/backend.Dockerfile` was not built; no Docker daemon here). `model_relay.py` and `pilot_metrics.py` are also in no commit; `deploy/pilot/run_smoke.py:254` runs
`python -m harness_orchestrator.pilot_metrics`.

## 2. Committed `engine.py` and committed `confine.py` disagree (commit `a24124e`)

`engine.py` calls `confine.wrap_command(..., container_name=..., network=..., image=..., env=...)`. The committed `confine.py`
(last changed `295532b`) takes only `argv, *, mode, roots, workspace, session_dir, tmp_dir` and has no container mode. Observed directly:
`DeepSeek Harness run failed: wrap_command() got an unexpected keyword argument 'container_name'`. So **every isolated live engine run on
the committed tree fails**, which the worker-process tests show (below).

## 3. A shared test fixture edit is not committed

`tests/unit/test_collab_backend.py` `USERS` has no `"boss"` (admin on `team-a`). Committed tests use it:
`test_collab_contract.py` (2), `test_collab_rules_s2.py` (1), `test_control.py` (4, through `ALL_USERS = {**USERS, ...}`). All fail with
`KeyError: 'boss'`. Part 14's evidence row cites `test_collab_contract.py (46)`; on the committed tree 2 of those 46 fail.

## 4. The worker stand-in has no crash case

`tests/unit/test_lifecycle_recovery.py::test_worker_crash_blocks_honestly_and_stays_resumable` expects `"exited 7"` from the stand-in
worker. The committed `tests/unit/standin_worker.py` has no goal that exits non-zero, so the run completes and the assertion fails
(`'completed' == 'error'`). Rows 10 and 23 cite this file as crash/restart evidence. (Established by reading `standin_worker.py`; the
test was not run with a crash branch added.)

## 5. Citations to files that are not in the repository

| Where | Cites | State |
|---|---|---|
| capability matrix row 06 | `test_collab_denial_audit.py` | no such file anywhere in the tree |
| capability matrix row 09 | `docs/briefs/p0-container-isolation.md` | absent |
| `docs/runbooks/pilot.md` (links) | `../briefs/pilot-supervisor-checklist.md`, `../briefs/multiplayer-pilot-release-plan.md` | both absent |

Both matrix rows still cite at least one file that exists, so `test_program_docs.py` passes; the dangling names remain for the owner.

## 6. Section 1: a stale fake in the slim-bus test

`section1/tests/test_slim_bus.py::test_listener_wakes_and_fetches_envelope_table_side` fails every time (`TimeoutError`) on the committed
tree. `eventbus._listen` attaches two listeners on one connection: `_on_notify` on `agent_events` and `_on_collab_notify` on
`COLLAB_CHANNEL` (added in `cad3973`, 2026-10-18). The test's `_FakeNotifyConn.add_listener` keeps only one callback
(`self.callback = cb`), so the second registration replaces the first and the slim `agent_events` signal reaches the collab handler,
which ignores it (no `runId`). The test was last committed in `3805ca7` (2026-08-25), before the second listener existed. This is a
test-side fake that predates the code, not a delivery bug that was shown; the fake was not edited (reserved slim-bus test file) and the
test was not re-run with a corrected fake. **Owner action:** make the fake keep one callback per channel.

Three section 1 test modules import `harness_orchestrator` (`test_alert_rules_load.py`, `test_request_size_limits.py`,
`test_runs_index_tuning.py`), so the section 1 numbers below were also produced with the `container_runtime` stub installed in the
section 1 venv. Without it those modules fail to import for the item 1 reason.

## 7. Section 1 tests that need Docker

Four tests run only when `HARNESS_TEST_PG` is set and call `docker exec` into the compose database container (`harness-db-1`):
`test_fk_deletion_story.py::TestLivePostgresDeletionStory` (3) and `test_runs_index_tuning.py::TestLivePostgresRunsIndexPlan` (1). They
are skipped by default. They were **not run** here (no Docker daemon). A section 1 run with `HARNESS_TEST_PG` set was started and
stopped at about 74% by the operator, so it produced no result and is not counted anywhere below.

## How the results below were produced (verification-only stand-ins, not in the repo)

* `container_runtime`: a stub module registered through a `.pth` file inside the section4 venv's `site-packages` (gitignored). It raises if
  a container boundary is actually requested, so nothing silently uses it.
* "Shimmed" runs add a pytest plugin kept outside the repo that (a) adds `boss` to `test_collab_backend.USERS` and (b) lets
  `confine.wrap_command` accept and ignore the four extra keywords. These only separate "failure caused by the missing owner files" from
  "failure caused by something else".

| Run | Result |
|---|---|
| section 4, SQLite (gate run), committed tree + stub only | **16 failed**, 1414 passed, 22 skipped |
| section 4, SQLite (gate run), stub + both shims | **1 failed** (`test_lifecycle_recovery`, item 4), 1429 passed, 22 skipped |
| section 4, real PG 16.15 enabled, committed tree + stub only | **17 failed**, 1409 passed, 4 skipped: the 15 below, `test_lifecycle_recovery`, and one timing flake in `test_limit_sweeper` (passed on re-runs; budget raised) |
| section 4, real PG enabled, stub + both shims | only `test_lifecycle_recovery` still fails (item 4), plus 13 of my new `test_program_docs.py` tests that ran before their documents existed |
| section 1, SQLite (gate run), stub installed, no `HARNESS_TEST_PG` | **1 failed** (`test_slim_bus`, item 6), 941 passed, 29 skipped |
| section 1, `HARNESS_TEST_PG` set | not completed (stopped by the operator at about 74%); no result |

The 15 that pass once items 2 and 3 are shimmed: `test_collab_contract` 2, `test_collab_rules_s2` 1, `test_control` 4,
`test_isolation_and_staging` 3, `test_staging_e2e` 4, `test_workspace_lifecycle_s2` 1. Which of the two gaps each one needs was not
separated test by test. With the stub only, 1424 tests are collectable; the owner's tree runs about 135 more (the uncommitted tests).

## What the owner needs to do

1. Commit `container_runtime.py`, `model_relay.py`, `pilot_metrics.py`, the `confine.py` container mode and its tests, together, or
   revert the `engine.py` wiring. Until then the pushed history is not importable.
2. Commit the `test_collab_backend.py` fixture edit and the `standin_worker.py` crash case.
3. Commit or drop the two citations in item 5.
4. Fix the slim-bus test fake (item 6): keep one callback per channel.
