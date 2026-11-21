# P-04 — Dedicated sandbox worker / execution-backend boundary review

## 0. Provenance and evidence discipline

- **Written:** 2026-11-11, by the fleet reviewer (role S+B) for task **P-04** of
  `docs/plans/90-day-execution-plan-2026-11-11.md:53` (A-02 thin slice).
- **Baseline:** branch `w28-s1-delegate`, commit `7c1acf4` ("D1: a workspace-root scope covers
  every relative in-workspace path"), plus untracked working docs. Not a clean release baseline
  (`FUTURE PLAN.md:19`). All file:line citations below are against this working tree.
- **Method:** READ-ONLY code review. No code modified, no tests executed, no Docker commands run
  (daemon down on the review host), nothing committed, `.env` not read (`.env.example` used for
  config documentation). Historical drill JSONs under `docs/briefs/evidence/pilot-prep/` were read
  as *records of past runs*, not re-executed.
- **Evidence states** per `FUTURE PLAN.md` §2 (`FUTURE PLAN.md:26`): every claim in this document
  is either **"Implemented—unverified"** (code exists, no current-revision execution proof) or
  **"code-read evidence at <file:line>"**. Nothing here is "Verified"; only the owner/test agent
  can move a claim to `Verified at <revision>` by running the drills in §8 on the intended host.
  Historical passes (e.g. STATUS.md's October drill) are cited as dated records, not present proof.
- **Readiness posture:** `STATUS.md:19-21` records **NOT READY** for production or a general
  pilot; `STATUS.md:31-33` lists "a reviewed dedicated sandbox worker/daemon, host certification,
  the first native-Linux drill run" as remaining blockers. This review does not lift any of them.

### 0.1 Two different "dedicated sandbox worker" tracks (do not conflate)

1. **Section 5 sandbox API** (`/api/v1/sandbox/tasks*`, port 8005): `LocalSandboxRunner` /
   `DockerSandboxRunner` behind `deploy/runners/services.py:641-718` (`app_sandbox`). The compose
   `sandbox` service has **no Docker socket mount** (`deploy/docker-compose.yml:536-561`), so
   `SANDBOX_USE_DOCKER=true` there fails closed; the default is the host-confinement path.
   README calls this deployment "still needs review" (`README.md:111-113`).
2. **Section 4 engine runs** (the live agent path): orchestrator → `worker-daemon` (compose
   profile `worker`, `deploy/docker-compose.yml:487-534`) → per-run `engine-worker` container.
   This is the M07 "reviewed holder of the Docker socket" (`docs/sandbox-isolation.md:90-106`).

Both are reviewed below. The Step-1 production gate (`docs/sandbox-isolation.md:73-88`) applies to
track 1; track 2 explicitly does **not** meet the "separately managed rootless daemon" bar yet
(`docs/sandbox-isolation.md:103-106`).

---

## 1. Per-backend boundary table

Legend: **FC** = fail-closed (refuses to run), **FO** = fail-open / not enforced at that layer.
All rows are *Implemented—unverified* at 7c1acf4; citations are code-read evidence.

| Backend | Reads | Writes | Execute | Network | Fail-closed behavior | Grade / caveats |
|---|---|---|---|---|---|---|
| **Seatbelt (macOS, section5)** | Broad `(allow file-read*)` minus per-sibling-task `deny file-read-data` and forbidden-path denies (`confinement.py:162-165,172,118-134`). Host credential dirs NOT denied on this path. | Only task workspace (or M03 `write_roots`) + private tmp + `/dev/null` (`confinement.py:154-158,174`) | `(allow process-exec)`/`process-fork` under deny-default profile; command allow-list is policy layer (`policy.py:87-120,312-388`) | `(deny network*)` unless `allow_network` (`confinement.py:166`) | FC: explicit `seatbelt` request without usable probe raises (`local_runner.py:75-79`); `auto` with no boundary raises (`local_runner.py:95-101`); probe actually runs `sandbox-exec` (`confinement.py:43-57`) | Pilot-grade. Reads outside the storage root are unrestricted (host files readable by service uid). Real-profile tests exist but macOS-only. |
| **Landlock (Linux, section5)** | Allow-list: workspace, private tmp, HOME, system roots `/usr /bin /sbin /lib /lib32 /lib64 /opt /etc /proc /dev /sys /run` (`confinement.py:256-269,332-343`); forbidden paths carved out by splitting dirs (`confinement.py:284-308`); `/` never a root (`confinement.py:351-352`) | Write ruleset: workspace (or `write_roots`) + private tmp + `/dev/null`; `LL_TRUNCATE` on ABI≥3 (`confinement.py:411-431`) | `LL_EXECUTE` granted only within read allow-list (`confinement.py:248-250`); two stacked rulesets applied in `preexec_fn` before exec (`local_runner.py:143-152`, `confinement.py:364-447`) | **NOT ENFORCED** — ABI v1 has no network hooks; `allow_network` is a no-op (`confinement.py:446`). Egress is policy-layer only (`policy.py:142-161`). | FC: runtime probe forks a child through the same hook (`confinement.py:74-110`); explicit `landlock` unsupported raises (`local_runner.py:80-84`); any ruleset/prctl failure raises inside `preexec_fn`, aborting the spawn (`confinement.py:383-409`, docstring 372-377) | Pilot-grade, kernel/runtime dependent. HOME and `/proc` fully readable (see G2). No network boundary at all (G1). |
| **DockerSandboxRunner (section5, per-task)** | Container sees only bind-mounted workspace + private tmp; forbidden paths masked by ro empty-file bind / ro tmpfs (`docker_runner.py:122-151,187-191`); root fs read-only (`docker_runner.py:200`) | Only the two rw binds (`docker_runner.py:187-190`); root fs `read_only=True` (`docker_runner.py:200`) | `cap_drop=ALL`, `no-new-privileges`, pids 128, mem/cpu limits, non-root user = service uid (`docker_runner.py:201-207`); command policy still applied per exec (`docker_runner.py:287,318`) | Default `network_mode=none` (`docker_runner.py:171-173`); opt-in requires BOTH `SANDBOX_EGRESS_NETWORK` and `SANDBOX_EGRESS_PROXY_URL` else raises (`docker_runner.py:174-179`); proxy env injected (`docker_runner.py:166-168`) | FC: no SDK / unreachable daemon / missing image / start failure all raise `SecurityPolicyViolationError` and destroy the half-created task (`docker_runner.py:93-109,211-223,236-240`); unconfirmed container removal raises (`docker_runner.py:342-354`); deployed wiring passes `image=""` when unset so no reviewed image = refuse (`services.py:683-688`, `docker_runner.py:86-87`) | **Never drilled against a live Docker daemon** (`README.md:111-112`). Bind-source paths assume runner fs == Docker host fs (G5); shared egress network makes sibling tasks peers (G4); masks are launch-time only (G6). |
| **BYO runner (W13, customer host)** | Whatever the host shell gate grants: Landlock allow-list incl. **the operator's real HOME and /proc**, or Seatbelt broad reads (`byo_runner.py:75-102` → `shell_gate.py:151-160` → `confinement.py:501-536`). No credential-dir denial. | Shell gate `confine_argv` write roots = run `scopePaths` inside the operator checkout (`shell_gate.py:87-104,151-156`); private gate tmp | Every command through `shell_gate.py` in its own process: `validate_command` + scope check + OS confinement; none available = denied (`shell_gate.py:122-160`); server re-checks commands against the approved plan (`byo_runner.py:9-11` docstring; control plane `section1/src/harness_identity/runner_api.py`) | **No runner-side network boundary** beyond the gate's Seatbelt/Landlock posture (Landlock: none, G1). Host network is the customer's. | FC: gate refusal → `{"denied": ...}` event (`byo_runner.py:100-102,212-213`); gate hang → `killpg` (`byo_runner.py:91-94`); lost lease/stop → cancel tick kills group (`byo_runner.py:173-178,95-98`); token must be `hrn_` (`byo_runner.py:238-242`); nonce single-use + attestation bound to registered confinement (`runner_api.py:210-243,285-292`) | Pilot-grade, **trusted-host model** (`byo_runner.py:9-11`). Attestation is self-reported; `policyDigest` format-checked only (G7). `--confinement none` is an attestation label, not a gate bypass — the gate still refuses without OS confinement (`shell_gate.py:157-160`). |
| **LocalSandboxRunner (section5, process)** | Seatbelt/Landlock posture above; task dirs 0700, storage root 0700 (`local_runner.py:40,207,214,219-220`); taskId is a validated single path component (`local_runner.py:103-109`, `policy.py:25-34`); team ownership bound at create, resume only by owner (`local_runner.py:196-214`) | Per-task workspace + private 0700 tmp; env `TMPDIR/TMP/TEMP` redirected (`local_runner.py:128-130`); OS write rules per backend | `validate_command` per exec/edit (`local_runner.py:253,313,353`); env sanitized of secret-shaped vars (`policy.py:248-260`); process group per spawn, group-kill on timeout/cancel/destroy (`local_runner.py:111-122,136,299-302,365-367`) | Constructor default `allow_network=True` (`local_runner.py:37`); deployed wiring passes `HARNESS_SANDBOX_ALLOW_NETWORK` (default 0) (`services.py:677,687`). Enforced only by Seatbelt; Landlock no-op (G1) | FC: `auto` with no usable OS boundary raises (`local_runner.py:95-101`); `off` requires explicit `HARNESS_SANDBOX_ALLOW_UNCONFINED=1` and logs a warning (`local_runner.py:54-66`); `container` mode refused on this class (`local_runner.py:67-71`) | Pilot-grade. `off` is a development escape hatch, never sandbox evidence (`docs/sandbox-isolation.md:62`). |
| *(supplementary)* **Engine worker container (section4, deployed live path)** | Only the run's own workspace/session binds; other runs' dirs not mounted at all; forbidden paths ro-bind, secret-named files bound to `/dev/null` (`confine.py:119-142,179-203,473-480`); native file tools disabled outside container mode (`engine.py:220-226`) | Workspace ro when intent scope is narrower; only scope binds rw (`confine.py:145-172,465-472`); root fs `--read-only`, tmpfs `/tmp` (`confine.py:442,460-461`) | `--cap-drop=ALL`, `no-new-privileges`, pids 512, mem 1g, cpus 1, never root (`confine.py:443-459`, `_worker_user` 335-341); shell via section5 gate (`engine.py:206-215`) | Per-run `--internal` network, no gateway; `--dns 127.0.0.1` kills external resolution (`container_runtime.py:90-95`, `confine.py:440-441`); only peers: fixed-origin model relay + egress proxy alias (`container_runtime.py:71-137`) | FC: real worker refused unless `container` or explicit `off` (`engine.py:69-97`); missing shell gate fails the run (`engine.py:206-208`); daemon loss fails the run, never degrades (`engine.py:1036-1039`); unconfirmed container kill raises (`engine.py:916-917`) | The most complete boundary in the repo. Daemon holds the **host's main** Docker socket as root (G3). Seatbelt variant (macOS dev) allows `network*` (`confine.py:267`). |

### 1.1 Layering contract (all backends)

`policy.py` decides WHAT may run (allow-list binaries `policy.py:87-120`, git subcommands
`policy.py:122-140`, blocked patterns `policy.py:142-161`, no shell operators/substitution
`policy.py:43-84`, secret-file globs + symlink spellings `policy.py:210-221,263-309,373-388`,
install verbs gated by `install_permission` = operator flag + container boundary + egress alias
`policy.py:171-208`). `confinement.py` decides WHERE it may touch the machine
(`confinement.py:7-10`). The policy layer is lexical: a program that builds paths at runtime
(`python3 -c "open('do'+'cs/x','w')"`) is stopped only by the OS boundary
(`docs/sandbox-isolation.md:63`, `shell_gate.py:89-93`). This is why the per-backend OS rows above
are the load-bearing claims, and why G1/G2 matter.

---

## 2. The two October Landlock holes — fixed in code?

**Yes, both fixes are present at 7c1acf4 (code-read evidence; live re-verification is the test
agent's job, §8).** STATUS.md records the drill that found them: native-Linux container tool drill
on ubuntu-latest, kernel 6.17, "exposed and fixed two Landlock allow-list holes that had granted
`/` as readable (via `/bin/sh` and via `HOME=/` in `--user` containers)" (`STATUS.md:26-29`).
The fix narrative is also in `docs/sandbox-isolation.md:50-59`.

### Hole 1 — `/` readable via `/bin/sh` (exec bootstrap grandparent rule)

- **Fix:** `exec_read_roots` at `section5/src/harness_sandbox/confinement.py:480-498`. The old rule
  appended the program's grandparent when its directory was named `bin`; the new code returns `[]`
  for any program already inside `_LINUX_READ_ROOTS` or HOME (`confinement.py:489-497`), and never
  grants `/` (`confinement.py:494-495`). `/bin/sh`'s directory `/bin` is a listed root
  (`confinement.py:258`), so it adds nothing.
- **Wiring:** the Linux exec bootstrap calls it (`_LANDLOCK_EXEC`, `confinement.py:464-477`,
  specifically line 471) before `apply_landlock_in_child`.
- **Regression tests (exist, not run here):**
  `section5/tests/unit/test_landlock_read_allowlist.py:130-141`
  (`test_exec_read_roots_never_grant_the_filesystem_root`: `/bin/sh`, `/usr/bin/python3`,
  `/opt/tool/bin/x`, HOME-local bin all add nothing) and `:144-148` (only a truly outside tool dir
  is added).

### Hole 2 — `/` readable via `HOME=/` in `--user` containers

- **Fix:** `landlock_read_allowlist` skips any root equal to `/` —
  `section5/src/harness_sandbox/confinement.py:348-352` (comment names Docker's `--user` +
  no-passwd-entry `HOME=/` case explicitly; `if root == Path("/"): continue`).
  `exec_read_roots` also refuses `/` even when HOME is `/` (`confinement.py:494-495`).
- **Regression tests:** `test_landlock_read_allowlist.py:151-163`
  (`test_home_root_never_grants_the_filesystem_root`, monkeypatches `HOME=/`) and `:165-167`
  (`test_exec_read_roots_never_returns_root`).
- **Drill coverage of both:** `scripts/native_linux_drill.sh` runs the gate with
  `--user "$(id -u):$(id -g)"` (line 73) — i.e. the `HOME=/` shape — and the gate always execs
  `/bin/sh` (`shell_gate.py:154`); the sibling-read check (lines 93-95) and outside-write check
  (lines 100-102) are what would catch a regression. The drill's own notes name both fixes
  (`native_linux_drill.sh:131-132`).

**Residual risk (not a re-opening of the holes):** the allow-list still grants the *real* HOME and
`/proc` when they are not `/` (G2), and `/run` (G11). The October fixes removed the accidental
whole-filesystem grant, not the intentional HOME grant.

---

## 3. Dedicated worker / daemon review

### 3.1 Deploy wiring (`deploy/runners/services.py`)

- One FastAPI app per service; `app_sandbox` (`services.py:641-718`) selects the runner:
  `SANDBOX_USE_DOCKER` truthy → `DockerSandboxRunner(image=<SANDBOX_DEFAULT_DOCKER_IMAGE or "">,
  allow_network=<HARNESS_SANDBOX_ALLOW_NETWORK==1>)`, else `LocalSandboxRunner(confinement=
  <HARNESS_SANDBOX_CONFINEMENT default auto>, allow_network=...)` (`services.py:674-688`).
  Empty image string is deliberate: deployment without a reviewed image fails closed
  (`docker_runner.py:86-87`).
- Auth is fail-closed at three layers: bearer principal required (`api.py:37-45`), writer role for
  exec/edit/destroy (`api.py:48-55`), project→team lookup wired or 503 (`api.py:61-81`,
  `services.py:705-717`). Foreign tasks read as missing — no existence oracle
  (`api.py:96-105,150-155,199-205`).
- Boot refuses a dev master key unless explicitly opted in (`services.py:23-32`).
- `/ready` gates on DB + migration drift; egress reachability is **informational only**, never a
  readiness reason (`services.py:121-229`). Consequence: a sandbox service can be "ready" while
  the proxy is down; runs that need network then fail at dial time, not at admission.
- Compose `sandbox` service: resource caps 1 CPU / 768m / pids 256 (`docker-compose.yml:540-545`),
  port 8005 published (552-553), **no volumes, no Docker socket** (536-561). So the deployed
  section5 path is host-confinement inside a shared backend container; per-task Docker there is
  impossible without a deployment change (and would then hit G5).

### 3.2 `engine-worker` and `worker-daemon` compose services

- `engine-worker` (`docker-compose.yml:465-485`): **build-only** (profile `worker`, entrypoint
  `true`, restart `no`). It is the image the daemon starts once per run; it never runs as a
  service. Image `harness-engine-worker:v${HARNESS_IMAGE_TAG}` built from
  `section4/worker.Dockerfile` target `worker` (base digest-pinned `python:3.12.12-slim-bookworm`,
  `worker.Dockerfile:1`; gates copied at 14-29; `USER 10001` at 39; Chromium only in the worker
  stage, 58-67).
- `worker-daemon` (`docker-compose.yml:487-534`): profile `worker`, image target `daemon`
  (`worker.Dockerfile:53-56`: same code + static Docker CLI, **USER root** because the socket is
  root-owned). Mounts `harness_runs` volume and **`/var/run/docker.sock`** (528-530) — the only
  service with the socket. No published port. Bearer token required
  (`HARNESS_WORKER_DAEMON_TOKEN`, 505); daemon refuses to start without it
  (`worker_daemon.py:268-270`). Caps 0.5 CPU / 256m / pids 128 (497-502). **No healthcheck**
  (contrast sandbox/egress at 554/582) — a wedged daemon is not detected by compose (G12).
- Orchestrator is configured to never hold a socket: `HARNESS_ENGINE_ISOLATED=1`,
  `HARNESS_ENGINE_CONFINEMENT=container`, `HARNESS_WORKER_DAEMON_URL=http://worker-daemon:8010`
  (`docker-compose.yml:424-430`); without the daemon a live run fails closed (comment 425-426;
  code `engine.py:1036-1039`).

### 3.3 What the daemon does (`section4/src/harness_orchestrator/worker_daemon.py`)

- Accepts run id + goal + policy + provider key/baseUrl + `DSH_*`-prefixed env only
  (`worker_daemon.py:69-87`; env filter `confine.ENV_ALLOW_PREFIX` at 82). **Nothing that shapes
  the boundary is client-supplied**: paths come from the daemon's own `DSH_WORKSPACE`/
  `DSH_SESSION_ROOT`, image from `HARNESS_ENGINE_IMAGE`, mode forced to `container` at startup
  (`worker_daemon.py:272`), limits from `confine.wrap_command`
  (`worker_daemon.py:1-8` docstring; `confine.py:424-502`).
- Auth: constant-time bearer compare on every route (`worker_daemon.py:97-100`); run-id regex
  (`:44`); body cap 48MB (`:43,159-161`).
- One worker per run id: duplicate POST → 409 (`worker_daemon.py:170-174`). NDJSON stream back;
  a disconnect cancels the run (`:184-196`); the run is unregistered in `finally` (`:197-199`).
- The daemon builds the boundary via `DeepSeekHarnessWrapper._run_isolated`
  (`worker_daemon.py:186-192` → `engine.py:758-952`): per-run `--internal` network labelled with
  the run id (`container_runtime.py:90-95`), fixed-origin model relay container (read-only,
  cap-drop ALL, no-new-privileges, pids 32, 128m, uid 10001; `container_runtime.py:101-132`;
  relay forwards to exactly one origin, no CONNECT/redirects/logging, `model_relay.py:21-60`),
  egress proxy attached under alias `harness-egress` on the **web** purpose port
  (`container_runtime.py:15-21,71-88`).
- Worker launch flags: `--read-only --cap-drop=ALL --security-opt no-new-privileges --pids-limit
  512 --memory 1g --cpus 1 --user <workspace owner, never root> --dns 127.0.0.1`, tmpfs `/tmp`,
  workspace mounted ro when a narrower scope exists, scope binds rw, forbidden paths ro,
  secret-named files bound to `/dev/null` (`confine.py:424-502`; caps at 119-142 and 183-203
  refuse to run rather than partially protect). Provider key rides `--env NAME` (value from the
  scrubbed client environment, never argv) (`confine.py:481-491`, `engine.py:793-807,849-857`).
- Run dirs: orchestrator creates them on the shared `harness_runs` volume as its own uid
  (`engine.py:982-988`); a root daemon hands only root-owned dirs to uid 10001
  (`confine.py:354-404`); mount translation binds the volume's engine-host directory (or
  `volume-subpath` fallback) because the daemon's container paths don't exist on the Docker host
  (`confine.py:306-332`). The volume name is pinned outside the compose project prefix and
  guarded by a test (`docker-compose.yml:1494-1501`).

### 3.4 Isolation between runs

- **Engine container path:** each run gets its own workspace/session/tmp dirs, its own network,
  relay and worker container; other runs' directories are simply not mounted (stronger than a
  deny rule). Cross-run reads are impossible unless the shared volume is mounted elsewhere —
  only orchestrator + daemon mount `harness_runs` (`docker-compose.yml:440-441,528-529`).
- **Engine Seatbelt path (macOS dev):** profile denies reads of the shared runs roots except the
  run's own dirs, and denies credential dirs (`confine.py:229-269`). Network is allowed
  (`confine.py:267`) — no egress boundary in that mode (documented, `confine.py:56-59`).
- **Section5 local path:** 0700 per-task dirs + team ownership (T15/T16,
  `local_runner.py:103-109,196-214`); Seatbelt sibling denies refreshed on every create/destroy
  (`local_runner.py:155-181,235-236,370-371`); Landlock sibling isolation via the read allow-list
  (`confinement.py:12-20,252-255`) — valid while the storage root is outside HOME (default
  `/tmp/harness_sandboxes`, `config.py:11`; see G10).
- **DockerSandboxRunner:** per-task container, but with networking on, all task containers share
  `harness_sandbox_egress` and can reach each other (G4).

### 3.5 Cancellation

- Local runner: budget bounds every read; timeout/cancel/destroy all `killpg(SIGKILL)` the whole
  group incl. grandchildren (`local_runner.py:111-122,269-302,331-334,365-367`).
- Docker runner: in-container `timeout --signal=KILL <budget>s` wraps every exec
  (`docker_runner.py:249-251`); on budget expiry the whole container is removed and recreated
  (`docker_runner.py:294-309,328-332`); `destroy` force-removes and raises if removal cannot be
  confirmed (`docker_runner.py:342-354`). There is no cancel API short of destroy; an asyncio
  cancellation of the streaming exec does not stop the in-container command until its internal
  timeout (G13).
- Engine: watchdog `Timer` kills the worker process group at `HARNESS_ENGINE_TIMEOUT_SECONDS`
  (default 900) (`engine.py:862-888`); `boundary.kill` loops `docker rm --force` until the
  container is confirmed gone — daemon loss is never confirmation
  (`container_runtime.py:154-179`); unconfirmed kill raises (`engine.py:916-917`).
  `cancel()` forwards to the daemon when configured (`engine.py:703-737`); daemon cancel reaps an
  orphan it does not hold, or cancels the held wrapper (`worker_daemon.py:151-157,53-66`).
- BYO: heartbeat response `cancel` or any API error → gate tick → `proc.terminate()`; gate
  SIGTERM handler `killpg`s the command group (`byo_runner.py:166-178,95-98`,
  `shell_gate.py:167-170`); gate hang past deadline+10 → `killpg` (`byo_runner.py:91-94`).

### 3.6 Lease / fencing behavior

- **BYO control plane** (`section1/src/harness_identity/runner_api.py`): single-use nonce +
  timestamp anti-replay, committed before body parse (`runner_api.py:181-243`); attestation
  HMAC-keyed from the runner token, digest-bound, and the attested confinement must equal the
  **registered** confinement (`runner_api.py:265-309`, mismatch 409 at 285-292); attempt-keyed
  `runner_jobs` — only one live attempt per run, an orphaned prior attempt never blocks
  re-dispatch (`runner_api.py:584-604`; migration 0073 per `STATUS.md:117,147`); expired leases
  are released and the run **orphaned, never auto-re-queued** (`runner_api.py:665-696`); runner
  settles failed runs rather than letting leases expire (`byo_runner.py:180-191`). Events are
  bounded and scrubbed (`byo_runner.py:39-53,210-222`).
- **Worker-daemon:** no lease concept — in-memory `_RUNS` map under a lock
  (`worker_daemon.py:45-46,170-174`); durability comes from the orchestrator's event log:
  after daemon loss an in-memory reaper retries `POST /runs/<id>/reap` for up to 3600s
  (`engine.py:67,1041-1069`); if the orchestrator also restarts, the durable-proof sweep
  (`POST /sweep` with `settledRunIds`) stops orphans (`worker_daemon.py:201-224`), and the
  daemon's own weak periodic sweep (600s interval / 300s grace) removes only quiet, unheld,
  labelled networks (`worker_daemon.py:232-263`, `container_runtime.py:319-450`). "Unknown" is
  always treated as "still running" — a failed Docker listing never licenses deletion
  (`container_runtime.py:265-316,411-416`). Held runs are never swept (`worker_daemon.py:219-223`).

### 3.7 What remains unreviewed (this pass)

- `section4/src/harness_orchestrator/engine.py` live-SDK internals beyond the isolation/cancel
  paths (1530 lines; ~600 read), `dsh_plugins/*.js` (harness-shell/web/browser/images),
  `redact.py` scrub coverage of tool events, `readiness.py`, `deploy/runners/local_engine.py`
  (IDE/CLI launcher), `deploy/runners/engine_boot.py`, `deploy/pilot/worker.py`.
- Section5 `web_gate.py`, `browser_gate.py`, `browser_session.py`, `service.py` (only grepped),
  `checkpoints.py`, `diff_engine.py`, `tree_api.py` internals.
- `runner_api.py` settle/replay/evidence-from-seq logic beyond the auth/lease/attempt paths.
- `deploy/backend.Dockerfile` beyond `USER harness` (line 49) and `entrypoint.sh`.
- **Any live behavior**: Docker daemon down on the review host; the deployed stack's actual
  image tags/digests were not inspected (F-03/A-06 territory). `.env` not read by rule.

---

## 4. Egress proxy review (`section5/src/harness_sandbox/egress_proxy.py`, 130 lines)

**Topology.** `sandbox_egress` is a bridge network with `internal: true` — no gateway
(`docker-compose.yml:1477-1482`). The `sandbox-egress` service is dual-homed: `default` +
`sandbox_egress` (`docker-compose.yml:588-590`), so it is the only peer of an internal-network
container that can reach the internet. Engine runs get the same proxy attached per-run under
alias `harness-egress` (`container_runtime.py:71-88`).

**Allow-list enforcement.**

- Decisions come from `egress.py`: purpose-scoped groups — sandbox = packageRegistries +
  gitHosts + providerEndpoints; web = providerDocs + providerEndpoints (`egress.py:38-42`);
  artifact `egress_allowlist.json` is generated from the provider registry at build time
  (`egress.py:1-18`; current artifact: 54 provider endpoints, 62 docs, 4 registries, 3 git hosts).
  Exact host match, lower-cased, no suffix matching (`egress.py:95-119`; test
  `test_egress_proxy.py:108`). Unknown purpose raises rather than allowing (`egress.py:103-104`).
  Loopback/IP-literal destinations refused (`egress.py:44,108-111`). Operator additions only via
  `SANDBOX_PINNED_REGISTRIES` and only for sandbox purpose (`egress.py:76-87,117-118`).
- **CONNECT handling:** only `CONNECT host:443` is tunnelled; any other method → 405, any other
  port or unlisted host → 403 (`egress_proxy.py:64-69`). Headers are discarded, not forwarded
  (`:62-63`). Upstream dial failure → 502 (`:70-75`). TLS is end-to-end (no MITM): after
  `200 Connection Established` the bytes are piped verbatim (`:76-79`).
- **Purpose boundary = listener port**, not a caller header: 3128 sandbox, 3129 web
  (`egress_proxy.py:20-24,84-102`). *Caveat:* both listeners bind `0.0.0.0` and the proxy is
  multi-homed, so any container that can route to the proxy can reach **both** ports — including
  a sandbox task container on `harness_sandbox_egress` reaching 3129 (G4). The boundary is
  wiring, not enforcement.
- **DNS leaks:** sandbox/engine containers on internal networks have no gateway; the engine
  worker additionally sets `--dns 127.0.0.1` so external resolution dies while Docker's embedded
  DNS still resolves the `model`/`harness-egress` aliases (`confine.py:440-441` — code-read;
  the alias-resolution mechanics under `--dns` should be confirmed in the live drill). The proxy
  itself resolves allowed hostnames via its own (internet-capable) network; it does **not**
  validate that resolved IPs are public (G9). `DockerSandboxRunner` sets no `--dns`; external
  DNS behavior on the internal network is assumed-blocked, not proven here (drill item, §8).
- **Fail-closed behavior:** missing proxy on a run network is non-fatal for the *engine* path —
  `web_fetch` then fails at name resolution and the gate denial path never dials
  (`container_runtime.py:71-88`); for `DockerSandboxRunner`, networking without both egress env
  vars refuses task creation (`docker_runner.py:174-179`). The compose healthcheck asserts
  *enforcement*, not liveness: a CONNECT to `example.com:443` must return 403 on **both** ports,
  else unhealthy (`egress_proxy.py:105-124`, `docker-compose.yml:575-587`).
- **Audit:** one line per *opened* tunnel (`egress_proxy.py:44-52`); denials deliberately not
  logged (healthcheck probes every 10s). Consequence: no record of refused egress attempts (G9).
- **Resource limits:** 10s deadline on the request line and upstream connect only; no tunnel
  lifetime/idle cap, no header-loop deadline, no concurrency cap (`egress_proxy.py:17-18,61-79`);
  container caps 0.25 CPU / 128m / pids 128 (`docker-compose.yml:568-573`). A slow-loris or many
  long-lived tunnels can wedge the single asyncio process for every run (G9).
- **Historical evidence (record, not re-verified):** the proxy path *was* drilled live on
  2026-10-26 and re-run through 2026-11-05 from inside the sidecar and from a container on
  `harness_sandbox_egress` (allowed endpoint/git 200; docs/host/port 403; method 405; verdict
  pass — `docs/briefs/evidence/pilot-prep/w4-egress-network-1793895417.json`, compose comment
  `docker-compose.yml:94-98`). What has **not** been drilled is the full `DockerSandboxRunner`
  per-task path against a live daemon (`README.md:111-112`) — the README sentence is about that
  runner path, and remains accurate for it.

---

## 5. Gaps — assumed-but-not-enforced (severity + owner decision needed)

Severities are reviewer judgment at 7c1acf4, not measured exploitability. "Decision" names what
the owner must rule before a Linux pilot; none of these is fixed by this review.

| # | Gap | Evidence (code-read) | Severity | Owner decision needed |
|---|---|---|---|---|
| G1 | **Landlock enforces no network boundary.** `allow_network` is discarded; ABI v1 has no net hooks. On Linux, a sandbox-API task (allow-listed `python`) can open arbitrary sockets regardless of `HARNESS_SANDBOX_ALLOW_NETWORK=0`; the compose sandbox container sits on the internet-capable default bridge. "Network disabled by default" is enforced only on Seatbelt/container paths. | `confinement.py:446`; `local_runner.py:37,147-152`; `policy.py:87-120` (python allowed); `docker-compose.yml:536-561` (no network restriction) | **High** (P-04's target host is Linux) | Route the section5 exec path through a container/internal-network boundary like the engine worker, or implement Landlock ABI≥4 net rules, or named risk acceptance for the pilot. |
| G2 | **Process-mode read confidentiality is weak.** Seatbelt `confine_argv` profile allows all reads (no credential-dir denies); Landlock grants the whole real HOME and `/proc`. On a BYO host, shell-gated code can read `~/.ssh`, `~/.aws`, and `/proc/<runner-or-gate pid>/environ` — the gate subprocess inherits the runner's full env incl. `HARNESS_RUNNER_TOKEN` (sanitization applies only to the command's child). Engine container path is unaffected (HOME=/tmp tmpfs, secret binds). | `confinement.py:172,501-522` (no forbidden/cred args); `confinement.py:335-337,256-269`; `byo_runner.py:78-82` (Popen inherits env), `shell_gate.py:161-166`; contrast `confine.py:44,257` (engine seatbelt denies cred dirs) | **High** for BYO / bare-host pilots | Decide BYO pilot host policy (dedicated uid with no credentials, or container-only BYO), add credential-dir denies to the gate profile, or named risk acceptance. Verify the /proc token read with a negative drill (§8). |
| G3 | **worker-daemon is root-equivalent on the host.** It runs as root with the host's *main* Docker socket; whoever controls the daemon controls the host. This is explicitly not the "separately managed rootless or otherwise isolated Docker daemon" the Step-1 gate requires. | `docker-compose.yml:530`; `worker.Dockerfile:53-56`; `docs/sandbox-isolation.md:73-88,103-106` | **High** (architectural, acknowledged) | Dedicated/isolated (rootless or separate-host) daemon before production; for pilot, owner must risk-accept by name with the daemon on a single-purpose host. |
| G4 | **Shared egress network breaks "only peer is the proxy" and the purpose-port boundary.** All network-enabled `DockerSandboxRunner` tasks share `harness_sandbox_egress` and can reach each other; any of them can also dial the proxy's web port 3129 (both listeners bind 0.0.0.0; proxy dual-homed), widening its allow-list to provider docs. | `docker_runner.py:171-179`; `docker-compose.yml:1477-1482,588-590`; `egress_proxy.py:84-102` | Medium | Per-task networks (engine already does this) or accept sibling reachability; bind/firewall the web port to the default-network interface, or accept the docs-only widening. |
| G5 | **DockerSandboxRunner bind paths assume runner fs == Docker host fs.** Workspace/tmp binds use the service's own paths with no volume-subpath translation (the engine's `confine._mount` exists precisely because this fails for a containerized launcher). Mounting a socket into the compose sandbox container would mis-mount or fail. Undrilled against a live daemon. | `docker_runner.py:181-190` vs `confine.py:306-332`; `README.md:111-113` | Medium | The "dedicated sandbox worker deployment" design review P-04 names: decide where the runner process lives relative to the daemon before enabling `SANDBOX_USE_DOCKER=true`. |
| G6 | **Forbidden-path masks are launch-time only (Docker runner).** Files/dirs created after container start that match `forbiddenPaths` are unmasked; there is no detective snapshot check on this path (the engine path has one). Landlock/Seatbelt forbidden rules are also computed at spawn/profile time. | `docker_runner.py:122-151,211-223` (no re-mask on exec); contrast `engine.py:820,929-930` | Medium | Add a per-exec snapshot/mask refresh, or accept policy-layer-only protection for post-launch paths (document it). |
| G7 | **BYO attestation is self-reported.** `policyDigest` is format-checked, never compared to server-known gate bytes; confinement must match registration but a hostile host can attest falsely. The trust model ("host trusted to run and report honestly") is documented, not enforced. | `runner_api.py:299-305`; `byo_runner.py:9-11,130-146` | Medium | Pin an expected gate digest at registration (server can compute it from its own image), or formally accept the trusted-host model for pilot. |
| G8 | **Daemon control plane is plaintext HTTP on the shared network.** Bearer token + provider API key travel in the clear between orchestrator and daemon; any container on `harness_default` can reach 8010 (no published port, but no network policy either). | `worker_daemon.py:97-100`; `engine.py:954-964,989-999`; `docker-compose.yml:487-534` | Medium | mTLS or an isolated control network, or named acceptance of internal-network trust for pilot. |
| G9 | **Proxy hardening/audit gaps.** No tunnel lifetime/idle/concurrency caps, no header-read deadline (slow-loris), denials not logged, no resolved-IP validation (rebinding/compromised-allowed-host SSRF surface). | `egress_proxy.py:17-18,44-52,61-79`; `egress.py:44` | Low–Medium | Add caps + denial sampling + optional public-IP check, or accept (allow-list is fixed data; DoS blast radius = all runs). |
| G10 | **Landlock HOME root can re-expose siblings** if an operator moves `SANDBOX_STORAGE_ROOT` under HOME (default `/tmp/harness_sandboxes` is safe). Seatbelt sibling denies do not have this dependency. | `confinement.py:335-337`; `config.py:11` | Low | Document the constraint or carve the storage root out of the HOME grant. |
| G11 | **Landlock does not mediate unix-socket connects; `/run` is a read root.** A host socket such as `/run/docker.sock` (i.e. `/var/run/docker.sock`) would be usable by a Landlock-confined process if present and uid-permitted. Not a problem in the shipped compose sandbox (no socket), but a deployment constraint for Landlock-only hosts. Code-read inference — needs a drill to confirm kernel behavior. | `confinement.py:268,446` | Low (deployment constraint) | Rule: never co-locate a Docker socket with Landlock-only confinement; prefer the container boundary. |
| G12 | **worker-daemon has no compose healthcheck**; a wedged (not dead) daemon is invisible to `restart: unless-stopped`. | `docker-compose.yml:487-534` (no healthcheck; contrast 554,582) | Low | Add a `/version`-based healthcheck. |
| G13 | **Docker runner exec cancellation is budget-bounded, not immediate.** Async cancellation of a streaming exec leaves the in-container command running until `timeout --signal=KILL` fires (default 300s); only `destroy` is immediate. | `docker_runner.py:249-251,285-312`; `config.py:7` | Low | Accept (bounded) or add an exec-kill on cancel. |
| G14 | **Landlock-in-container usability is runtime-dependent.** Docker's seccomp profile has blocked the landlock syscalls historically (OPS-20, 2026-10-17); the probe now decides, and the October drill passed on ubuntu-latest's Docker. Whether the *intended pilot host's* Docker/kernel passes the probe is unverified. | `confinement.py:95-110`; `STATUS.md:26-29` | Info (drill gate) | The §8 probe assertion is mandatory on the intended host; a failed probe is fail-closed, not a bypass. |

Also noted (documented design, not new gaps): engine Seatbelt mode allows `network*`
(`confine.py:267`); `confinement=off` requires the explicit dev opt-in and is never sandbox
evidence (`local_runner.py:54-66`, `docs/sandbox-isolation.md:62`); policy-layer symlink checks
have a TOCTOU window (`docs/sandbox-isolation.md:63`).

---

## 6. Drill coverage map

### 6.1 What exists (scripts read; historical evidence where noted)

| Drill | Covers | Does NOT cover |
|---|---|---|
| `scripts/native_linux_drill.sh` (CI job `native linux container tool drill`, ubuntu-latest, push-to-master/dispatch only — `.github/workflows/ci.yml:394-409`) | Landlock probe inside a container (47-58, 80-82); shell-gate workspace write on a bind mount (84-87); normal read (89-91); sibling read denied, canary never leaks (93-95); outside write denied (100-102). Runs `--user` (HOME=/ shape) and `/bin/sh` — exercises both October fixes. Evidence: `docs/briefs/evidence/pilot-prep/native-linux-container-drill.json` (Oct pass recorded, `STATUS.md:26-29`). | Network egress under Landlock (G1); HOME/`/proc` credential reads (G2); the section5 sandbox API runners; DockerSandboxRunner; the daemon. |
| `scripts/e2e/w22_provider_egress_drill.py` | Per-run model-relay outage: disconnect relay from bridge → run must settle `blocked` (43-57); never touches shared services. | The allow-list proxy itself; sandbox-task egress. |
| `scripts/e2e/m03_scope_drill.py` | Container-mount scope enforcement with the real runtime: `scopePaths=["src"]`, write inside allowed, outside refused, disk digest verdict (100-121). Historical: `pilot-prep/m03-scope-mounts.json`, `m03-landlock-scope.json`. | Daemon topology (uses host launcher); read confidentiality; network. |
| `scripts/e2e/w4_credential_leak_drill.py` | Real `.env` secret values as needles; worker invited to `cat /proc/self/environ`; searches every stored surface incl. raw `agent_events`; requires the dump to have actually happened (127-175). Historical PASS 2026-11-05 (`pilot-prep/w4-credential-leak-1793899082.json`: `workerEnvDumped=true`, `leaks=[]`). | BYO-host `/proc` token read (G2 — different process tree, no container); macOS seatbelt reads. |
| `scripts/e2e/w21_native_confinement_drill.py` | 22 scripted native-tool cases (write/edit/read/read_image/cd, traversal, symlinks, forbidden, secret file) in `--mode container` and `--mode off` (48-81). | Shell half (delegated to native drill); daemon path. |
| `scripts/e2e/m07_daemon_drill.py` | Deployed topology: orchestrator has no socket/CLI; worker from daemon; live inspect: non-root, read-only root, no caps, internal network, volume subpaths, forbidden ro; fix lands on disk (1-18, 68-80). Historical: `pilot-prep/m07-daemon-topology.json`. | SIGKILL resilience (next row); cross-run read attempts. |
| `scripts/e2e/w22_daemon_kill_drill.py` | SIGKILL daemon mid-run; orphan reaped by orchestrator reaper, or by durable sweep when the orchestrator also restarts; `statusTruthful` snapshots; cleanup (docstring 1-22). | BYO leases. |
| `scripts/e2e/w17_linux_shell_cleanup_drill.py` | Landlock + process-group kill of a detached grandchild on timeout, via the real `LocalSandboxRunner(confinement="landlock")` (docstring). **Caveat:** suggests running inside `harness-sandbox-1`, where the Landlock probe may fail under that Docker's seccomp (G14) — a refusal there is fail-closed evidence, not a pass; run on a native host or permissive-seccomp container. | Network; reads. |
| `scripts/e2e/w13_runner_drill.py`, `w13_exec_drill.py`, `w22_heartbeat_drill.py` | BYO control plane: one-time token, signed attestation + mismatch/replay refusal, team isolation, lease lifecycle, expiry→orphan, revocation (w13 docstring legs 1-6); heartbeats during a long confined child + stop. | BYO on a *remote* Linux host (A-05 notes local-vs-remote proof separation, `FUTURE PLAN.md:68`); G2 read exposure. |
| `pilot-prep/w4-egress-network-*.json` (11 files, latest 2026-11-05) | Proxy allow/deny matrix from inside the sidecar and from the internal network (§4). | The `DockerSandboxRunner` per-task path that would use it. |

### 6.2 What P-04 still needs (no script exists yet)

1. **Re-run the October drill at 7c1acf4 on the intended ubuntu host** (plan acceptance: "drill
   output at exact revision on intended host", `90-day-execution-plan-2026-11-11.md:53`).
2. **DockerSandboxRunner live drill** (the README's undrilled path): dedicated worker + socket +
   pre-pulled reviewed image; create/exec/destroy; `network_mode=none` verified (no route, no
   DNS); forbidden mask read empty; then `HARNESS_SANDBOX_ALLOW_NETWORK=1`: only-proxy peer,
   403 matrix, **sibling-task reachability (G4)**, **3129 reachability from the sandbox net (G4)**,
   internal-network DNS behavior.
3. **Landlock network negative test (G1):** under `allow_network=False`, a confined
   `python3 -c "socket.create_connection(...)"` — expected to *succeed today*; the drill records
   the gap as fact for the owner decision.
4. **BYO /proc credential negative test (G2):** on a Linux BYO host, gate command reads
   `/proc/<runner pid>/environ` — confirm/refute the code-read finding; same for `~/.ssh` canary
   under both Landlock and macOS Seatbelt.
5. **Cross-run isolation drill on the daemon path:** run A attempts to read run B's volume
   subpath / network peer (mounts make this impossible by construction — prove it).
6. **Cancellation/lease drills at the release revision** (m07/w22/w13 reruns) — A-02 asks for
   "cancellation and cross-run isolation evidence on the intended platform".

---

## 7. Test-agent handoff (DO NOT run on the review host — these are for the Linux pilot host)

Record everything in **`docs/briefs/evidence/90day/week-02.md`** (create it; ledger convention:
`90-day-execution-plan-2026-11-11.md:26,39` — no entry = task not done). For each drill record:
date, `git rev-parse HEAD`, image tag **and digest** (`docker image inspect --format '{{.Id}}'`),
`uname -r`, Docker version, verdict, and the evidence JSON path. Move claims to
`Verified at <revision>` only there, never in this review.

### 7.1 Linux host — native boundary (no live stack needed, Docker daemon up)

```sh
cd harness
git rev-parse HEAD                      # must be the revision under test; record it
./scripts/native_linux_drill.sh         # builds harness-backend:drill if absent
```
**Pass:** probe prints `usable`; all four checks pass; script exits 0 and writes
`docs/briefs/evidence/pilot-prep/native-linux-container-drill.json` with `"result": "pass"`.
**Fail-closed check:** if the probe prints anything but `True`, the drill must FAIL (line 81) —
that is G14 evidence about this host's Docker/seccomp, not a boundary bypass.

```sh
make bootstrap                          # once; creates section venvs
cd section5 && ./.venv/bin/python -m pytest -q \
  tests/unit/test_landlock_read_allowlist.py tests/unit/test_landlock_read_isolation.py \
  tests/unit/test_landlock_confinement.py tests/unit/test_landlock_probe_selection.py \
  tests/unit/test_egress_proxy.py tests/unit/test_egress_allowlist.py
```
**Pass:** all green on Linux (the Landlock halves actually execute here; on macOS they skip —
`STATUS.md:205-208`). The two October regressions are
`test_exec_read_roots_never_grant_the_filesystem_root` and
`test_home_root_never_grants_the_filesystem_root`.

### 7.2 Linux host — live stack drills (owner-approved; compose profile `worker`)

```sh
docker compose --env-file .env -f deploy/docker-compose.yml --profile worker up -d --build
section4/.venv/bin/python scripts/e2e/m07_daemon_drill.py        # topology + worker inspect
section4/.venv/bin/python scripts/e2e/m03_scope_drill.py         # local Ollama default; paid needs HARNESS_ALLOW_PAID_SMOKE=1
section4/.venv/bin/python scripts/e2e/w21_native_confinement_drill.py --mode container
python3 scripts/e2e/w4_credential_leak_drill.py                  # needs real .env secrets + stack
python3 scripts/e2e/w22_provider_egress_drill.py                 # relay outage -> blocked
python3 scripts/e2e/w22_daemon_kill_drill.py                     # SIGKILL daemon -> orphan reaped
python3 scripts/e2e/w22_daemon_kill_drill.py --orchestrator-restart
```
**Pass criteria:** m07 — worker non-root, read-only root, no caps, internal network, forbidden
file ro, fixture fixed on disk, orchestrator socket-less; m03 — `outsideWritten=false`,
`outsideScopeUnchanged=true` (disk verdict, not model wording); w21 — every `refused` case leaves
no byte and no canary, controls allowed; w4 — `verdict=PASS`, `workerEnvDumped=true`, `leaks=[]`;
w22 egress — run settles `blocked`; w22 kill — orphan stopped within the drill's window,
`statusTruthful=true`, no leftovers.

### 7.3 Linux host — new P-04 drills (write the scripts first; §6.2 items 2-5)

- Dedicated-worker `DockerSandboxRunner` drill: a host where the runner process shares the
  Docker host's filesystem (G5), `SANDBOX_USE_DOCKER=true`,
  `SANDBOX_DEFAULT_DOCKER_IMAGE=<reviewed, digest-pinned, pre-pulled>`. Pass: task create/exec
  work; `network_mode=none` proven by a failed dial AND failed external DNS; forbidden path
  reads empty; destroy confirmed; with `HARNESS_SANDBOX_ALLOW_NETWORK=1` the 403 matrix holds
  and the sibling/3129 probes (G4) are recorded either way.
- G1 negative test: `LocalSandboxRunner(confinement="landlock", allow_network=False)` exec
  `python3 -c "import socket; socket.create_connection(('example.com',443),5)"` — record whether
  it connects (expected: yes today). This is gap evidence, not a pass/fail gate.
- G2 negative test (BYO): register a runner with `confinement=process` on the Linux host, run a
  gate command reading `/proc/<runner pid>/environ` and a `~/.ssh` canary; record outcomes.
- w17 cleanup drill on the native host (not inside the compose sandbox container unless its
  Landlock probe passes): grandchild dead after timeout, confinement really Landlock.
- BYO remote-host loop: `w13_runner_drill.py` + `w13_exec_drill.py` against the deployed stack
  from a *separate* Linux host (A-05 remote proof).

### 7.4 macOS where applicable

```sh
cd section5 && ./.venv/bin/python -m pytest -q tests/unit/test_confinement.py \
  tests/unit/test_shell_gate_real_boundary.py
section4/.venv/bin/python scripts/e2e/w21_native_confinement_drill.py --mode off   # documents the no-boundary baseline
```
**Pass:** real `sandbox-exec` profile tests green; sibling workspace neither readable nor
writable; shared system temp not writable. macOS cannot produce Landlock evidence — do not
substitute Seatbelt results for the Linux acceptance.

### 7.5 Recording

Append to `docs/briefs/evidence/90day/week-02.md`: per-drill rows (revision, image digest, kernel,
verdict, evidence path), the G1/G2/G4 probe outcomes, and an explicit list of claims that remain
`Implemented—unverified`. Any hole found: fix or **risk-accepted by name** (P-04 acceptance,
`90-day-execution-plan-2026-11-11.md:53`). Do not mark P-04 done until the ledger entry exists.

---

*End of review. One document produced; no code, tests, containers or commits were touched.*