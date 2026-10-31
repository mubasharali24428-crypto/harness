# Marathon 2.0 W22 — stop and recovery

Started 2026-10-30. This is an investigation record, **not a W22 exit or
release decision**. Scope and exit are in [the continuation plan](m2-continuation-plan.md).
W17's uncommitted files are owned elsewhere and must not be edited here; W21 is
also active on the shared stack.

## Required evidence

Each drill must identify the source revision, deployed images, isolation,
before/after state, failure injection, outcome, residual processes and spend.
Keep unsuccessful and inconclusive attempts. A unit test or a prior revision's
drill is not a substitute for the deployed-topology evidence.

| Gate | Status | What remains |
|---|---|---|
| Worker and child cancellation | Passed once | Two live children were observed; stop removed the selected worker and relay while preserving the neighbour, and lifecycle confirmed the stop. Stop then restart of orchestrator and identity also passed 8/8 (see "Stop, then restart"). |
| Partial-write reconciliation | Passed once | A real interrupted write was rewound and the resumed task produced exactly 120 lines, as recorded below. |
| Crash cleanup | Passed once | Launcher-client crash and orphan sweep passed with a live neighbour. Daemon SIGKILL passed once on `w22fix2` after two fixes (see "Worker daemon SIGKILL"). An orphan can run for up to grace + interval. |
| Event replay | Passed 8/9 | Disconnect during publication, overflow, truncation and REST backfill (`before_seq` fix). One unexplained A-part ordering failure right after a redeploy (see below). Restart replay passed. The frontend does not yet backfill on the truncation marker. |
| Provider outage and recovery | Passed once (in-flight cut) | A response cut mid-stream blocked the run with no partial file, and a fresh run recovered. A real upstream 5xx outage cannot be produced on demand and is not proven. |
| Lease/heartbeat | Passed once | Cause found (host sleep, then the reaper won the race against our own heartbeats), reproduced on the deployed orchestrator, fixed in `worker.py`, and passed after the fix. See "Lease/heartbeat" below. Now on clean images built from git trees (no overlays). |

## Initial investigation

At start, HEAD was `93a220573d2d3e3a421e776a161b793d81d331b9`. The active
working tree contained W17 changes; none were edited. The shared Docker stack
was running, including the orchestrator and worker daemon. Its services were
not restarted or reconfigured for this investigation.

The retained database events for `w14-design-1793357294` show the last tool
result at **10:49:50.892 UTC**, followed by `engine_failed` with
`heartbeat_lost` at **11:01:27.147 UTC**, with a 90-second timeout. This
establishes a gap of about 11 minutes 36 seconds between those events, **not**
the last heartbeat time or the reason for its loss. The former orchestrator's
logs for the incident were not available from the current container. Host
sleep, process scheduling, database interruption, and other causes remain
unresolved. The run was blocked and the observed failure must not be treated
as a completed design task. See [the W20 raw record](briefs/evidence/w20/live-r3-w14-design-heldout.json).

The heartbeat is an asynchronous task in `service._execute`; it updates the
claim periodically, and the reaper uses database time to delete stale claims.
Loss of ownership triggers local engine fencing. Code inspection alone does
not prove that these mechanisms work under a long provider call or suspension.

Local focused tests: `tests/test_heartbeat_claims.py` and
`tests/unit/test_engine_runtime.py` **23 passed**. These are unit-level
evidence only. An initial invocation also named a nonexistent test path and
ran no tests; the corrected command produced the result above.

## Isolation hazard before live drills

The old W3 scripts hard-code the shared API ports and orchestrator container;
the W8 crash drill selects a daemon by Compose service label and invokes a
boundary sweep. They are not safe to run unchanged alongside W21.
Furthermore, the daemon's periodic sweep enumerates Docker networks carrying
the global `harness-run` label and protects its own in-memory run IDs, not an
explicit deployment namespace. The Compose worker daemon defaults
`HARNESS_RUNS_VOLUME` to the literal `harness_runs`. A second Compose project
on the same Docker daemon is therefore **not automatically isolated** merely
because its project name, database and published ports differ. A safe drill
must establish distinct run storage and prove that *both* daemons' sweepers
cannot act on the other stack's boundaries, or use a separate Docker daemon.
Do not test this by risking an active neighbour. See
`section4/src/harness_orchestrator/worker_daemon.py`,
`section4/src/harness_orchestrator/container_runtime.py` and
`deploy/docker-compose.yml`.

## Next steps

1. Coordinate the lease investigation with the W17 owner, including their
   saturation observations and a non-interfering test window or separate daemon.
2. Establish an isolated deployment with uniquely named volumes, scoped cleanup,
   independent provider control, and verified source/image identity. Verify the
   isolation before injecting a failure; do not reuse the shared stack's volume.
3. Adapt the drills to target that deployment explicitly and make cleanup
   run-specific. Capture before/after process, workspace, claim, event and ledger
   evidence. Record every run and provider spend.
4. Investigate any failures and rerun at the fixed revision. W22 remains open
   until all six rows above satisfy the continuation plan's exit.

Spend in this initial investigation: **$0**; no model calls were made.

## Fresh-project replay drill (2026-10-30)

W17 remains open according to its own remaining-gates list; per the user's
instruction it was not committed or edited. W21 is concurrently active. A
non-disruptive drill, `scripts/e2e/w22_replay_drill.py`, creates a fresh
project and run, observes `plan_proposed` over a WebSocket, disconnects,
approves the plan while disconnected, and compares REST persistence with the
WebSocket replay after the recorded cursor. It never starts the run or calls a
model. [Raw evidence](briefs/evidence/w22/w22-replay-a9b96323213b.json):
`plan_proposed` seq 4519; after disconnect `plan_approved` seq 4520; REST and
resumed socket both returned exactly 4520. All four drill checks passed; spend
$0. Ruff passed for the script. This is one event, with no overlapping
publication or backpressure, and does not close the replay gate.

Provenance: HEAD was `93a220573d2d3e3a421e776a161b793d81d331b9`; the deployed
identity `app.py` SHA-256 was `12bc1329e2435ac4be0ff396360d8dc2a78c281b2229dbd3b3e0bff41e92d332`,
matching the worktree; the deployed `eventbus.py` was
`b1fd826d87f2303dd9b194518ef5cdbd49179b6e6915550a87ff06da7e4b760c`,
matching HEAD but **not** the W17 uncommitted worktree version
`97c6dd7cd0587e7fed4f1fc2859439b609e83567898b661f47dc8031a9b7986d`.
This result therefore does not test W17's new backpressure behavior. Only one
Docker daemon (`desktop-linux`) was found; its default context points to the
same local Docker installation. Do not mistake separate contexts for separate
daemons.

## Partial-write drill — failure found (2026-10-30)

The existing W3 drill was run against the shared deployment using a uniquely
created run (`w3torn-1793379110`), affecting only that run and its workspace.
It observed the Python writer, paused after 29 of 120 lines, and rewound to
`before-attempt-1`; `out.txt` was removed. The second attempt did **not**
complete: it settled `blocked`, with `engine_failed` reason `session
"w3torn-1793379110" already exists`. The legacy drill wrongly marked this
outcome as a pass because it accepted *any* settled status and did not check
the resulting bytes. Its raw record is retained at
[`partial-write-attempt-1.json`](briefs/evidence/w22/partial-write-attempt-1.json),
but its `ok: true` and verdict are **incorrect**. The ledger records one row
of $0.000729 for the run. No claim is made that this is the complete provider
bill after a killed attempt.

`scripts/e2e/w22_partial_write_drill.py` is a copy with an explicit evidence
path and a stricter second-attempt check: a successful state and exact 120
lines are both required. It has not yet been rerun after a product fix. The
SDK exposes `resume_session`, whereas the engine currently calls `harness.run`
with the same session ID on each attempt. Whether resuming persisted history
is compatible with a rewind and the active security policy requires a careful
fix and a live proof; do not mask the error by deleting history or accepting a
blocked state. W21 is actively exercising the shared engine, so no shared
engine image or service was rebuilt or restarted for this investigation.

### Candidate fix (not yet deployed or live-verified)

The worktree now detects a persisted SDK session and calls the SDK's explicit
`resume_session` before submitting the new turn. If restore fails it reports
an error instead of silently starting a new session. A focused fake-SDK test
checks first start, resume, and failed restore without fallback. The engine
unit selection passed **17, with 1 live-provider test skipped**, and Ruff
passed. A network-disabled disposable worker container then copied the
interrupted session from a read-only runs volume and called the real SDK's
`resume_session`: it returned successfully without a model request. The first
attempt could not read the session as the image's default user; the second
used the wrong provider adapter and failed initialization. The successful
third attempt used the engine's `deepseek-official` adapter. This proves the
copied history can be restored by that SDK, not that a resumed turn completes
or that restored conversation history reflects a rewound workspace; those
remain required live checks. No shared service or image was changed.

### Live follow-up (2026-10-30)

After the user confirmed W21 was finished and the shared stack available, the
worker, daemon and orchestrator were overlaid with W21's `confine.py` and the
W22 engine fix. The prior images are retained as `*:w22-before` and the
candidate images as `*:w22-v2` and `*:w22-v3`; the running services use v3.
This is an overlay, not a clean-build proof. Attempt 2 resumed and produced
the exact 120 lines (independently checked SHA-256
`60f1a49ed066c1bcf9e279d4476846bb31015df113696cc6aa7e29f5c62fcd77`),
but its stricter verifier erroneously added a newline while reading and marked
it failed. The helper was corrected, without changing the expected output.
Attempt 3 restored the SDK session but finished without recreating the file:
retained history could make the interrupted tool call appear complete. The
resume prompt now says the attempt was interrupted and the workspace may have
been rewound. [Attempt 4](briefs/evidence/w22/partial-write-attempt-4.json)
observed a 26-line partial write, removed it by rewind, resumed, and verified
the exact 120-line output. It passed once; it is not a reliability rate.
The failure and verifier error remain in the evidence pack.

### Crash cleanup (2026-10-30)

The [first attempt](briefs/evidence/w22/w8-crash-cleanup-1793380154.json)
removed its orphan but failed the neighbour check because the neighbour had
already completed. The [second attempt](briefs/evidence/w22/w8-crash-cleanup-1793380194.json)
held a child running in the neighbour. It observed a killed victim client,
blocked victim and released claim, then reclaimed a separately constructed
orphan while preserving the live neighbour: **11/11 checks passed**. This is
one launcher-client crash and synthetic orphan, not a daemon-kill test.

`native-tool-confinement` was rerun after deployment: PASS with revision
verification (`baseline-20261030T171026Z.json`), preserving W21's gate.
Focused engine/checkpoint/lifecycle/heartbeat tests: 42 passed, 1 skipped.
Ruff passed for the W22 drill scripts from the repository root.

Recorded ledger rows: partial-write attempts 2, 3 and 4 were $0.003446,
$0.001724 and $0.003571 respectively; first crash attempt's completed
neighbour was $0.003465. Other killed/cancelled runs had no row at this
query; their provider spend is unknown, not zero.

The [live replay overlap drill](briefs/evidence/w22/replay-b66827484726.json)
published ten plans while a socket was connected and compared the five events
after its selected cursor with both REST replay and a resumed WebSocket. All
sequences matched in order. It did not force a socket overflow or disconnect
during publication, and it used the deployed committed event bus, not W17's
uncommitted changes. No model call was made.

The [per-run egress outage drill](briefs/evidence/w22/w22-outage-045f99d07d7f.json)
disconnected only its own model relay from Docker's outbound bridge. The run
blocked with `Connection error`; its model-routed event selected primary, with
no fallback enabled. A later held-out code-search run passed in 16.87 seconds
at a verified revision ([runner evidence](briefs/evidence/eval/baseline-20261030T171246Z.json));
its ledger row records `anthropic/claude-haiku-5.5` and $0.003916. The outage
run had no ledger row; its actual provider spend is unknown. This simulates a
network-path failure before or during request dispatch, not a verified outage
of the upstream provider or interruption after a response has begun. The
first recovery invocation deferred all tasks and is not counted as a pass.

## Stop drill — partial evidence (2026-10-30)

[`stop-attempt-1.json`](briefs/evidence/w22/stop-attempt-1.json) records
run `w3stop-1793379171`. A Python child sleeping for 180 seconds was observed
inside the worker before `/stop`; the worker container was absent 0.67 s after
the request and the run had no claim afterward. The drill reported a pass,
but its evidence is narrower than W22: it found no relay ID and did not prove a
live neighbour survived or capture the lifecycle object. The state field read
`done` while the stop response said `stopped`; the durable `paused` event has
reason `user_requested_stop`. Inspection of `stop_agent` shows these are
intentional legacy representations: it calls `finish()` and emits a paused
envelope with that reason. They are not by themselves evidence of a defective
stop. The authoritative lifecycle and replay after process restart remain to
be checked. No cost-ledger row was found for the stopped run;
actual provider spend is **unknown**, not necessarily zero. The stop script
targets a fresh run and uses the allow-listed Python command rather than the
legacy script's disallowed `sleep` executable.

The [two-run stop drill](briefs/evidence/w22/stop-bc2a3406679e.json) later
observed both children before stopping one. That run's worker and relay were
gone; the other worker and child were still running. Its lifecycle reported
`stopped`, `cancelConfirmed: true`, `mutationPossible: false`. The script then
stopped the neighbour. This does not establish lifecycle behavior after a
service restart, and killed-run provider spend may be unrecorded.

## Lease/heartbeat — cause, reproduction, fix (2026-10-30)

**Cause of the `w14-design-1793357294` incident.** `pmset -g log` shows the
Mac went into clamshell sleep on battery at 03:54:30 PDT (10:54:30 UTC). That
was 4 min 40 s after the run's last tool result. The Mac woke at 04:01:25 PDT
(11:01:25 UTC). `heartbeat_lost` was emitted at 11:01:27 UTC, **2 s after
wake**. The worker was not dead. The orchestrator was frozen along with the
Docker VM, the database clock jumped about 7 minutes on resume, and the first
`RunQueueWorker` cycle reaped the orchestrator's own live claims before their
heartbeat tasks could write. The heartbeat task then saw the claim gone and
fenced the engine. The reaper runs first in every cycle
(`worker._loop`). After any freeze longer than the 90 s timeout, the
orchestrator's own runs therefore race their own reaper.

**Long tool call is not the cause.**
[`w22-heartbeat-a79c9e725a80.json`](briefs/evidence/w22/w22-heartbeat-a79c9e725a80.json)
shows heartbeats every 10 s, with a maximum age of 10 s, across a 300 s
single bash call. The drill marked itself FAIL only because the bash tool's
300 s timeout ended the 540 s child and the run then finished normally (claim
released, `engine_completed`, $0.001544 in the ledger). It is not a lease
failure.

**Drill.** `scripts/e2e/w22_suspend_reap_drill.py` starts a run whose child
sleeps for 200 s. It runs `docker pause harness-orchestrator-1` for 120 s,
which is longer than the 90 s timeout; the database keeps its own clock, as it
did in the incident. It then unpauses and captures the claim, run state,
worker and child, and events before, during and 30 s after the freeze. Pass
means the claim is still `running` under the same worker, the child is alive,
and there is no `heartbeat_lost`.

| Attempt | Orchestrator image | Result |
|---|---|---|
| [before](briefs/evidence/w22/w22-suspend-d52cbb89af02.json) | `1c184627288c` (w22-v3) | FAIL, **reproduced**: `engine_failed:heartbeat_lost` on resume; claim deleted, worker and child killed |
| [v4](briefs/evidence/w22/w22-suspend-1edac1aff49b.json) | `43b18187c5be` | FAIL: overlay ineffective (see below) |
| [after](briefs/evidence/w22/w22-suspend-7d886b237c25.json) | `29bd710afb12` (w22-v5) | **PASS**: claim kept by `e24a13bfd5dd:1:b848d6`, heartbeat age 0, child alive, no `heartbeat_lost`; `/stop` then paused it with `user_requested_stop` |

**Fix.** `section4/src/harness_orchestrator/worker.py`: if a loop cycle starts
more than `interval + 10 s` after the previous one, the process was frozen.
Reaping is then deferred for 25 s, two heartbeat intervals plus margin, and a
warning is logged. A worker that really is dead is still reaped by any
replica that was not frozen, or by this one after the grace. Test:
`section4/tests/unit/test_w22_suspend_reap.py` fails with detection disabled
and passes with it.

**Overlay finding.** The backend runner (`deploy/runners/services.py:13`) puts
`/opt/harness/section4/src` first on `sys.path`. In the backend image,
therefore, a file copied only into `site-packages` is shadowed. v4 did that
and changed nothing; v5 also copies `worker.py` to the `/opt` path. The
`site-packages` copies of `engine.py` and `confine.py` in that image carry the
W21/W22 fixes, but the `/opt` copies do not. The engine and confinement run in
the engine-worker and worker-daemon images, which load `site-packages` and do
carry both fixes, so the earlier W21/W22 evidence stands. A clean image build
from the committed tree removes the split.

Not edited: any W17 file. The W17 owner should know that the reaper now
defers after a self-suspend, because their saturation runs share the loop.

**Verification.**
- `section4/.venv/bin/python -m pytest tests -q`: 1893 passed, 59 skipped.
- `ruff check` is clean on the changed files.
- An idle 15 s pause on v5 logged `worker loop was suspended 15s; reaping
  deferred`.

**Spend.** The before, v4 and after runs had no ledger rows (killed or
stopped mid-run), so their spend is **unknown**, roughly one model turn each.
The heartbeat observation run cost $0.001544. Rollback: `harness-backend:w22-v3`.

Also observed, not changed: `agent_runs.state` stayed `idle` through every
drill run, including running, reaped and paused. Lifecycle comes from events,
not from this column.

## Completing the open items (2026-10-30)

Items: replay under publication and overflow, a response cut off partway,
stop and replay across a service restart, a SIGKILLed worker daemon, and a
clean image build. W17's files were not edited. W17's only `eventbus.py`
change is a guard in `EventBus.__init__` that rejects
`subscriber_queue_maxsize <= 0`. With the default 1000 the overflow and replay
code paths are identical to HEAD, so these drills cover W17's bus at runtime
without deploying its uncommitted file.

### Clean image build

`git archive HEAD` (`2adb551`) was built with
`HARNESS_IMAGE_TAG=w22clean`, without overlays: backend `4b4f3b242f36`,
engine-worker `920b802634ec`, daemon `2e5609d67d08`. Every backend service and
the daemon were moved to it. The frontend stayed on `vdev-local`. In-image
hashes of `worker.py`, `engine.py`, `confine.py` and `eventbus.py` matched
HEAD in both the `/opt` and `site-packages` copies, which removes the overlay
split described under Lease/heartbeat. `run_eval.py --verify-only` printed
`verified: true`, and `native-tool-confinement` passed with revision
verification ([evidence](briefs/evidence/eval/baseline-20261030T174419Z.json)).
That rerun rewrote `briefs/evidence/w21/native-confinement-container.json`
(timings and temp paths only); the file is left uncommitted.

The fixes below were then built the same way, as `w22fix`, from git tree
`5a6ba51a` (HEAD plus exactly the six fix files, W17's edits excluded):
backend `a4b830181f75`, engine-worker `69e11a69c4e3`, daemon `ad509ae07403`.
The deployed `worker_daemon.py`, `service.py` and identity `app.py` hashes
matched that tree. Rollback: `HARNESS_IMAGE_TAG=w22clean`, or unset for
`vdev-local`.

### Replay under publication and overflow — bug found, fixed

`scripts/e2e/w22_replay_overflow_drill.py` uses a fresh project and inserts
`terminal_output` rows through the same committed row plus `NOTIFY` path that
the orchestrator uses. It makes no model call.

- **A:** a socket reads 60 of 300 events while rows keep arriving,
  disconnects, and resumes with `afterSeq`. The union must equal the database
  exactly.
- **B:** a 12,000-row burst overflows the live queue (close 1013). The resume
  must carry the truncation marker and the newest 5,000 events. REST paging
  from the client's cursor must then recover the skipped range.

On `w22clean`, part B **failed** twice
([1](briefs/evidence/w22/replay-load-f3b884edbbc0.json),
[2](briefs/evidence/w22/replay-load-2fb24c69e524.json)): 7,000 and 6,921
events were unrecoverable. `replay_page` always keeps the *newest* events of a
gap, so REST paging by `nextCursor` can never walk back to the gap's start,
contrary to the marker's own documentation. When the gap is no larger than the
cap but larger than `limit`, plain REST paging also skips events without any
signal. The first attempt ([evidence](briefs/evidence/w22/replay-load-82329fba6ca4.json))
was a drill error: it used an event type outside the enum.

**Fix (user's choice):** `GET /projects/{id}/events/replay` takes
`before_seq`. With it, pages run oldest-first within `(after_seq, before_seq)`
through `bus.replay()`, and an empty page ends the range. Without it, behaviour
is unchanged, so `CommandCenter` (newest 50) is unaffected. `eventbus.py` was
not touched. Test: `test_rest_before_seq_backfills_truncated_gap_oldest_first`.

On `w22fix`: **8 of 9 runs passed every check**, including
`B_recovered_everything` with 0 missing. One run
([evidence](briefs/evidence/w22/replay-load-b9b25e1510e2.json)), the first after
the redeploy, failed `A_union_exact`: the counts matched (60 + 240 = 300) but
the order or the members did not. It was not reproduced in the 8 later runs.
Its cause is **unknown**, because the drill then kept no sequence lists. The
drill now records the mismatch (`A.mismatch`). Part A uses the WebSocket path,
which the fix did not change. Still open, not fixed: the frontend's
`useEventStream` ignores the truncation marker and does not backfill.

> Superseded: backfill landed in `211e740` and is verified deployed with
> `useEventStream.test.ts` 20 passed; the replay drill re-passed 8/8 at
> `0df3e5a` (9/10 consecutive). See
> [`replay-load-9b9e224aa3a7.json`](briefs/evidence/w22/replay-load-9b9e224aa3a7.json).

### Stop, then restart orchestrator and identity — PASS

[`w22-restart-8bc3c7cdbdb1.json`](briefs/evidence/w22/w22-restart-8bc3c7cdbdb1.json),
on `w22clean`: a child was observed and the run stopped. Both services were
restarted with `docker restart` and were healthy again 7.9 s later; the drill
then waited 15 s. All 8 checks passed:

- Lifecycle `stopped` / `cancelConfirmed` / `mutationPossible: false`, identical before and after.
- State `done`, the same before and after.
- REST events 29572–29577 identical; WebSocket replay equal to REST; no new events.
- No worker, relay or claim afterwards.

No ledger row; spend **unknown** (one model turn).

### Response cut off partway — PASS

`scripts/e2e/w22_response_cut_drill.py` asks for a 400-line file written in
one write call. It waits until the run's relay is sending the response back
(run-network TX 1,919 → 5,378 bytes and still growing), then `docker kill`s
the relay mid-stream. [`w22-cut-6002596d294d.json`](briefs/evidence/w22/w22-cut-6002596d294d.json),
on `w22clean`, passed all 6 checks:

- The run settled `blocked` with `engine_failed: Connection error.` and engine status `error`.
- `big.txt` was never written, so nothing came of the truncated tool arguments.
- No worker or relay was left.
- A fresh recovery run then wrote `ok.txt` and reached `awaiting_done_signal`.

The first attempt
([evidence](briefs/evidence/w22/w22-cut-044351480f3d.json)) was a drill error,
a probe that crashed on `bonding_masters`; its run was stopped.

Spend: the attempt-1 run $0.002595 and the recovery run $0.003112 (ledger). The
cut run has no row, so its spend is **unknown**. This is a response
interrupted in flight between the provider path and the worker. A real
upstream provider 5xx outage cannot be produced on demand and is **not**
claimed. No fallback model is configured, so a provider outage blocks runs.

### Worker daemon SIGKILL — two bugs found, fixed

`scripts/e2e/w22_daemon_kill_drill.py` starts a run whose child sleeps, then
`docker kill`s `harness-worker-daemon-1`, restarts it 60 s later, and follows
the orphan. The deployed sweep grace (300 s) and interval (600 s) are **not**
shortened. The orphan must outlive that window on its own: four 280 s
children, because the bash tool caps a call at 300 s. A pass needs the run
settled, the claim gone, nothing left on the run's network, the daemon to have
logged `POST /sweep`, and the run's network removed. The network can only go
by the sweep, which rules out a worker that simply finished.

| Attempt | Images | Result |
|---|---|---|
| [ae686d336065](briefs/evidence/w22/w22-dkill-ae686d336065.json) | `w22clean` | **FAIL, reproduced.** About 60 s after the kill the run is `blocked` (`engine_failed: worker daemon ended the run without a result`) and the claim is released. 90 s after the daemon came back, the worker and its child were still running. Lifecycle said `mutationPossible: false` while it ran. The drill removed it. |
| [3de504a69cc7](briefs/evidence/w22/w22-dkill-3de504a69cc7.json) | `w22fix` | Drill reported PASS — **false pass.** No sweep was called; the worker ended because its 200 s child finished. Its relay kept running unseen, because the drill found relays only through their worker. Removed by hand later. |
| [9cf765594514](briefs/evidence/w22/w22-dkill-9cf765594514.json) | `w22fix` | **FAIL.** The orchestrator called `GET /boundaries` and `POST /sweep` at 18:18 and 18:28. The run was settled and its network about 14 min old, yet nothing was stopped. The orphan lived 1,132 s, until its children ended, and its relay was left running again. |
| [4ac502b1bf2c](briefs/evidence/w22/w22-dkill-4ac502b1bf2c.json) | `w22fix2` | **PASS, 8/8.** The child was still live when the daemon came back. The daemon logged `POST /sweep` at 18:49:28; by 18:49:34 the worker, relay and network were gone and the child was dead. The drill removed nothing. The orphan lived 586 s after the kill. |

**Bug 1 (design gap, user chose the fix).** In the split deployment the
daemon holds the Docker socket but not the DB, so it may only sweep networks
with nothing running. The orchestrator holds the DB but not the socket. A
SIGKILLed daemon's worker therefore ran on after the run was reported blocked.
Fix: on its usual sweep cadence, the orchestrator asks the daemon for the
labelled boundaries (`GET /boundaries`). It proves which are settled from
`agent_events`, as in single-process mode, and sends those to
`POST /sweep`. The daemon runs the existing strong sweep and never touches a
run it still holds. Tests: `test_sweep_takes_settlement_from_the_caller_but_never_a_held_run`
and `test_split_deployment_sends_settled_runs_to_the_daemon`.

**Bug 2 (since W8, root cause of attempt 9cf765594514).** The sweep read a
network's age with `docker network inspect --format '{{.Created}}'`, which
prints Go's `2026-10-30 18:35:07.806244887 +0000 UTC`. `_parse_iso` rejects
that, and the sweep keeps every network it cannot date. Any sweep with a grace
above 0 has therefore **never removed anything**. That includes the daemon's
own periodic sweeper, whose leak protection W8 and W4 relied on. The unit fake
returned ISO whatever format was requested, and the only grace test asserted
"kept". Fix: `{{json .Created}}` (RFC 3339), and the fake now returns that.
New test `test_a_settled_boundary_older_than_the_grace_window_is_removed`
fails on the old code and passes on the fix. A direct call on a synthetic
orphan (`olderThanSeconds: 0`) stopped and removed it, isolating the bug to
the age check.

The drill now finds containers through the run's labelled network as well, so
a relay that outlives its worker is seen.

Residual, not fixed: between the daemon's death and the next sweep, an orphan
can run for up to grace + interval (about 15 min by default). During that time
lifecycle reports `mutationPossible: false` while the orphan can still write.

> Superseded 2026-10-31 at `0df3e5a` / `vw22fix4`: the engine-side `POST /reap`
> retry loop stops the orphan ~17 s after the daemon returns, and lifecycle
> truthfully reports `mutationPossible: true` while any container of the run is
> live. See [`gap-reconciliation-2026-10-31`](reviews/gap-reconciliation-2026-10-31.md)
> and [`w22-dkill-76b5633be5b3.json`](briefs/evidence/w22/w22-dkill-76b5633be5b3.json).

### Verification and spend

- `section4/.venv/bin/python -m pytest tests -q`: 1896 passed, 59 skipped.
- `section1/.venv/bin/python -m pytest tests -q`: 1203 passed, 63 skipped.
- `ruff check` is clean on every changed source, test and drill file.
- Deployed: `w22fix2` from git tree `e5bc4856` (HEAD `2adb551` plus the eight
  fix files): backend `719c97e00e9f`, engine-worker `09ffe485738a`, daemon
  `5a30e84a10e7`. The deployed `container_runtime.py` (`809cb707abbf`) and
  `worker_daemon.py` hashes matched the tree. Identity `app.py` is identical
  in `w22fix` and `w22fix2`, so the replay results carry over. Rollback:
  `HARNESS_IMAGE_TAG=w22fix`, `w22clean`, or unset.

Ledger rows: daemon-kill runs $0.000732, $0.000733, $0.000734 and $0.000734.
Each row covers only the first model turn; turns taken after the daemon died
are not recorded, so the full spend is **unknown**. Cut attempt 1 was
$0.002595 and the cut recovery $0.003112. The cut run and the restart run have
no rows, so their spend is **unknown**. Replay drills: $0, no model calls.
Recorded total: **$0.008640**; the actual total is higher by an unknown amount.

Release decision: blank, for an authorized owner. W22 is not declared exited
here. Each live gate passed once (replay 8/9), which is a single observation,
not a reliability rate.
