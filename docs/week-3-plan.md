# Week 3 plan — interrupt, steer, approval, rewind

Follows [Week 2](week-2-plan.md). Governing rule inherited from
[the marathon execution plan](marathon-execution-plan.md): *"An unchecked gate
stays open when a dependent week begins."*

## Gate

Exit evidence for W3 as written: **the worker and its child tool stop; resume
reconciles partial writes; a rejected action stays blocked; steering does not
silently expand the approved scope.** None of that is claimed yet. This document
records the first prerequisite only — the worker-control spike — because the
gate cannot be designed before it is known whether Stop actually reaches a
worker mid-command.

## W3 control spike — what is proven and what is not (2026-10-26)

`scripts/e2e/w3_stop_spike.py` drives a run over HTTP whose goal makes the agent
run `sleep 180` through the shell, waits until the worker is mid-command, then
calls `POST /stop` and times the container's death.

**Proven: the container kill works.** Across four clean measurements the run's
own worker and its relay were gone within **0.58 s – 1.01 s** of the stop call,
with no surviving processes, and the state moved `executing → done`. The kill is
attributed by the run id in the worker's mounts, not by container name — names
carry a random uuid, and an earlier pass of this drill matched *any* run
container, read a leftover from a different run as this run's survival, and
reported a leak that did not exist. The retained sample
(`w3-stop-w3stop-1793034660.json`) has no live child; one earlier sample did show
a live `/bin/sh -c python3 …time.sleep(180)` and its children at the moment of
the stop, and the worker still died in 0.58 s — but that file was overwritten by
a later inconclusive run, which is why the drill now writes **one file per run**.
An observation whose file cannot be produced is not evidence, so the live-child
case is recorded here as observed-once and *not* counted as closed.

**Not proven: that stopping kills a live tool child.** The local model does not
reliably call `bash`: of six attempts, one ran the command inside the window and
five were still thinking when the stop fired. Whether the shell child dies is a
consequence of the container dying — there is no separate channel — but the
sample count is one, not enough to state as a gate.

## Next, in order

1. **Make the child deterministic.** The spike must not depend on a model
   choosing `bash`. Drive a worker through the daemon with a shell policy and a
   goal that exercises the Section 5 gate directly, or use the stand-in worker so
   a long-running child exists by construction. Then the live-child case is a
   measurement, not a lucky sample.
2. **N01's gap closes here.** The event-stream loop guard stops a run durably but
   does not kill the worker (`docs/week-2-plan.md`, N01 outcome). With a proven
   kill path, the guard should invoke it, so a looping run stops instead of
   burning until `ENGINE_RUN_TIMEOUT_SECONDS`.
3. **Partial writes on resume.** Kill in the middle of a write, resume, and show
   what the workspace actually contains and what the checkpoint claims. This is
   the part of W3's gate with no code today.
4. **Steering and scope.** `resumeWithChanges` (W2 N04) posts guidance before
   resuming. W3 must show that guidance cannot widen approved scope — the mount
   boundary already refuses out-of-scope writes (M03), so the check is that a
   steered run still cannot write outside its mounts.
5. **A policy decision is still owed by a human:** what "rewind" means for a run
   that has already written files — revert to the last checkpoint, or leave the
   workspace and mark the run abandoned. No implementation should pick one
   silently.

## W3 outcome (2026-10-26) — one clause closed, one half, three open

| Gate clause | State | Evidence |
|---|---|---|
| The worker and its child tool stop | **half** | container kill measured 0.58–1.01 s over four samples; a live `sleep` child observed in one; the guard now calls the kill |
| Resume reconciles partial writes | **half** | resume adopts the disk (pinned); the *partial-write* case is unmeasured |
| A rejected action stays blocked | **closed** | `test_w3_rejected_stays_blocked.py`: a rejected plan is not approved, start raises, no engine call, no task |
| Steering does not silently expand the approved scope | **half** | `test_w3_steering_scope.py`: guidance reaches the model as text and leaves `scopePaths` untouched; the write itself is refused by M03's mounts, not re-proved here |

**What landed.** `_guard_stopped` no longer stops at the event stream: it calls
`_kill_engine`, so a run the guard judges stuck is actually killed instead of
burning until `ENGINE_RUN_TIMEOUT_SECONDS`. That closes the gap W2's N01
outcome named. The kill's own answer is recorded and the failure text says
"worker kill could not be confirmed" when the engine cannot confirm it — an
unconfirmed kill must not read as a stop. The method is also idempotent: the
live forwarder and the post-run drain can both reach it before either sets the
flag, and two failure events for one stop say nothing new.

Two tests pin it (`test_a_guarded_run_kills_its_worker`,
`test_an_unconfirmed_kill_is_not_reported_as_a_stop`). One thing they do not
pin, stated rather than implied: more than one `cancel` call for a run is
legitimate — the claim fence kills too, and `cancel` is idempotent by contract —
so the assertion is "the guard asked for the kill", not "exactly one call".

**The honest gap.** The remaining three clauses are not code I can close by
writing more of the same. "Resume reconciles partial writes" needs a decision
about what reconciliation means before it needs an implementation, and "rewind"
is listed in the plan below as owing a human policy decision. Week 4 should not
start on the assumption that W3's gate is met.

## Decisions taken (2026-10-26)

Two policy questions were open. Both are answered, and the answers are the
common ones rather than inventions:

1. **Rewind = revert to the last checkpoint.** Chosen over "leave the tree and
   mark the run abandoned". This matches how checkpointed agent tools behave
   (Cursor/Devin-style: a step boundary is a restore point) and it is the only
   one that lets a run continue after a bad step instead of abandoning work.
2. **Resume adopts whatever is on disk.** Chosen over "refuse to resume when the
   diff is dirty" and over "auto-revert on resume". Adopting is what the engine
   already does — it re-invokes in the same workspace and the SDK continues its
   own session history — and it is what other harnesses do: the interrupted write
   is visible to the next read, so the agent sees the real tree instead of a
   guess. Auto-revert would silently discard work the user may have accepted.

### What that changed now

The resume caveat used to read "review the workspace checkpoints before
resuming". **There are no run-workspace checkpoints**, so that sentence pointed
at a control that does not exist — the fake affordance the roadmap forbids. It
now says what is true: side effects are not rolled back and resume continues from
whatever is on disk. `test_execution_runs.py` pins both clauses.

### What that does NOT change, and the next slice

Decision 1 has nothing to revert to yet. Section 5 already implements the hard
part and it is reachable over HTTP —
`POST /api/v1/sandbox/{projectId}/checkpoints/{sha}/restore` resets tracked files
to a snapshot and records the restore as a new snapshot
(`section5/src/harness_sandbox/checkpoints.py:368`), with `list_checkpoints`,
`get_diff`, `list_hunks` and `revert_hunks` beside it. What is missing is the
*orchestrator* side: a run executes in its own workspace on the `harness_runs`
volume, and nothing snapshots it before or after the engine's edits. The seam is
even marked "UNVERIFIED" in `service.py` around the human-edit path.

So W3's rewind clause needs, in order: (a) a snapshot of the run workspace
before the engine's first edit and at each step boundary, written through the
sandbox checkpoint API (sections may not import each other, so HTTP, not a
library call); (b) a run-scoped `rewind` route that maps a run to its workspace
and calls the existing restore; (c) evidence that a killed run can be rewound and
then resumed with the tree matching the checkpoint. None of that is done, and
W3's gate stays open on it.

## Rewind, first slice (2026-10-26)

Decision 1 ("revert to the last checkpoint") had nothing to revert to. This slice
gives it something, without borrowing another section's storage or widening one.

**Why not git.** The obvious local tool is a commit per checkpoint, and the
orchestrator image has no `git` binary (checked inside the running container).
Adding one to the image is a build dependency bought for one feature. Why not
Section 5's checkpoint store either: it covers sandbox task workspaces, and
pointing it at `harness_runs` would let one run's sandbox read every other run's
workspace — a tenant-isolation regression for convenience.

**What it does instead** (`section4/src/harness_orchestrator/run_checkpoints.py`):
a checkpoint is a manifest plus content-addressed blobs, written under
`DSH_WORKSPACE/.checkpoints/<runId>/`, *outside* the run's own tree so the agent
cannot read or edit its own undo history as files. `max files`/`max bytes` caps
refuse rather than write a partial manifest — a checkpoint that quietly misses
files would make a later rewind delete real work.

**Wiring.** `_execute` snapshots `before attempt N` and `after attempt N` around
every engine call, best-effort: a snapshot failure is logged, never raised, and a
run with no checkpoints answers "this run has no checkpoints" rather than
pretending. Two routes join the execution surface:
`GET /runs/{runId}/checkpoints` and `POST /runs/{runId}/rewind`
(`{checkpoint?}` defaults to the newest). Rewind is refused with 409 while the
run has a live task: resetting files under a worker that is writing them would
race the agent and could restore a tree the agent never saw.

**Rewind is exact.** Files are written back AND files that did not exist at the
snapshot are removed. A "restore what you know" implementation would leave stray
files behind, and a later resume would then read a tree no checkpoint ever
contained.

**Evidence.** `test_run_checkpoints.py` (11) pins the store, including the
removal property, the caps refusal, symlinks never being captured or recreated,
and ids that cannot escape the store. `test_run_checkpoints_wiring.py` (5) pins
the before/after pair around a real `_execute`, that the BEFORE snapshot does not
contain the attempt's own write, the restore through the service, and the live
refusal. A live drill (`scripts/e2e/w3_rewind_drill.py`) drives the same path
over HTTP against the deployed stack.

**Live evidence (2026-10-26, deployed stack).** `w3_rewind_drill.py` →
`w3-rewind.json`: a run edited `calc.py` (`a - b` → `a + b`), two checkpoints
appeared (`before-attempt-1`, `after-attempt-1`), a rewind posted while the run
was EXECUTING was refused 409 `INVALID_STATE`, and a rewind to the before
checkpoint put the file back to `a - b` byte-for-byte.

The first run of that drill reported a *false failure* — a correct restore read
as wrong because `print()` added a newline the comparison did not expect — and a
*false pass* would have been worse: its "refused while executing" step fired
before any checkpoint existed and got 409 `CHECKPOINT_UNAVAILABLE`, which would
have credited the race guard for a refusal with a different cause. Both are
fixed and the evidence JSON above is from the corrected run.

**Still open in W3:** "resume reconciles partial writes" as a *measured* claim —
kill mid-write, resume, compare the tree against the checkpoint. Everything else
in the gate is either closed or closed as far as a seam test can take it, and the
gap is named rather than averaged away.
