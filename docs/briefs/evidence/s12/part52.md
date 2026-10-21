# Part 52: shell gate in the worker image (session 12): BLOCKED, no gate shipped

**Result: blocked.** The worker image cannot run the agent's bash tool on this host (Docker Desktop
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
