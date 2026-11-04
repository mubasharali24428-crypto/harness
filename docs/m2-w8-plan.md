# Marathon 2.0 W8 — parallel isolation, overlap, cancellation, quotas (M2-W8)

Follows the 2026-10-28 Marathon 2.0 roadmap, the W8 row, and
[the W7 outcome](m2-w7-plan.md). The historical W8 evidence (three concurrent
runs, crash cleanup) is in `docs/briefs/evidence/pilot-prep/w8-*.json` and the
original plan in [week-8-plan.md](week-8-plan.md).

Governing rule: *an unchecked gate stays open when a dependent week begins.*
W7 closed the LSP and editor paths; W8 expands the concurrency proof to
overlapping results, explicit cancellation and subagent quotas — and does not
claim universal parallel isolation.

## Purpose

W8 asks for:

1. **overlapping edits and conflict reporting** across concurrent runs;
2. **cancellation and cleanup** that preserves neighbours;
3. **subagent quotas**;
4. a **re-run of the task subset against the baseline**;
5. no shared writable state, no checkpoint collision, no silent overwrite.

## Audit (2026-10-28)

| Area | Observation | Where |
|---|---|---|
| Three-run isolation | live proof exists (W8 historical): distinct networks, workspaces, checkpoints | `scripts/e2e/w8_concurrency_drill.py` |
| Crash cleanup | live proof exists: orphan network/containers reclaimed, neighbour untouched | `scripts/e2e/w8_crash_cleanup_drill.py` |
| Overlapping results | **not tested.** Each run is reviewable, but two runs producing the same path were never exercised | — |
| Apply safety | the **editor** refuses a file that changed after the diff (`run.ts`, tested); the **CLI apply copied with no precondition** | `clients/python/harness_sdk/cli/local.py`, `clients/vscode/src/core/run.ts` |
| Cancellation | `/stop` is terminal and releases the claim; no drill asserted stop + neighbour preservation together | `section4/.../api.py`, `service.stop_agent` |
| Subagent quotas | **enforced** server-side (depth, live children, run pool, budget, scope subset) with CAS; the engine **does not call these endpoints yet** | `section4/.../agent_tree.py` |
| Approval of an agent | never self-service: a second admin must approve | identity `agents.py` (`SELF_APPROVAL`) |

The unproven assumption was that "each run is isolated" was enough. It is not:
isolation says nothing about what happens when two results meet the same file.

## What changed

### Hash-anchored apply (`clients/python/.../cli/local.py`, `deploy/runners/local_engine.py`)

`run_local` now reports `baseHashes` — the sha256 each named path had in the
pristine copy of the workspace. `apply_back` accepts that map and checks every
named path **before writing anything**:

* equal → the file is written (or deleted) as reviewed;
* different → `ApplyConflict` (`APPLY_CONFLICT`, exit 1) naming each path with
  the digest it expected and the digest it found;
* all-or-nothing: one conflict means no file is touched.

A new path has an expected digest of `None`, so a creation collides with any
existing file. A stale deletion is refused while the file survives. The editor
path already had this rule (compare the file at apply time to the bytes the diff
was shown against); the CLI now matches it.

### Overlap drill (`scripts/e2e/w8_overlap_drill.py` + `w8_overlap_probe.py`)

Deterministic, no model: two "runs" produce `shared.txt` from one base; the
first applies, the second conflicts, a re-apply conflicts, a new-file collision
conflicts, and a stale deletion is refused. 14 checks.

### Live concurrency drill, expanded

The three-run drill now also:

* makes **two runs produce the same path** (`w8_shared.txt`, different bytes);
* asserts their **diff digests and checkpoint bytes differ**, and that no run's
  checkpoint manifest contains another run's file;
* **approves** run A's change, then presents A's digest against run B's change
  for the same path — `409 CHANGE_STALE` — and then approves B with its own
  digest;
* starts a **fourth run, confirms it executing beside three executing
  neighbours, and stops it**; waits (bounded) for `cancelConfirmed`, then
  checks the claim is gone and the boundary network is removed;
* checks the neighbours were executing at the stop and finished with their
  recorded changes intact.

### Subagent quota drill (`scripts/e2e/w8_agents_quota_drill.py`)

Registers an agent definition (depth 1, one live child) as admin A, approves it
as a **second** admin B (the invite → redeem → team-login path, because
`SELF_APPROVAL` is refused), then:

* spawns a root and one child (201);
* a second child → `409 SPAWN_CHILDREN_EXCEEDED`;
* a grandchild → `409 SPAWN_DEPTH_EXCEEDED`;
* a child asking for a tool its parent does not hold → `403 SCOPE_EXCEEDS_PARENT`;
* cancels the root → both assignments `cancelled`, run-pool reservation back to 0.

No model is called and no agent process starts: this is the gate that exists.

## Outcome (2026-10-28)

**Concurrency, live: 22/22 checks PASS.**
`pilot-prep/w8-concurrency-1793200473.json` — max 4 executing, three worker
containers, three distinct networks and run directories, A/B `w8_shared.txt`
digests and checkpoint hashes distinct, cross-run digest refused
(`409 CHANGE_STALE`), both overlap changes approved independently. The cancelled
run was executing beside three executing neighbours; stop confirmed in **6.7 s**,
`lifecycle = {phase: stopped, cancelRequested: true, cancelConfirmed: true,
mutationPossible: false}`, claim released, boundary network gone, neighbours
settled with their changes.

**Crash cleanup, live: 11/11 PASS.**
`pilot-prep/w8-crash-cleanup-1793200243.json` — the victim settled (`blocked`),
its claim released, the launcher-shaped orphan's network and running container
reclaimed, the live neighbour held and untouched, the victim's boundary gone.

**Overlap apply, deterministic: 14/14 PASS.**
`eval/w8-overlap-1793198798.json` — first result applied byte-for-byte, second
refused with `expected` = base digest and `actual` = first result's digest, no
silent overwrite, all-or-nothing, re-apply refused, new-file collision refused,
stale deletion refused.

**Subagent quotas: 9/9 PASS.** `eval/w8-agent-quotas-1793200199.json` — depth,
live-children, scope-inheritance refusals as above; cancel cascade; pool
reservation zero.

**Baseline subset: 2/2 PASS, `revisionVerified: true`.**
`eval/baseline-20261028T151158Z.json` (`w8-apply-conflict` 0.53 s,
`w8-agent-quotas` 1.45 s). Corpus is now 33 tasks (23 implemented).

**Tests.** `clients/python` 88 passed (six new apply-anchor tests);
`clients/vscode` 29 passed (unchanged; its stale check pre-existed).

## Findings

1. **The CLI apply could silently overwrite.** The engine's diff is computed
   against a copy; nothing compared the live file to that copy before writing.
   Fixed with the hash anchor and an all-or-nothing preflight.
2. **A stopped run's `state` is `done`.** `stop_agent` finishes the state
   machine, so `/status.state` cannot distinguish "completed" from "stopped";
   only `lifecycle.phase`/`cancelConfirmed` can. Recorded, not changed: the
   lifecycle block is the operator-facing truth, and the drill now asserts it.
   A consumer reading `state` alone would misread a cancelled run.
3. **Cancellation is not instantaneous.** The first revision read the claim and
   network immediately after the state settled and saw `stopping` with a live
   claim and network. The drill now waits, bounded, for `cancelConfirmed`
   (6.7 s in this run) — an observation about the engine's unwind, not a defect.
4. **Agent approval needs a second admin.** The drill had to invite a second
   user, redeem the invite, and sign in to the team explicitly; a solo admin
   cannot approve their own agent version. That is the intended gate, and the
   quota drill crosses it honestly.
5. **Quota endpoints are not called by the engine.** `agent_tree.py` states it:
   they record and gate, they do not start or stop agent processes. W8 proves
   the gate, not delegation.

## Gate position

| W8 exit claim | State |
|---|---|
| No shared writable state | **pass** — distinct workspaces, networks and mounts in three concurrent runs |
| No checkpoint collision | **pass** — per-run manifests; no foreign file; shared path hashes differ |
| No silent overwrite | **pass** — CLI apply is hash-anchored (tests + drill); editor already refused stale files |
| Stale edits fail | **pass** — W3 plan/version CAS, and the apply conflict for file bytes |
| Cancellation/cleanup preserves neighbours | **pass** — live stop with three executing neighbours; crash sweep 11/11 |
| Rerun task subset against baseline | **pass** — 2/2, `revisionVerified: true` |
| Subagent quotas | **pass at the API**; the engine does not call the endpoints yet |
| Universal parallel isolation | **not claimed** — one host, one stack, n=1 per scenario |

## Limitations

- **n=1 per live scenario.** These are existence proofs, not rates; a single
  pass says nothing about behaviour under load (W17).
- **Overlap is proven for one path produced by two runs.** A same-file,
  different-hunk merge is not attempted: each run's result is all-or-nothing,
  and the second is refused rather than merged.
- **The apply anchor covers named paths only.** A file the run never named is
  never written — by design — but the anchor does not watch it either.
- **`APPLY_CONFLICT` is new wire surface** for the CLI error contract
  (`code: APPLY_CONFLICT` with `error.conflicts[]`); consumers that switch on
  `CLI_ERROR` must add it.
- **Quota gate ≠ delegation.** Nothing starts an agent process; the engine's
  use of these endpoints (and any per-agent workspace isolation) is unbuilt.
- **The crash drill covers one failure mode**: a SIGKILLed launcher/`docker run`
  client, plus the product's own settle path. Host loss, Docker daemon loss and
  partial writes are not simulated.
- **Stopped runs look `done` to `/status.state`.** Until a consumer reads
  `lifecycle`, a cancellation can be misread as a success.
- Live drills (`w8-concurrency`, `w8-crash-cleanup`) cost model runs and are not
  in the runner's default infra subset; their evidence is written by the drills.

## Carry-over

- **W9:** artifact authorization, preview/share and deployment approval.
- **W12:** DB-backed skill catalog (skills remain advisory-only).
- **W17:** load, saturation and backpressure for runs, workers and agent quotas.
- **Engine delegation:** wire subagent spawn/cancel to the engine behind the
  same gate, with per-agent workspace isolation, before any delegation claim.
- **M01:** native-tool confinement still open; nothing here widened access.
- **W11:** current-revision re-run still owed for the live vision task.
