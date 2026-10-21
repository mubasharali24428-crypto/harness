# Part 52: shell gate in the worker image (session 12, B1 2026-10-21)

**Update 2026-10-21 (B1): unblocked without a vendor build.** The runtime's bash tool is replaced by a
local plugin that routes every command through Section 5's gate. It runs in the hardened worker
container; see "B1: Section 5 shell gate" at the end. The rest of this file is the original
session 12 record.

**Original result: blocked.** The worker image cannot run the agent's bash tool on this host (Docker Desktop
linux-arm64, kernel 6.12.76-linuxkit), under the pinned sandbox mode or under any mode tried. So the
runtime's framing bytes could not be captured live, and no allowed or denied command ran in a real
container. The DEBUG-trap gate was not written, so no weaker gate ships. `section4/worker.Dockerfile`
is unchanged. Part 52 stays `partial` (external cap 8): this work claims nothing for the external
penetration test.

## What was run (the real runtime path, a scripted model)

- Image `harness-engine-worker:s12`: `section4/worker.Dockerfile`, target `worker`, built from this branch.
- Driver `probe/probe_run.py`: `DeepSeekHarness(profile="sdk-minimal", base_url=<scripted model>)`,
  the same call `engine._run_live_events` makes.
- Scripted model `probe/mock_model.py`: answers the runtime's `/v1/messages` (Anthropic format) with
  eight `bash` tool calls, then final text. It was verified to drive the runtime through a full tool loop.
- Shell logging `probe/Dockerfile` + `probe/rc.sh`: `/usr/bin/bash` is replaced by a wrapper that execs
  the real bash with `--rcfile rc.sh`; a DEBUG trap logs every `BASH_COMMAND` (`printf %q`) to
  `/probe-out/bash_command.log`.

## Results

| # | Configuration | Result |
|---|---|---|
| 1 | `workspace-write` (pinned by section4), container hardened: `--read-only`, `--cap-drop=ALL`, `no-new-privileges`, uid 10001 | Every bash call returns `Error: sandbox mode "workspace-write" is requested but no sandbox backend is usable on this host; refusing to run the command unconfined.` The DEBUG log stays empty. |
| 2 | `workspace-write`, variants: no `--read-only`; root user; no `no-new-privileges` | The same error in every variant. The flags are not the cause. |
| 3 | `workspace-write` with bubblewrap installed (diagnostic image) | `bwrap --ro-bind / /` fails: `No permissions to create new namespace`. Default caps and Docker's default seccomp block unprivileged user namespaces (`unshare(CLONE_NEWUSER)` = EPERM). |
| 4 | `danger-full-access`, probe only (NOT shipped) | The bash tool fails: `Failed to load native module: pty.node ... Cannot find module './prebuilds/linux-arm64/pty.node'`. The shipped `deepseek_harness_runtime_bin 0.2.1rc2` has no `pty.node` for linux-arm64, so the persistent PTY shell never starts. |

Landlock: `landlock_create_ruleset` answers ABI 6 inside the container, so the kernel supports it.
The runtime still reports no usable backend, and why is not determined. The runtime binary is closed,
and an attempt to read it was denied by the auto-mode classifier in this session; it was not retried.

## What this means for the brief

- Framing: the brief's framing bytes (START printf, eval wrapper, status printf, `stty -echo`) are
  still unconfirmed live. They were not captured here, so the DEBUG-trap exemption cannot be tested.
- Acceptance (a denied command is refused and logged; allowed commands still run) is not met, and
  nothing in the worker image changed.

## linux/amd64 rerun (2026-10-20, owner chose "probe amd64 first")

The worker image was rebuilt with `--platform linux/amd64` (Docker Desktop emulation;
`deepseek_harness_runtime_bin-0.2.1rc2-...-manylinux_2_28_x86_64.whl`) and the same probe was run,
hardened as in row 1, with eight scripted `bash` calls (an echo, `id -u`, a workspace write, a write
to `/etc`, `git --version`, a `curl`, an `nc`, `pwd`).

| # | Configuration | Result |
|---|---|---|
| 5 | amd64, `workspace-write`, hardened | All eight calls: the same `no sandbox backend is usable on this host` refusal. DEBUG log empty. |
| 6 | amd64, `danger-full-access`, probe only (NOT shipped) | All eight calls: `Cannot find module './prebuilds/linux-x64/pty.node'`. The x86_64 runtime build has no PTY module either. |

So the shipped runtime cannot run the agent's bash tool inside the worker container on either Linux
architecture, under either sandbox mode. This is not an arm64 or emulation artefact: the PTY module
is absent from both wheels. Option A below (a vendor runtime build) is now the only path that keeps
section4's pinned policy; B also needs it, because the PTY fails under `danger-full-access` too.
Consequence beyond part 52: a containerised engine worker on this runtime version has no working
shell tool at all.

## Options for the owner (decide before a gate is built)

A. A runtime build that ships `pty.node` for linux-arm64 and detects a sandbox backend (vendor fix).
   Then re-run `probe/` and continue with the gate design in the brief.
B. Run the worker with `danger-full-access` once the PTY works. The container becomes the only
   write boundary (read-only root, cap-drop ALL, uid 10001, internal network, workspace-only mounts),
   and the DEBUG gate the only command policy. This reverses section4's pinned `workspace-write`
   policy, so it needs an explicit decision.
C. Allow unprivileged user namespaces for bubblewrap (seccomp unconfined or a userns sysctl).
   This weakens the container boundary; not recommended.

## Reproduce

Build the worker image from the repo root (`docker build -f section4/worker.Dockerfile --target
worker -t harness-engine-worker:s12 .`), then build `probe/Dockerfile` on top of it. Start
`probe/mock_model.py <port> <commands.json>` on the host, and run `probe/probe_run.py` with
`PROBE_BASE_URL=http://host.docker.internal:<port>/v1` in the container. `PROBE_MODE` selects the
sandbox mode.

## B1: Section 5 shell gate (2026-10-21)

The owner chose "spike a plugin, fall back to orchestrator-level checks". The spike worked, so there was no fallback.

**How it works.** The runtime composes plugins from patch layers. `--patch` entries can disable a shipped entry by id
and `- insert:` a new one, with the module named by path (`--dump-config-schema`, `$defs/patch`). The per-run overlay
`engine._execution_policy_patch` now also:

- disables `persistent-bash` (the PTY tool that cannot start on Linux);
- inserts `section4/src/harness_orchestrator/dsh_plugins/harness-shell.js` with this run's policy as `config`.

The plugin registers `bash` and needs no npm install. Each call spawns `python -I shell_gate.py` (Section 5,
`section5/src/harness_sandbox/shell_gate.py`) with only `PATH` and `HOME`. The gate runs `validate_command` (allow-list,
git subcommands, no shell operators or substitution, secret files, plan `forbiddenPaths`), then the run intent's
`scopePaths` check, then the command under `confine_argv` (Landlock on Linux, Seatbelt on macOS; neither = refused) with
`sanitize_environment`. A `ctx.tools.guard` counts every tool call of the run and denies calls past `maxToolCalls`.
A missing gate file fails the run closed. The worker image copies the gate and its two sibling modules to
`/opt/harness-shell` (`HARNESS_SHELL_GATE`).

**Landlock fix found on the way.** Inside the container the gate first refused everything ("no OS confinement").
`confinement.apply_landlock_in_child` had never worked. It called `landlock_add_rule` without its `flags` argument (EINVAL),
and added a read rule for `/` with rights the ruleset did not handle (also EINVAL). The probe therefore always failed, and
Section 5's `auto` mode ran `off` on Linux. Both are fixed; truncate is handled on ABI 3+ and `/dev/null` stays writable.
Effect beyond this part: the Section 5 sandbox service now confines writes on Linux where it previously did not.

**Live run** (`harness-engine-worker:b1`, linux-arm64, Docker Desktop kernel 6.12.76; `--read-only --tmpfs /tmp
--cap-drop=ALL --security-opt no-new-privileges --user 10001:10001`). The real `DeepSeekHarnessWrapper` ran in the container
against the scripted model (`part52-probe/mock_model.py`), driven by `part52-probe/gate_run.py` and
`part52-probe/gate_commands.json`, with policy `maxToolCalls 10, scopePaths ["src"]`:

| # | Command | Result |
|---|---|---|
| 0 | `echo hello` | ran: `hello`, exit 0 |
| 1 | `id -u` | refused: binary not in allow-list |
| 2 | `python3 -c "open('src/made.txt','w')..."` | ran: workspace write, exit 0 |
| 3 | `cat src/made.txt` | ran: `ok` |
| 4 | `python3 -c "open('/etc/x','w')..."` | ran, write failed: read-only file system |
| 5 | `python3 -c "open('/tmp/x','w')..."` | ran, write failed: `PermissionError` (Landlock; `/tmp` is a writable tmpfs) |
| 6 | `git --version` | refused: git subcommand not allowed |
| 7 | `curl https://example.com` | refused: binary not in allow-list |
| 8 | `cat .env` | refused: secret file |
| 9 | `ls docs` | refused: outside the intent's scope paths |
| 10 | `echo after-cap` | refused: run tool-call limit reached (10 calls) |

All 11 calls came back on the result as `toolCalls` (the engine records them in `agent_tool_calls`). This meets the
brief's acceptance: a denied command is refused and recorded, and allowed commands run. The DEBUG-trap design is
dropped: no runtime framing bytes reach a shell any more.

**Tests.** `section5/tests/unit/test_shell_gate.py` (the gate run standalone: denials, forbidden and scope paths,
confinement, environment scrubbing); `section4/tests/unit/test_engine_shell_policy.py` (patch contents, missing gate fails
closed, call folding, the service handing over forbidden paths, intent scope and remaining calls, and recording that
counts toward the cap).

**Limits.**
- One program per call, no pipes or redirection, no shell state between calls. The model is told so in the tool description.
- Path checks are argument-level, as in Section 5's exec path: a path built inside `python -c` is stopped only by confinement,
  which keeps writes inside the workspace but not inside the intent scope.
- Network is not confined by the gate. The worker's internal network is the boundary.
- Tools that write under `HOME` (npm and pip caches) fail under confinement; the gate does not redirect `HOME`.
- No external penetration test. Part 52 keeps its external cap of 8.
