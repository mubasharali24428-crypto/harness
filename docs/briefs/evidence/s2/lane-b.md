# Session 2 Lane B: sandbox isolation integrity (section5)

Date: 2026-10-18. Not a git repo, nothing committed. Rules and test map: `docs/sandbox-isolation.md`.

## Files

Changed: `section5/src/harness_sandbox/{policy,local_runner,diff_engine,confinement,service,api}.py`, `section5/openapi.json` (hand-edited: `CreateTaskRequest.resume`, `400`/`409` on `POST /tasks`).
Created: `section5/tests/unit/test_sandbox_isolation.py`, `docs/sandbox-isolation.md`, this file.

## Commands and results (cwd `section5`, `./.venv/bin/python`)

| Command | Result |
|---|---|
| `pytest -q` (before any change) | 182 passed, 1 skipped (the Linux-only landlock import test) |
| `pytest -q tests/unit/test_sandbox_isolation.py` after tests + exception stubs, before fixes | 38 failed, 10 passed (the 10 are controls and already-safe cases) |
| `pytest -q tests/unit/test_sandbox_isolation.py` after fixes | 48 passed |
| `pytest -q` (after) | 230 passed, 1 skipped |
| `ruff check src tests` | All checks passed |
| `section4/.venv/bin/python scripts/spec_drift.py` (check mode only) | "orchestrator specs and UI calls match the router." |

The macOS Seatbelt tests ran for real (`sandbox-exec`), including the two sibling/system-temp probes. Not run: any Linux Landlock path, docker compose, `run_smoke.py`.

## Status of each item

| Item | Fixed + tested | Tests |
|---|---|---|
| T15 (HIGH) takeover by taskId reuse | yes | `test_t15_other_team_cannot_take_over_task_id`, `test_t15_foreign_resume_flag_does_not_help`, `test_t15_same_team_reuse_needs_explicit_resume`, `test_t15_runner_level_refusal`, `test_t15_on_disk_owner_survives_runner_restart`, `test_t15_id_is_reusable_after_destroy` |
| T16 taskId traversal | yes | `test_t16_runner_rejects_bad_task_id[12 ids]`, `test_t16_destroy_with_traversal_handle_never_deletes_outside`, `test_t16_http_create_rejects_bad_task_id[6 ids]`, `test_t16_legitimate_ids_still_accepted[5 ids]` |
| T17 symlink bypass | yes (edit and exec-token paths) | `test_t17_*` (file link, relative link, intermediate dir link, escape, dangling, runner path, exec reads, controls) |
| T7b shared temp | yes on macOS Seatbelt (tested); Linux Landlock writes only (untested here); `off` mode advisory | `test_t7b_*` |

## Contract change

One optional request field `resume` (default false) on `POST /api/v1/sandbox/tasks`, plus `400 INVALID_TASK_ID` and `409 TASK_ID_IN_USE`. `scripts/spec_drift.py --write` only regenerates orchestrator specs, so I did NOT run it; `section5/openapi.json` was edited by hand. Lead action: `scripts/sdk-gen/specs/sandbox.openapi.json` (and generated `clients/ts`, `clients/python`) still lack `resume`; not my files. Callers grepped: only `scripts/e2e/compose_journey.py` (valid id, create once); section4/section7/section10 do not call the sandbox task API.

## Suggested status changes (lead applies)

- threat-model.md T15, T16, T17: mitigation found, `section5/tests/unit/test_sandbox_isolation.py` (names above), S5 closed.
- T7b: partially mitigated: Seatbelt verified (system temp unwritable, sibling contents unreadable/unwritable, private 0700 tmp). Remaining: Linux sibling reads, `off` mode, DockerSandboxRunner still empty.
- capability-matrix row 09: the note "Sandbox-runner gaps T15-T17 are in section5" can say they are closed in S2 with the test file above; keep the dev-grade caveat for the local runner.

## Residual risks

- Landlock sibling reads and the whole Landlock path are unverified on this host.
- `confinement=off` has no boundary; the compose image degrades to it when landlock is blocked.
- TOCTOU on symlink swaps between check and write; exec symlink check only covers literal tokens/globs.
- Pre-change task dirs without `.owner` are unresumable until removed; orphaned dirs after restart are not destroyed without `resume`.
- Resume of a live task ignores new `forbiddenPaths`/`branch` by design.
