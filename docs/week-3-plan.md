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
| Resume reconciles partial writes | **open** | nothing implemented; needs a kill mid-write and a workspace comparison |
| A rejected action stays blocked | **open** | approval routes exist; no W3 evidence gathered |
| Steering does not silently expand the approved scope | **open** | guidance is stored as events and never touches the shell policy, but that is a code reading, not a measurement |

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
