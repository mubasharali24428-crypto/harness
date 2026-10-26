# Sandbox isolation rules (section5)

Scope: `LocalSandboxRunner` and the `/api/v1/sandbox/tasks*` routes. Threat IDs refer to
`docs/briefs/threat-model.md`. All tests are in
`section5/tests/unit/test_sandbox_isolation.py` unless noted.

## What is enforced

| Rule | Enforced in | Proving tests |
|---|---|---|
| **T16** `taskId` must match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` (no `..`, separators, NUL, leading dot, spaces, empty, over-length). `fullmatch`, so a trailing newline fails too. | `policy.validate_task_id`, called from `LocalSandboxRunner._task_dir`, the only place a taskId becomes a path (used by `create_task` and `destroy`). Task dir must also be a direct, non-symlink child of the storage root. HTTP create maps it to `400 INVALID_TASK_ID`. | `test_t16_runner_rejects_bad_task_id[*]`, `test_t16_destroy_with_traversal_handle_never_deletes_outside`, `test_t16_http_create_rejects_bad_task_id[*]`, `test_t16_legitimate_ids_still_accepted[*]` |
| **T15** A taskId already in use (live, or present on disk) cannot be created again. Another team can never take it, with or without `resume`. The same team can only get it back with `resume: true`. | `LocalSandboxRunner.create_task`: live check, atomic `mkdir` claim of a fresh id, and an on-disk `<task>/.owner` record so ownership survives a runner restart. Refusal is `409 TASK_ID_IN_USE`, identical for "yours, no resume" and "someone else's" (no ownership oracle). An id on disk without an owner record is refused for everyone. `destroy` frees the id. | `test_t15_other_team_cannot_take_over_task_id`, `test_t15_foreign_resume_flag_does_not_help`, `test_t15_same_team_reuse_needs_explicit_resume`, `test_t15_runner_level_refusal`, `test_t15_on_disk_owner_survives_runner_restart`, `test_t15_id_is_reusable_after_destroy`; existing foreign exec/edit/destroy in `test_s1_r01_authz.py` |
| **T17 edit** An edit is checked against where it really lands: `resolve()` (every component, dangling links included) must stay inside the workspace, and the forbidden/secret checks are re-run on the resolved workspace-relative path. | `diff_engine.apply_unified_diff` (used by `LocalSandboxRunner.apply_edit` and so the HTTP route). | `test_t17_file_symlink_to_forbidden_target_is_blocked`, `..._relative_file_symlink_...`, `test_t17_intermediate_directory_symlink_is_blocked`, `test_t17_symlink_escaping_workspace_is_blocked`, `test_t17_dangling_symlink_outside_workspace_is_blocked`, `test_t17_runner_apply_edit_blocks_symlink_to_forbidden`, control `test_t17_in_workspace_symlink_to_allowed_file_still_edits` |
| **T17 exec** Command tokens naming a workspace symlink are also checked at their real target (final or intermediate link) against forbidden paths and secret-file patterns. Escaping links are NOT blocked here (a venv `python` link is legitimate); reads outside the workspace are a confinement question. | `policy._symlink_spellings`, used by `validate_command` (given `cwd`). | `test_t17_exec_read_through_symlink_to_forbidden_is_blocked[*]`, `test_t17_exec_read_through_symlink_to_secret_file_is_blocked`, control `test_t17_exec_ordinary_symlink_still_allowed` |
| **T7b** Each task has a private temp dir `<storage>/<task>/tmp` (mode 0700; the task dir is 0700 too; storage root created 0700). `TMPDIR`/`TMP`/`TEMP` point at it. Seatbelt: writes allowed only to the task workspace, its tmp and `/dev/null` (system temp is no longer writable), and file contents under the storage root are unreadable except this task's own dir. Linux Landlock: writes only to workspace/private tmp, and reads/exec are granted through an allow-list so forbidden paths and sibling task workspaces are unreadable. Removed with the task on `destroy`. | `local_runner.create_task/_spawn/destroy`, `confinement.seatbelt_profile`, `confinement.landlock_read_allowlist`, `confinement.apply_landlock_in_child(tmpdir=...)`. | `test_t7b_per_task_private_tmp_dir_is_0700_and_distinct`, `test_t7b_exec_gets_private_tmpdir_env`, `test_t7b_tmp_dir_removed_on_destroy`, `test_t7b_seatbelt_profile_has_no_system_temp_write_or_sibling_read`, `test_landlock_read_allowlist.py`, `test_landlock_read_isolation.py`, and on macOS with the real `sandbox-exec`: `test_t7b_seatbelt_sibling_workspace_is_neither_writable_nor_readable`, `test_t7b_seatbelt_shared_system_temp_is_not_writable_but_private_tmp_is` |

## API surface change (backward compatible for callers that never reuse a taskId)

- `POST /api/v1/sandbox/tasks` takes an optional `resume: bool = false`.
- New error responses: `400 INVALID_TASK_ID`, `409 TASK_ID_IN_USE` (same `{"error": {code, message}}` envelope).
- A resume of a live task returns the existing handle and changes nothing (new `forbiddenPaths`/`branch` are ignored, so a resume can never loosen policy).
- Known callers: `scripts/e2e/compose_journey.py` (id `e2e-<epoch>`, creates once) and the generated SDKs. No in-repo caller reuses an id.

## Known limits

- **Docker Desktop / macOS bind mounts cannot enforce the engine shell gate's
  Landlock writes.** Inside a per-task container the shell gate re-applies
  Landlock, but a Docker Desktop virtiofs bind mount rejects the write grant:
  the file is created and the content write fails with `EACCES`. Reproduced with
  a minimal Landlock write ruleset, so it is the mount, not the gate logic.
  Container-isolated tool runs are therefore only expected to work on a native
  Linux host (ext4/overlayfs); Docker Desktop container tool writes are not a
  supported deployment. `scripts/native_linux_drill.sh` (CI job `native linux
  container tool drill`, ubuntu-latest) reproduces this path on a native kernel:
  a shell-gated write must land on a bind-mounted workspace and a sibling
  directory must stay unreadable. The `HARNESS_ENGINE_CONFINEMENT=off`
  diagnostic opt-in makes the worker write normally but removes the OS
  boundary.
- **Read allow-list bootstrap (fixed 2026-10-25).** `confine_argv` used to add
  the target program's grandparent to the readable roots whenever its directory
  was named `bin`. Since the engine shell gate always runs `/bin/sh`, that
  granted `/` as readable and silently defeated the read allow-list for every
  shell-gated command. `exec_read_roots` now adds a directory only when the
  program lives outside the system roots and HOME; a regression test proves
  `/bin/sh` adds nothing. The same drill found a second hole: a container
  started with `--user` and no passwd entry has `HOME=/`, which the allow-list
  accepted as a readable root. `/` is now refused as a root. The native-Linux
  drill is what surfaced both.
- **Linux/Landlock** is now a read/write boundary when the runtime probe passes. Landlock grants handled rights rather than expressing deny rules, so the read side is an allow-list assembled from the task workspace, private tmp, HOME, required system trees, and the target tool's directory; forbidden paths are omitted and sibling task workspaces are outside the allow-list. The rules are installed before `exec`, and a probe failure still fails closed. This is stronger than the prior write-only posture but remains host/kernel dependent and is not a production-readiness claim.
- **Engine shell gate (part 52, B1).** The DeepSeek runtime's own bash tool is disabled per run and replaced by `section4/src/harness_orchestrator/dsh_plugins/harness-shell.js`, which sends each command to `section5/src/harness_sandbox/shell_gate.py`: `validate_command` (allow-list, no shell operators, secret files, plan `forbiddenPaths`), the run intent's `scopePaths`, then the command under `confine_argv` with a sanitized environment. No confinement available = refused. The plugin also denies tool calls past the run's remaining `maxToolCalls`. Same argument-level limits as the exec path above. Each call starts a fresh `/bin/sh`, so no shell state carries over between calls.
- **`confinement=off`** has no OS boundary: the private tmp is a convention (env vars) and 0700 mode, not a barrier against code that ignores `TMPDIR`. It requires the explicit `HARNESS_SANDBOX_ALLOW_UNCONFINED=1` opt-in. The Compose journey may use that diagnostic opt-out, but it is not sandbox security evidence.
- The exec symlink check is a policy-layer filter on literal tokens and globs; shell indirection is already rejected by `find_unquoted_shell_syntax`, but a program that builds a path at runtime (e.g. `python -c`) is only stopped by OS confinement. Symlink checks have a time-of-check/time-of-use window (a link swapped between check and write).
- An id on disk with no `.owner` record (data from before this change) cannot be resumed by anyone; remove it manually. `destroy` only removes live tasks, so after a runner restart an orphaned dir needs `resume: true` first.
- `DockerSandboxRunner` is implemented as an explicit per-task container boundary. It uses a read-only root filesystem, private workspace/tmp mounts, dropped capabilities, `no-new-privileges`, CPU/memory/PID limits, explicit network mode, a sanitized environment, and forbidden-path mounts. Its default network mode is `none`; `HARNESS_SANDBOX_ALLOW_NETWORK=1` is an explicit opt-in that attaches the container to the gateway-less `harness_sandbox_egress` network, whose only peer is the `sandbox-egress` allow-list CONNECT proxy (`egress_proxy.py`: HTTPS port 443 to pinned registries and GitHub/GitLab only; not yet exercised against a live Docker daemon, and it fails closed if `SANDBOX_EGRESS_NETWORK`/`SANDBOX_EGRESS_PROXY_URL` are unset). It fails closed if Docker or the configured pre-pulled image is unavailable; `SANDBOX_USE_DOCKER=true` selects it. The Compose sandbox service remains a shared backend container and does not enable this mode by default. Landlock on Linux and Seatbelt on macOS remain pilot-grade host boundaries with the limitations above.

- **Network policy is deliberately fail-closed by default.** Compose supplies
  `HARNESS_SANDBOX_ALLOW_NETWORK=0`, and the per-task Docker runner defaults to
  `network_mode=none`. A future production deployment must route approved
  traffic through a reviewed relay/proxy or equivalent host policy; setting the
  opt-in flag alone does not provide that control.

## Production deployment gate — Step 1

The approved deployment shape is a dedicated sandbox worker with a separately
managed, rootless or otherwise isolated Docker daemon and a reviewed,
pre-pulled worker image. The shared backend must not receive the host Docker
socket: that socket is effectively host-level control and would erase the
security boundary this runner is intended to provide.

The current Compose `sandbox` service has no Docker socket mount, so it remains
on the Landlock/Seatbelt path by default. The local Docker Desktop daemon
reports only the built-in seccomp profile and cgroup namespace; rootless
operation is not verified here. This step is therefore architecture-complete
but not production-certified. Acceptance requires a dedicated worker/daemon,
explicit image provenance, verified non-root execution, no shared backend
socket, and a Linux host test before `SANDBOX_USE_DOCKER=true` is enabled for
a pilot.

## Engine worker daemon (M07, 2026-10-26)

The engine's confined worker (`HARNESS_ENGINE_CONFINEMENT=container`) now has
that dedicated holder. The `worker-daemon` service (compose profile `worker`) is
the only service with the Docker socket. The orchestrator posts it a run id, the
goal, the run's policy and its `DSH_*` settings. The daemon builds the boundary
from its own configuration: the run directories under its `DSH_WORKSPACE`, the
image in `HARNESS_ENGINE_IMAGE`, mode `container`, and `confine`'s limits. Each
worker runs as the workspace's owner and never as root. Its run directories
reach it as subpaths of the `harness_runs` volume. Calls need a bearer token,
and the daemon has no published port. Without the daemon, a live run fails
closed.

What this is not: the daemon uses the host's main Docker engine, so whoever
controls it controls the host. It narrows *who* holds that power (one small,
reviewed service instead of the API), but it is not the separately managed
rootless daemon this gate ultimately asks for.
