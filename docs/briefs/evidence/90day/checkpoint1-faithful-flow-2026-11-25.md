# Checkpoint-1 faithful flow — evidence (2026-11-25)

Implements `checkpoint1-redesign-2026-11-21.md`. `integration/test_checkpoint1.py`
no longer carries the honest skip: the full flow now runs green in the default
sqlite run by satisfying the three product laws instead of patching around them.

- Branch: `claude/project-thread-hgcjtr`, cut from `66847e9` (Sprint 5 Task 32 on
  `w28-s2-sprints1-4`, PR 27). Commit: `de6b58e`.
- Where it ran: cloud container, Linux, Python 3.13.18, sqlite (aiosqlite). No
  Docker, no Postgres, no live model. The PG-only fanout test stays skipped, as before.

## What the flow does now (every step is a production path)

1. `propose_plan(project_id=...)` → `approve_plan` → `start_execution` enqueues on
   the migrated `run_claims` queue → `poll_once()` claims and spawns (the worker path).
2. The isolated engine (`isolated=True`, the production default) takes the
   baseline; the injected runtime rewrites `calc.py` (a substantial modify) and
   runs the scratch repo's pytest **for real in the S5 sandbox**, reporting the
   call as runtime session events (`tool/call` + `tool/result` with the real
   `[exit code: N]`).
3. `_execute` records those calls through `record_engine_tool_calls` (the live
   writer) and staging **withholds** the substantial edit; the workspace keeps the
   seed bytes and a pending `system:staging` approval is filed.
4. Over the real FastAPI routes (httpx ASGI, no auth bypass beyond the principal
   override the collab suite uses): `POST /leader`, `GET /approvals`,
   `POST /approvals/{id}/decision` (with `viewed_detail`), `POST /approvals/{id}/apply`,
   `POST /done-signal`.
5. The service publishes through the S1 `EventBus`; the durable replay equals the
   emitted lifecycle, in order, with the frozen envelope.

## The laws, as assertions

| Law | Positive | Refusal asserted |
|---|---|---|
| 1. Staged-approval gate | done opens only after approve **and** apply; the approved bytes land | `409 STAGED_APPROVAL_PENDING` before approval, and again after approval but before apply |
| 2. I-09 verification-first (default on; `HARNESS_VERIFICATION_FIRST` unset) | the run's own pytest, exit 0, recorded as `engine bash call (exit 0)` by `reported_by=engine` | no recorded test → `400 VERIFICATION_REQUIRED` even after approve+apply; a real red run → same 400 |
| 3. Reviewer integrity | reviewer passes, and calls models through S3 | `signal_done` called past the route gate: no evidence → fails ("No green test evidence…"); red run → fails ("Automated test suite failed.") |

Mutation check (run locally, reverted, not committed): making
`_gate_staged_completion` a no-op fails `test_checkpoint1_full_flow` at refusal 1;
defaulting `HARNESS_VERIFICATION_FIRST` to false fails all three flow tests.

## What changed

- `section4/src/harness_orchestrator/engine.py` (production, ~12 lines): an injected
  `backend_runner` may return `{"finalResponse", "events"}`; the events fold through
  the same `_fold_tool_calls` a live SDK run uses. A plain string keeps the old shape
  (no `toolCalls`). No gate, policy or reviewer code changed.
- `integration/test_checkpoint1.py`: skip removed; the hand-written `run_claims` /
  `agent_tool_calls` DDL and the hand-inserted tool-call row are gone. The flow fixture
  applies the real migration files 0006, 0007, 0035, 0043, 0044, 0049 over the S1 ORM
  schema through a two-token sqlite shim (`now()` → `CURRENT_TIMESTAMP`,
  `BIGSERIAL PRIMARY KEY` → `INTEGER PRIMARY KEY AUTOINCREMENT`). Two new negative
  tests (no recorded tests; real red tests).
- `section4/tests/unit/test_injected_runtime_tool_calls.py`: 6 focused unit tests
  (mapping folds like a live run; string shape unchanged; no events = no evidence;
  red recorded red; refusal never green; red-then-green is green).

## Deliberate choices, stated

- **Sandbox confinement.** The integration `sandbox` fixture uses
  `confinement="off"` with the explicit `HARNESS_SANDBOX_ALLOW_UNCONFINED=1` opt-in,
  the same convention as `section5/tests/conftest.py`. This host has no usable
  Landlock boundary, so `auto` refuses to run (correctly). Confinement itself stays
  covered by S5's own suite (`test_landlock_read_isolation`,
  `test_phase4_confinement_gate`); nothing in S5 was changed.
- **Evidence timing.** The design sketch lists "record a green test" after apply.
  Here the evidence is the engine run's own test run (recorded before staging
  withholds the edit), because `agent_tool_calls` rows with `reported_by=engine`
  only come from the engine; a test-harness run recorded as "engine" would be
  synthetic. Apply is digest-bound to exactly the bytes the agent tested, and the
  test also re-runs pytest on the applied tree in S5 (asserted green, not recorded).
- **No `pr_opened`.** The old happy path called `sm.open_pr` after review. A review
  that follows the engine-opened done-gate now seals `DONE` (terminal), so opening a
  PR is a separate gated step and is out of this checkpoint's scope.

## Commands and output

Setup (once, from the repo root):

```sh
cd section1 && python3 -m venv .venv && ./.venv/bin/pip install -q ../contracts -e ".[dev]"
cd ../section4 && python3 -m venv .venv && ./.venv/bin/pip install -q ../contracts -e ".[dev]"
```

Integration checkpoint, default sqlite run (the same command `make pg-probe-integration`
runs, without the PG env):

```text
$ cd integration && ../section1/.venv/bin/python -m pytest test_checkpoint1.py -v -rs -p no:warnings
test_checkpoint1.py::test_protocols_conform_to_contracts PASSED          [ 16%]
test_checkpoint1.py::test_checkpoint1_full_flow PASSED                   [ 33%]
test_checkpoint1.py::test_checkpoint1_refuses_done_without_recorded_tests PASSED [ 50%]
test_checkpoint1.py::test_checkpoint1_red_tests_never_pass PASSED        [ 66%]
test_checkpoint1.py::test_orchestrator_rehydrates_state_from_event_log PASSED [ 83%]
test_checkpoint1.py::test_cross_instance_live_fanout_via_pg_notify SKIPPED [100%]

=========================== short test summary info ============================
SKIPPED [1] test_checkpoint1.py:488: requires Postgres (set HARNESS_TEST_PG=1)
========================= 5 passed, 1 skipped in 2.84s =========================
```

Stability: the same command 5 times in a row → `5 passed, 1 skipped` each time
(2.59–2.84 s).

Before this change, the same command printed `2 passed, 2 skipped` with the
checkpoint-1 skip reason.

Unit tests and lint:

```text
$ cd section4 && ./.venv/bin/python -m pytest tests/unit/test_injected_runtime_tool_calls.py \
    tests/unit/test_engine.py tests/unit/test_review_summary.py tests/unit/test_engine_shell_policy.py -q -p no:warnings
92 passed, 1 skipped in 2.04s
$ cd section4 && ./.venv/bin/ruff check src tests
All checks passed!
$ section1/.venv/bin/ruff check integration/test_checkpoint1.py
All checks passed!
```

Full section 4 suite (`make test-section4`):

```text
$ make --no-print-directory test-section4
FAILED tests/unit/test_forbidden_path_mounts.py::test_real_container_readonly_bind_blocks_bypass
FAILED tests/unit/test_isolation_and_staging.py::test_hung_worker_is_killed_at_the_operator_bound
FAILED tests/unit/test_scope_mounts.py::test_real_container_scope_mounts_refuse_the_m01_bypass
FAILED tests/unit/test_worker_daemon.py::test_a_root_launcher_runs_the_worker_as_the_workspace_owner
4 failed, 2457 passed, 63 skipped, 26673 warnings in 182.53s (0:03:02)
```

The 4 failures are this host, not this change: they fail identically with
`engine.py` reverted to `66847e9` (`4 failed in 1.56s`). Two need a Docker daemon
(the `docker` CLI exists here but `/var/run/docker.sock` does not), and the other
two depend on process-group and uid behaviour of this root-run container. They are
not marked PASS anywhere; they need the owner's Mac or CI to judge.

## Not done here

- No live run, no Docker, no Postgres: nothing in this file is a live PASS. The
  nightly `make pg-probe-integration` (GitHub, real PG service) will exercise the
  same file; it has not run on this branch yet.
- The week-04 ledger §7 entry is not edited here; this file is its closure evidence.
