# Week 1 plan — make the agent able to see, and let us watch it

From `docs/capability-gap-2026-10-25.md`. One theme: **the runtime we ship is
already more capable than the profile we run it with.** Week 1 is configuration,
one callback and one attachment path — no new subsystems. Everything here is
reversible and test-gated.

## Day 0 — before anything else: live execution is broken right now

Found while writing the gap list, and it blocks the rest of the week.

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
- **Decision needed from you on day 1**, because it gates the rest: frozen
  `contracts/` has no tool event type (`contracts/ts/src/events.ts:4-19`).
  Either (a) version the contract with `tool_call` / `tool_result`, or (b) ride
  `terminal_output` with a payload convention. I recommend (a); (b) rots and
  every consumer has to guess.
- Tests: reducer folds a call/result pair into one row; denial appears as an
  error row, not a silent gap; reconnect replays without duplicating.

## Day 4 — stop crippling the context (B4, B6)

- Stop stripping `.git` from the seeded workspace (`engine.py:194-198`) so the
  agent can see history and run `git diff` against reality.
- Remove the hardcoded `max_tokens=4096` (`engine.py:602`) in favour of a
  per-model value, and surface what the runtime already knows: the profile
  defaults `defaultContextWindow` to 1M via `DSH_CONTEXT_WINDOW`, and
  `compaction-basic` + `image-offload` are enabled. We currently show none of
  it and cap answers at a number we chose in a hurry.

## Day 5 — a picture is worth the whole debug session (A6)

- `readImage` is already `true` in the profile and the runtime has a real
  attachment store with base64 delivery. What's missing is ours: a goal can't
  carry an image. Add the attachment path (upload → artifact → content block in
  the goal) so "fix this UI, here's the screenshot" works.
- Small, visible, and it makes the browser gap (A4) tolerable for a while.

## Also this week if there's room

- S2: the `python -c` scope bypass — either parse the code argument or document
  plainly that OS confinement, not the gate, is the boundary.
- S1: say in the docs that Landlock ABI v1 has no network hook, so on Linux the
  container is the network boundary (`confinement.py:440`).
- Copy buttons on run id / sha / error / diff (audit item 16, ~2 hours).

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
