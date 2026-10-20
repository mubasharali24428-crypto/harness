# P0: container isolation exit evidence

**Status: P0 complete, 18/18 live checks passed.**

Date: 2026-10-18. Scope: roadmap P0.1–P0.5 only. P1–P3 remain required
before the real-user pilot.

## Implementation

`section4/worker.Dockerfile` builds the worker with SDK and runtime
`0.2.1rc2` from the existing pinned `live` extra. The Python base is pinned
by digest. Its default user is 10001. Launch uses the host's non-root uid/gid
so bind-mount writes retain their owner (root callers use 10001).

`HARNESS_ENGINE_CONFINEMENT=container` selects one worker container per call.
Only that run's workspace and session directory are bind-mounted, read/write,
at their original absolute paths. The host temporary directory and sibling
runs are not mounted. The root is read-only; `/tmp` is a 256 MiB tmpfs with
`nosuid,nodev,exec`. Explicit `exec` is necessary: the pinned runtime extracts
and loads native addons there. No capabilities, no new privileges, 128 PIDs,
1 GiB memory, and one CPU are allowed. Docker's default seccomp profile remains
in effect. Host environment values are allowlisted; provider credentials go
through the Docker client's environment, never command arguments.

Each run has an internal Docker network and a small, non-root relay container.
The relay also joins Docker's bridge network and forwards HTTP requests only
to the configured model scheme/host/port. HTTPS upstream certificates are
verified. It does not support CONNECT, arbitrary request destinations, or
follow redirects. No ports are published. The worker has no external network
and its external DNS forwarding is disabled; internal `model` DNS still works.
The relay receives no provider key in its environment and does not log requests.
A local model URL is mapped to `host.docker.internal` at the relay only.
The worker starts after a relay listener readiness check.

Cancellation forcibly removes the worker container, killing all its processes,
then confirms absence with a successful Docker container listing. It waits for
the Docker launcher to exit, preventing a create-after-cancel race. A dead CLI
or an unavailable daemon does not count as proof: worker activity remains true
until absence is confirmed. The existing stop handler therefore retains
`stopping` until it can return `stopped`. Relay and network cleanup follows.
The Docker socket is never mounted into either worker or relay.

The real runtime gate now accepts `container` and explicit `off`. Default,
`auto`, `seatbelt`, and `required` still refuse the isolated real runtime.
A missing Docker executable, daemon, image, or failed relay startup returns an
error; there is no fallback to host execution. Seatbelt remains available for
stand-in tests. Package initialization lazily imports the orchestration service
so the minimal worker does not require database dependencies.

## Repeat the evidence run

From the repository root, with Docker running and local Ollama serving the
already-installed `qwen3.5:9b` model on port 11434:

```sh
docker build -f section4/worker.Dockerfile -t harness-engine-worker:p0 .
docker build -f section4/worker.Dockerfile --target isolation-tests \
  -t harness-engine-worker:p0-tests .
HARNESS_P0_DOCKER=1 HARNESS_P0_LIVE=1 section4/.venv/bin/python -m pytest \
  section4/tests/integration/test_container_p0.py -v \
  --basetemp=/tmp/harness-p0-release \
  --junitxml=/tmp/harness-p0-release.xml
section4/.venv/bin/python -m pytest section4/tests -q
section4/.venv/bin/ruff check section4/src section4/tests
```

The Docker suite explicitly sets `isolated=True`, independently of the unit
suite's default `HARNESS_ENGINE_ISOLATED=0`. Its eighteen checks cover:

1. Non-root identity.
2. Writes in the own workspace.
3. Writes in tmpfs.
4. Writes at the root denied.
5. Writes under `/etc` denied.
6. Writes under `/var` denied.
7. Docker socket absent.
8. Host secret environment absent.
9. Effective capabilities empty.
10. No-new-privileges set.
11. Direct Internet TCP denied.
12. Direct model-host name access denied.
13. Two concurrent workers cannot read/write each other's files.
14. Sibling workspace read/write and writes outside the workspace denied.
15. Stop kills worker and child; response remains `stopping` before confirmation
    and becomes `stopped` after removal (real container and stop response builder;
    audit emission is stubbed, auth is outside this test's scope).
16. Model relay reachable; direct numeric host-IP model access denied.
17. Real-runtime gate refuses unsupported modes.
18. Real SDK/runtime runs shell and creates `hello.txt` containing `task0 ok`.

The stand-in tests exercise boundary attacks deterministically; the last test
uses the actual pinned SDK/runtime and local model. This is a reproducible
replacement for the earlier uncommitted 18-check stand-in driver; it does not
claim another Postgres or shared-UI verification.

## Operations and limits

Run the orchestrator on the Docker host with absolute workspace/session paths
visible to that daemon. Set `HARNESS_ENGINE_CONFINEMENT=container` and optionally
`HARNESS_ENGINE_IMAGE` to an operator-controlled image tag or digest. Keep
`HARNESS_ENGINE_ISOLATED=1` for pilot deployments. Existing compose services
are not automatically switched to this mode; host Docker access must be
configured deliberately. Do not expose the daemon to run participants.

The verified host is macOS/ARM64 with Docker Desktop 29.5.3. Repeat the suite on
the chosen pilot host. Linux requires the model to listen on an address reachable
through `host.docker.internal`; loopback-only listening may be insufficient.
Docker daemon loss can leave resources requiring operator cleanup; use the
exact `harness-<uuid>` worker, relay, and network names when investigating.
Containers are the implemented boundary; landlock and microVMs remain unbuilt.
P2 audit/chaos work and P3 retention/rollout work are not completed by P0.

## Recorded results

Section 4 regression suite: **592 passed, 25 skipped** (18 opt-in Docker checks
and 7 other environment-dependent checks). Ruff over Section 4 source/tests
and `git diff --check` both pass. Machine-readable results are stored in
[evidence/p0/regression.junit.xml](evidence/p0/regression.junit.xml).

Live Docker suite: **18 passed in 68.24 seconds**, including real SDK/runtime
`0.2.1rc2` with Ollama `qwen3.5:9b`; [hello.txt](evidence/p0/hello.txt) contains
`task0 ok`. See [container JUnit evidence](evidence/p0/container.junit.xml).
All per-test worker/relay containers and networks were removed after the run.

Verified image identities:

- Worker: `sha256:874f2a3a79463829b2b211c291e15a68c24d2a7324ab839e8ab35adf1eddeeb0`
- Probes: `sha256:b08f75bc8255ec00cc968da0b75e3ee5234650fefe39c393c0843c7b213c43fc`

P0.1: image built and real shell hello-world passed. P0.2: launch-policy unit
checks and kernel probes passed. P0.3: live stop/cancellation and daemon-loss
regressions passed. P0.4: all eighteen live checks passed. P0.5: unsupported
real-runtime modes and missing Docker fail closed; contained real execution
passed. No contract or schema changes were needed.
