# Week 1 plan — make the agent able to see, and let us watch it

From `docs/capability-gap-2026-10-25.md`. One theme: **the runtime we ship is
already more capable than the profile we run it with.** The work includes
security review, a versioned event contract, worker transport and an attachment
path. It is larger than a configuration flip or one callback. The ordered
packages and provisional estimates in [the marathon execution plan](marathon-execution-plan.md)
govern scheduling; the day labels below are the original sequence, not a promise
that every package fits one week.

## Day 0 — historical incident and landed fix

The original incident below was found while writing the gap list. Its source
fix is recorded in the outcome section; current deployment health still needs
fresh verification.

- `harness-sandbox-egress` crash-loops: *"Refusing to start: set
  HARNESS_MASTER_KEY…"*. The shared env block passes
  `HARNESS_MASTER_KEY` (`deploy/docker-compose.yml:28`) but the egress service
  (`:408`) gets neither it nor `HARNESS_ALLOW_DEV_MASTER_KEY`, which two other
  services do set (`:1183`, `:1204`).
- Consequence: `harness-sandbox` answers **503 on `/ready`** and has failed its
  healthcheck 708 times in a row. No live run can execute on this stack. The
  demo replay path hides it, which is exactly how it stayed unnoticed for hours.

Work: give the egress proxy the env it needs — or, better, drop the
master-key requirement for a component that handles no secrets and assert that
in a test — then find out why sandbox readiness depends on egress at all and
decide whether that coupling is right. Add a compose-level smoke test that
fails when any service is `unhealthy`, so "broken but the demo still plays"
cannot recur. **~0.5 day, and it is the highest-priority item in the week.**

### Day 0 outcome (2026-10-25)

The env hypothesis was wrong, and the real cause was worse: `entrypoint.sh`
never read `"$@"`, so the egress sidecar ignored its own `command:` and booted
the all-in-one branch — seven uvicorns plus a migrate job in a 128MB
container — then died on the master-key gate. The proxy handles no secrets, so
the fix is not to give it a key but to let the container run its command:
`deploy/entrypoint.sh` now execs a passed command before the `HARNESS_SERVICE`
branch. `sandbox-egress` gained a healthcheck that proves policy (a denied
`CONNECT` must answer 403) rather than liveness.

Readiness does **not** depend on egress, and that is now a decision rather than
an accident: a run needing no network must execute while the proxy is down, so
`/ready` still gates only on db + schema drift, and reports `egress: up|down`
as information — for the sandbox app only, since every backend container
receives the proxy URL and identity was reporting it too.

`scripts/e2e/compose_journey.py` check 0 now fails the journey if any service
this stack runs is missing, stopped, restarting or unhealthy. Its first version
read `docker compose ps` without `-a` and passed while a stopped sidecar was
invisible; the trip test caught that, and the check now reads `ps -a` and
enumerates enabled services via `compose config --services`.

Live evidence: all nine backend containers healthy including
`sandbox-egress`; `CONNECT example.com:443 → 403`, `CONNECT github.com:443 →
200`; sandbox `/ready` reports `egress: up`, identity reports no egress field;
stopping the sidecar makes the new check report it, restarting clears it.

Separately: the stack was ALSO down for a second reason — the database had
migration `0062_team_model_prefs` applied while the running image declared 60,
so identity/github/review/meeting/webhook-delivery were crash-looping on the
schema-drift gate. A rebuild from the current tree clears it; the full build is
currently blocked by a 60MB runtime wheel at ~28 kB/s, so verification used a
source-overlay image over the previous build.

## Days 1–2 — the agent can read and write files properly (A1, A2)

- Flip `tool-fs` to `read: true, write: true, edit: true` in the profile patch
  we inject (`engine._execution_policy_patch`, `engine.py:61-100`). Today all
  three are false, verified in the runtime binary.
- **Gate:** before flipping, confirm `dsh-tool-fs` honours the same workspace
  scope our shell gate enforces. If it does not, this is a security change and
  it stops here until it does. That check is the day's real work, not the flip.
- Add glob/grep as first-class tools (the runtime ships `…-rg` binaries next to
  the runtime already; the gate today forces `grep`/`rg` through bash).
- Tests: a confined run writes inside the workspace and is refused outside it;
  reads of a secret path still fail; the Landlock boundary is asserted, not
  assumed.
- Expected effect: fewer tool calls per task, no more `python -c` write
  smuggling (which currently bypasses the path scope check entirely —
  `shell_gate.py:55-58`).

## Days 2–4 — watch it work (C1, live transcript)

- Pass the SDK's `on_notification` callback into `harness.run()` — the
  signature is `run(input, *, session_id, on_notification)` and we currently
  pass nothing, which is why tool calls only appear folded into the final
  result (`engine.py:520`).
- Emit each `tool/call` / `tool/result` as it happens, and render it: one row
  per call with tool, target, duration, outcome, collapsible output, plus a
  running token/cost/elapsed footer.
- Land a versioned `tool_call` / `tool_result` contract before streaming
  consumers, with generated mirrors, mocks and golden fixtures in the same
  change. Trace notification delivery across the isolated worker boundary;
  adding a callback to the direct SDK call alone does not complete streaming.
- Tests: reducer folds a call/result pair into one row; denial appears as an
  error row, not a silent gap; reconnect replays without duplicating.

## Day 4 — stop crippling the context (B4, B6)

- Design safe history provisioning before changing the `.git` exclusion
  (`engine.py:194-198`): inherited hooks, credential-bearing config and gitdir
  references outside the workspace must not enter the run.
- Remove the hardcoded `max_tokens=4096` (`engine.py:602`) in favour of a
  per-model value, and surface what the runtime already knows: the profile
  defaults `defaultContextWindow` to 1M via `DSH_CONTEXT_WINDOW`, and
  `compaction-basic` + `image-offload` are enabled. We currently show none of
  it and cap answers at a number we chose in a hurry. Validate limits against
  the selected provider/model; the profile default is not proof of a 1M window.

## Day 5 — a picture is worth the whole debug session (A6)

- `readImage` is already `true` in the profile and the runtime has a real
  attachment store with base64 delivery. What's missing is ours: a goal can't
  carry an image. Add the attachment path (upload → artifact → content block in
  the goal) so "fix this UI, here's the screenshot" works.
- Small, visible, and it makes the browser gap (A4) tolerable for a while.

## Boundary work required before widening tool access

- S2: the `python -c` scope bypass needs enforcement of approved scope, including
  writes inside the workspace but outside the allowed subdirectory. Whole-workspace
  OS confinement does not establish this narrower boundary. Keep new tools
  disabled until the boundary is demonstrated; parsing arbitrary code strings
  or documenting the limitation does not close the gap.
- S1: say in the docs that Landlock ABI v1 has no network hook, so on Linux the
  container is the network boundary (`confinement.py:440`).

Copy buttons on run id / sha / error / diff remain optional polish (audit item
16, originally estimated at ~2 hours).

## Explicitly not this week

Interrupt/steering (C2/C3 — the SDK has no cancel, needs a design), browser
control (A4), CLI or IDE surfaces (D1/D2), codebase index (B2), MCP client
mount (E1 — capability exists upstream, but it needs an allow-list review we
should not rush), and Batch B of the UI audit.

## What "done" looks like on Friday

A live run executes end to end on the dev stack with the sandbox healthy; the
agent reads and edits files with real tools instead of shell smuggling; you can
watch each tool call stream with its outcome and a running cost; a screenshot
attached to a goal reaches the model; and a compose smoke test would have
caught the egress outage on the day it started.

### Days 1–2 outcome — files, output ceiling, `.git`

`sdk-minimal` ships `tool-fs` with read/write/edit off; the patch now enables
it and `tool-fs-search` (glob/grep). The safety argument is not "the tool is
scoped to the workspace" alone, because a plan's `forbiddenPaths` live inside
that workspace and the tool does not know them: writes stay off unless the
confinement can freeze those paths (container read-only mounts, Seatbelt).
Landlock and `none` therefore get read-only file access, and the patch records
why. `max_tokens=4096` is gone (`HARNESS_SDK_MAX_TOKENS` pins a cap), and the
workspace seed keeps `.git`.

Still open from Days 1–2: the live end-to-end gate (M07).

### Days 2–4 outcome — the transcript (2026-10-26)

`execute_goal(..., on_progress=)` exists on both execution paths: the direct
SDK call passes `on_notification`, and the isolated worker turns turn events
into NDJSON `progress` lines that the parent reads. The service forwards them
from the worker thread through a queue onto the bus; `ToolActivity` pairs each
result with its call by `callId` and renders output collapsed.

The contract landed first, as this plan required: `1.0.0rc3` adds `tool_call`,
`tool_result` and `context_compacted`, with golden payloads and a regenerated
TS mirror, and the tests that pin the event set were updated in the same change.

Context is visible too: the state machine records the window at execution
start, compaction is summed from the durable log, and the status panel shows
it. What is still not verified: reconnect/replay deduplication against a real
browser session (the reducer is tested; the browser round-trip is not), and the
running cost footer.

One correction to the plan text above: `on_notification` is not passed for
every engine. The wrapper probes whether `execute_goal` accepts `on_progress`
and passes it only then — 32 section-4 tests failed when it was assumed, which
is the check that now guards it.

### Day 5 outcome — the screenshot path (2026-10-26)

The runtime did accept images all along: `run()` takes a content-block list, and
the bundled runtime admits `{"type":"image","data":<canonical base64>,
"mimeType":…}` for png/jpeg/webp/gif, then rewrites it into its own attachment
store. What was missing was ours, exactly as this plan said.

Shipped: the composer holds a picked screenshot, stores it through the existing
artifact route once approve mints the run id, and the engine attaches it at
execution. Limits are enforced twice (ours, then the runtime's), a
non-vision-configured model is refused before the call rather than billed,
`HARNESS_GOAL_IMAGES=0` disables the path, and contracts `1.0.0rc4` carries a
`goal_images` event so the run page can say what the model saw and what was
declined — including a resumed attempt not re-attaching the same picture.

Still open here, stated plainly:

- One image per goal in the UI; the backend accepts three.
- The upload lands between approve and start, so a run started from another
  client in that window goes text-only. Reported as `requested: 0`, not hidden.
- The cross-replica resume case re-attaches: the "already sent" marker is
  per-process, because nothing in the frozen schema records what a session saw.
- ~~No live acceptance evidence~~ — drilled 2026-10-26 on OpenRouter: with the
  transport patch declaring image input, `qwen/qwen3.7-flash` read the attached
  PNG and answered `(220, 40, 40)`. With an `[text]`-only declaration the same run
reported
  "image omitted because this model accepts text only", which is the modality
  gate working. Evidence: `docs/briefs/evidence/pilot-prep/m07-openrouter-smoke.json`.
  Every one of those runs used `isolated: false`.

### The isolated worker cannot be evidenced on this host (2026-10-26)

Re-running the same smoke with `--isolated` against local Ollama (free, no
provider spend) returned `status: error` in **0.0 s, zero tool calls, zero
tokens** — it never reached the model:

> OS confinement is unavailable for the real DeepSeek runtime (seatbelt breaks
> its PTY); set `HARNESS_ENGINE_CONFINEMENT=off` explicitly to accept an
> unconfined worker, or run it inside a container/VM boundary

Evidence: `docs/briefs/evidence/pilot-prep/m07-ollama-isolated-refused.json`.

This is the guard behaving correctly, not a fault to route around. The
temptation is to set `HARNESS_ENGINE_CONFINEMENT=off`, watch the run go green,
and book it as M07 evidence — which would prove only that an unconfined worker
works, the one thing the week's whole argument says must not be claimed. So the
flag was not set and the run is recorded as a refusal.

Consequence for the plan, stated plainly:

- **M04's "both execution paths" is live-verified on one path.** The direct SDK
  call streams; the isolated worker's NDJSON `progress` transport is covered by
  unit tests only. It needs either Docker (a container boundary, currently not
  running on this machine) or a native Linux host.
- **M07's acceptance run is therefore still open**, independent of the
  screenshot evidence above. The real stack's worker is the isolated one.
- The same host limitation is why Landlock write evidence needs
  ubuntu-latest rather than this machine.

### A displayed number that is not the model's (found while reading that evidence)

`state_machine._context_window()` reports `DSH_CONTEXT_WINDOW`, default
`1000000`, as the run's context window. The OpenRouter transport patch declares
`contextWindow: 65536` for the model actually in use, and the status panel still
showed 1,000,000 in both drills. The panel is displaying a profile default and
calling it the window — precisely the "the profile default is not proof of a 1M
window" line from Day 4, now with a measured counterexample.

Not fixed at the time of writing: the model's real window lives in the runtime's
patched provider config, which the engine does not currently read, and guessing
at it would replace one wrong number with another. **Closed later the same day in
`a5148e0`** — `configured_context_window()` reports a window only when an
operator actually set one, rejects empty and non-positive values, and returns
`None` otherwise, which reaches the `StatusPanel` branch that prints "window
unknown" and had never been reachable. The three duplicated readings were
collapsed into that one helper so the run page and the durable
`context_compacted` rows cannot drift. What remains is provider-verified limits
rather than operator-asserted ones — W2 work, not W1.

## M00 outcome — the stack re-verified on a current image (2026-10-26)

M00 stayed open because **no image on this host contained the week's code**.
`harness-backend:vdev-local` and `vbuild-1025` were both inspected first: neither
has `goal_images.py`, and `engine.py` in them has zero `on_progress`. Verifying
against either would have re-reported an old revision as current health — the
precise failure this plan forbids. So `harness-backend:vm00-1026` and
`harness-frontend:vm00-1026` were built from `df4096e`.

Evidence: `docs/briefs/evidence/pilot-prep/m00-stack-verification-2026-10-26.json`.

- **Migrations:** 61 in tree, 61 in image, 61 applied, `migrate` exit 0. The
  schema-drift crash class from Day 0 did not recur.
- **Sidecar startup:** `sandbox-egress` runs its own `command:` on the current
  image, not the all-in-one fallback.
- **Proxy enforcement:** `CONNECT github.com:443 → 200`,
  `CONNECT example.com:443 → 403`, identical after recovery. The 403 probe *is*
  the healthcheck, so a listener that stops enforcing reads unhealthy.
- **Readiness:** sandbox `/ready` reports `egress: up` as information only.
- **Journey:** 13 checks green, 18 containers running/healthy.
- **Trip test:** stopping the sidecar produced `[FAIL] container health`;
  restarting cleared it.

Two caveats, kept in the record:

1. The **first** trip attempt PASSED with the sidecar stopped, because the
   journey read `docker ps -a` before the daemon reflected the exit. A
   settle-window race in the trip test, not a hole in check 0 — three seconds
   later it caught it. Backlogged.
2. **Check 07 still says `replayed`, not live.** The journey proves the stack;
   it does not prove a live agent run. M00 closing does not move M07.

## M04 outcome — the confined worker really does stream (2026-10-26)

The week's central claim, previously unit-tested only, is now live. A real
worker in a real `HARNESS_ENGINE_CONFINEMENT=container` boundary, on a worker
image built from this tree (`harness-engine-worker:m04-1026`), against local
Ollama — **zero spend**.

Evidence: `docs/briefs/evidence/pilot-prep/m04-container-streaming.json`.

- Run completed at **68.42 s**. Progress records arrived at **34.85 s** and
  **48.89 s** — before completion, so they cannot have come from the post-run
  sweep.
- **2 calls and 2 results**, each result paired to a `callId` whose call arrived
  first: `read` at 34.85 s with its result 60 ms later at 34.91 s, and `write` at
  48.89 s with its result at 48.92 s.
- Both tools are the **native file tools**, so the enablement in `8438b19` runs
  on the real worker rather than only under test doubles — and the agent read
  before it wrote, which is the behaviour the shell-smuggling path could not
  show.
- Real side effect on disk: `m04-70570/notes.txt` contains exactly
  `m04 streaming`.

Three findings this produced that are worth more than the green:

1. **`DSH_PATCH_FILES` does not survive the boundary.** The earlier smoke wrote
   its patch into the *host* temp dir, so the confined worker died with
   `DSH_PATCH_FILES entry not found` before any model call. `DSH_`-prefixed
   variables are allowlisted across `confine.scrubbed_env`, so the value
   traveled — the file did not. Fixed properly rather than worked around:
   `deploy/patches/openrouter-vision.patch.yml` is committed and baked into the
   worker image, which makes the modality declaration a reviewed artifact
   instead of something a script synthesises at run time.
2. **The refusal message is misleading.** It names Seatbelt even on a Linux
   kernel, because it is a fixed string for "not container and not off". It
   sent a reader looking for macOS-specific code when the actual rule is mode
   selection. Worth rewording.
3. **`contextWindow` came back `null` on this path**, not 1,000,000. The honest
   unknown is already reachable — which is the behavior the flagged defect in
   *A displayed number* should adopt everywhere, not just here.

Still open on M04: reconnect/replay deduplication against a real browser session,
the running cost footer, and `usage`, which stayed `null` on this transport.

## M07 outcome — failed, and the failure is the useful part (2026-10-26)

Same boundary, same image, a real screenshot attached, and the cheapest vision
model available (`qwen/qwen3.7-flash`). It did **not** work.

Evidence: `docs/briefs/evidence/pilot-prep/m07-confined-vision-failure.json`.

- **The image did cross the boundary** — `goalImagesAttached: 1`, the vision patch
  resolved from inside the worker image. So M06's plumbing is reachable in the
  confined path.
- **The run never produced the colour.** 37 tool calls, **all `bash`**, 516.75 s,
  ending in `model qwen/qwen3.7-flash returned an empty response` at the deadline.
  No `color.txt`.
- **Streaming passed again**: 74 records, all 37 call/result pairs correlated by
  `callId`, first arrival at 13.45 s against a 516.75 s completion.

Three findings worth more than a green would have been:

1. **The W2 loop-detection gap was reached in anger, not in a fixture.** A stuck
   run burned 37 calls to a wall-clock deadline with nothing to stop it. W2's
   "loop fixture stops within explicit budget" is no longer hypothetical.
2. **Tool choice changed between two runs of the same image under the same
   `tool-fs` policy**: local `qwen-local` used native `read`/`write`;
   `qwen3.7-flash` used `bash` 37 times and never touched the file tools. Any
   claim that file tools are now the agent's default needs to be model-aware.
3. **The metering gap stopped being academic.** `usage` is `null` on the
   openai-completions transport, so after 37 paid calls **the cost cannot be
   stated from evidence** — only bounded roughly by price × tokens. A runaway
   loop you cannot price is the worst combination available, and it is the one
   the plan's "actual usage distinguished from estimates" line was warning about.

No retry was made. The script's own discipline is one attempt; re-running a
runaway paid loop to obtain a nicer picture would have spent again and still
could not have been priced.

**M07 remains open.** Its gate needs a real task that reads, edits *and verifies*
a fixture repo, with the screenshot answered and the evidence visible — and, before
that is worth attempting, some way to bound the loop and price the call.





## M01 outcome — the approved-scope boundary does not exist (2026-10-26)

Measured by `scripts/m01_confinement_spike.py`, which drives the real
`shell_gate` the way the plugin does (JSON in, JSON out) against disposable
fixture workspaces. Evidence:
`docs/briefs/evidence/pilot-prep/m01-confinement-spike.json`. Intent for the run:
`scopePaths=["src"]`.

**The answer to the question Week 1 was built on: no.** Writes inside the
workspace but outside the approved scope are **permitted**, three ways over —
`open('docs/leak.txt','w')`, a concatenation `'do'+'cs/leak2.txt'`, and
`os.path.join('docs','leak3.txt')`. All three landed on disk.

Why, which is more interesting than the result: `scope_violation` skips code
strings (`shell_gate.py:55-58`), and a code string is the **only** write vector
the policy leaves. Redirection `<`/`>` is banned outright (`policy.py:79`) and
`cp`/`touch`/`mv` are not in `ALLOWLIST_BINARIES`; the allow-list's sole
file-writing member is `python3`. So the one path an agent has to write a file is
exactly the one the intent check does not read. The gap is structural, not an
edge case.

What **does** hold, at workspace granularity — all refused by the OS, with no
file left behind:

| case | result |
|---|---|
| `escape.txt` → symlink out of the workspace | `PermissionError` |
| `../outside/climbed.txt` | `PermissionError` |
| absolute path outside the workspace | `PermissionError` |
| control write inside `src/` | allowed |

Second finding, from the axis that adds a forbidden path: when `app.conf` is
forbidden and no read-only mount is available, `unprotected_forbidden` refuses
**every** command — including the control. Correct fail-closed behaviour, and the
reason the first version of this spike reported nine identical denials and a
green summary while testing nothing. The two constraints had to be measured apart
to say anything at all. It also means the shell tool is not "partially scoped" on
this backend; it is unavailable.

Honest limits on this spike:

- **Landlock was not measured.** Docker Desktop's linuxkit kernel
  (`6.12.76-linuxkit`) answers `ENOSYS` for `landlock_create_ruleset`, and this
  machine has no Linux VM (`multipass`/`lima`/`colima` all absent). The plan
  asks for "each supported enforcement backend"; this covers Seatbelt only.
  The Landlock column needs a Linux runner.
- **The native file tools are untested.** `tool-fs` read/write/edit run in the
  engine worker, not through the shell gate. The confined worker now runs
  (M04 evidence), so this half is doable — it is not done.

Consequence for M03, stated as the plan puts it: *"If intent restrictions cannot
be enforced by the native SDK policy, keep those tools disabled and implement
mediation or OS/mount restrictions covering the approved scope."* Parsing did not
fail here; there was nothing to parse against. M03 therefore needs mount-level
enforcement of the scope subtree (the per-run container already mounts the
workspace — a second read-only bind over the out-of-scope paths, or a read-only
bind of `src/` alone, is the shape that would actually hold), not a better
string matcher.

## M07 blocked on topology, not on the model (2026-10-26)

Two live acceptance attempts through the confined worker were run, and both
thrashed: 14 and 37 call/result pairs against the same "report the colour of this
screenshot" goal, each ending at the wall clock, one with
`model … returned an empty response`. Streaming worked in both — every record
arrived before completion, so this is not the transcript failing again.

Investigating where the boundary actually exists turned up the reason M07 cannot
pass as the stack is composed:

- `harness-orchestrator-1` has **no `/var/run/docker.sock` and no `docker` CLI**,
  and `deploy/docker-compose.yml` never mounts one. That is deliberate —
  `container_runtime.py`'s own header says "Docker socket stays on host".
- `container` is one of only two modes the real runtime accepts (the other being
  an explicit `off`), and it is the only one that is not a safety compromise.
- `HARNESS_ENGINE_ISOLATED`, `HARNESS_ENGINE_CONFINEMENT` and
  `HARNESS_ENGINE_IMAGE` are set **nowhere** — not in `.env`, not in
  `.env.example`, not in the compose file. The orchestrator container carries only
  `ENGINE_LIVE_MODE=live`.

So the deployed orchestrator cannot create a confined worker at all: with
isolation unset it runs in-process, and if isolation is set, `auto` resolves to
`none` on Linux and the worker refuses. Meanwhile the runs that *do* work —
including both M04 and these M07 attempts — are host processes with the host's
Docker CLI. **The evidence this week collected for the isolated worker was
therefore produced on a topology the product does not deploy.**

That is STATUS.md's remaining blocker "a reviewed dedicated sandbox worker/daemon",
now with a mechanism instead of a label. It needs a decision between:

1. a **worker daemon** that owns the boundary and receives work from the
   orchestrator over a socket — the reviewed thing the pilot checklist already
   asks for;
2. **mounting the Docker socket** into the orchestrator, which contradicts the
   documented design and hands the API a root-equivalent control channel; or
3. accepting **`off`** in the stack and saying so in the UI — which the roadmap's
   own gate forbids, since confinement is the product.

Option 1 is the only one consistent with everything else in this plan. Not
chosen here because it is architecture, not a fix, and because M07 is gated on
M03 — which the spike above just showed cannot be met by the current design
either. Recording M07 as **blocked with cause**, not as failed, and not as passed.
