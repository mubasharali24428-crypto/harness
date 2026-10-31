# Marathon 2.0 W23 — everyday loop: start and run (M2-W23)

Started 2026-10-31 as the first week of [Roadmap 3.0](roadmap-3.0.md) **Stage 3**
(W23–W24, W7). Scope and exit come from the W23 row of
[the continuation plan](m2-continuation-plan.md). This is a plan and inventory,
**not** a W23 exit or a readiness claim; `STATUS.md` stays NOT READY.

**Ordering note.** Stage 3 started by owner direction while Stage 1's exit gate
is still open: the W22 reliability rate was being re-measured on `vs1coherent`
when this plan was written. Nothing here closes or relaxes that gate. Slices
that change stop or recovery behaviour wait for it; readiness and status work
does not depend on it.

## Scope (from the continuation plan)

1. First-run readiness check: provider, model, repository, worker, permissions.
2. Deployment drift detection that **names the stale component**; the W20
   stale-daemon `401` must be caught by this check, not by a person.
3. Live status that distinguishes working, waiting, reconnecting, blocked and finished.
4. A real stop that confirms the process ended.
5. Steering that says when it takes effect.

## Inventory at `328622e`

| Item | What exists | Gap |
|---|---|---|
| Readiness | `/ready` (`deploy/runners/services.py`) gates on DB and schema drift only; egress is informational. | Nothing checks provider, model, repository, worker or permissions. `/ready` was green during the W20 outage (ops runbook §3.11). |
| Drift | `scripts/eval/run_eval.py --verify-only` hashes runtime files in the orchestrator, worker image and daemon against the worktree. | It's an operator script, not a product check. It does not probe daemon **auth**, so the W20 `401` (a token mismatch) passes it. Images carry no build revision, so the product cannot name the stale component. |
| Worker reachability | The runbook §3.11 snippet probes `GET <daemon>/runs/zz-probe` with the orchestrator's token (`200` ok, `401` mismatch). | Manual only. |
| Live status | The collab stream has `ConnStatus` (`connecting`, `reconnecting`, `polling`, …) and `ConnectionStatus` copy (`collabStream.ts`). Run state is shown raw (`sessionSixWorkbench.tsx`: "Run state: …"). | No single status that maps run state × connection × lifecycle onto the five user states. |
| Stop | Lifecycle `{phase, cancelRequested, cancelConfirmed, mutationPossible}` (`lifecycle.py`). W22 drills prove it truthful on the deployed topology. | UI/CLI confirmation that waits on `cancelConfirmed` must be checked against Stage 1 evidence; no change before the gate. |
| Steering | `control.py` shared steering queue: non-holders' guidance is queued for the holder. | Nothing tells the user **when** guidance takes effect (next tool call, next turn, never). |

## Slices, in order

1. **Readiness report (drift included) — built, see "Slice 1" below.** An
   authenticated, read-only orchestrator report with one entry per check:
   `provider`, `model`, `worker` (reachable **and** authorised), `revision`,
   `repository` and `permissions`. Each entry is `ok`, `fail` or `unknown`, with
   a named reason and the component. It never echoes secrets. `/ready` is left
   as it is so compose healthchecks keep their meaning. A CLI `doctor` command
   and a UI first-run panel read the same report.
2. **Live status — built, see "Slice 2" below.** A pure mapping from run state, connection status and
   lifecycle to the five user states, with tests for every combination, shown in
   the run page and the CLI.
3. **Steering timing.** State when queued guidance takes effect, sourced from
   the engine's actual delivery point, not a guess.
4. **Stop confirmation.** After the Stage 1 gate: the UI and CLI report
   "stopped" only on `cancelConfirmed`, and say so when confirmation is pending
   or unknown.

## Slice 1 — readiness report

* `GET /api/v1/orchestrator/readiness?projectId=…`
  (`section4/src/harness_orchestrator/readiness.py`, route in `api.py`). Any
  signed-in member may call it. A foreign or unknown project answers the same
  `404` (no-oracle). `ok` is true only when every entry is `ok`; `unknown` is
  not a pass.
* **Drift is a code digest, not a build stamp.** The daemon answers an
  authenticated `GET /version` with a SHA-256 over its installed
  `harness_orchestrator` package; the orchestrator compares it with its own.
  Measured identical across the orchestrator and daemon containers at
  `vs1coherent` (75 files). A daemon whose image predates `/version` answers
  `404` after authenticating and is reported as `WORKER_PREDATES_READINESS`. A
  token mismatch (the W20 fault) is `WORKER_UNAUTHORIZED`, with the revision
  `unknown`.
* Provider and model are checked from the validated engine configuration
  (`live_mode.py`); no model is called, so reachability is **not** proven.
* `repository` fails when the project has no `repo_full_name`. Runs still work
  in a seeded workspace; the check reports what the everyday loop needs to end
  in a pull request.
* Clients: `harness doctor [--project] [--json]` (exit 0 ready, 1 not ready), and
  a "Run readiness" card on `/onboarding` that reads the stored project.
* Not covered: the engine-worker image's code (the daemon launches it; only the
  daemon's own code is compared), the other backends' revisions, and provider
  reachability.

### Slice 1 evidence (2026-10-31)

* Suites: section4 1962 passed / 59 skipped (no live PG), including
  `tests/unit/test_readiness.py`; Python SDK 90 passed; TS SDK 33 passed and
  tsc clean; section7 1019 vitest passed and tsc clean; `spec_drift.py` clean.
* Images `vw23ready1` were built from `git archive 328622e` plus only this
  slice's files. The other lanes' uncommitted work was left out. The daemon
  image's code digest (`2541be4cc70b…`) equals the worktree's.
* Deployed: 12 backend containers and worker-daemon on `vw23ready1`. The
  **frontend stays on `vs1coherent`**: pulling `node:22-slim` timed out three
  times, so the onboarding card is verified by vitest and tsc only, not live.
* Drill `scripts/e2e/w23_readiness_drill.py --tag w23ready1 --stale-tag s1coherent`:
  **PASS 7/7** (`docs/briefs/evidence/w23/w23-ready-3b505b2afab7.json`;
  its `git` field shows the HEAD at drill time, `1774770`, which another lane
  committed after the build).
  * Baseline: provider, model, worker, revision and permissions `ok`;
    repository `fail` (the drill's fresh project has no repository, as expected).
  * Token fault (daemon recreated with another token):
    `worker: fail WORKER_UNAUTHORIZED`, revision `unknown`, token never echoed.
    **`/ready` answered 200 throughout**, which is the W20 gap.
  * Image fault (daemon recreated on `vs1coherent`):
    `revision: fail WORKER_PREDATES_READINESS`, component `worker-daemon`.
    `/ready` 200 again.
  * Both faults recovered after the daemon was recreated on `vw23ready1`; the
    stack was left on that tag. No model was called.
* W23 exit status: "the drift check fails on a deliberately stale daemon" is
  **met** for the two W20 faults. The fresh-machine setup run and the stop
  test are still owed.

## Slice 2 — live status

* **Server half.** `lifecycle.userStatus` (additive, on `GET /runs/{id}` and
  `/status`) comes from `lifecycle.user_status(state, phase)`. It is `finished`
  only when the phase is `stopped` or `completed`, so the engine is confirmed
  gone. `pausing` and `stopping` stay `working` because files can still change.
  `reviewing` is `working` because a reviewer sub-agent runs it. The done-gate
  and an open PR are `waiting`.
* **Client half.** Only the viewer knows its own connection dropped, so
  `reconnecting` is added in the UI: `liveStatus(userStatus, ConnStatus)` in
  `realtimeCore.ts`. `finished` survives a dropped connection; `live`,
  `polling` (3 s refresh) and `access_lost` keep the server status; every other
  connection state reads `reconnecting`. The run page reads `reconnecting`
  after a failed background refresh. An older server that sends no
  `userStatus` gets no status line rather than a guess.
* **CLI.** `harness status` prints `run <id>: <state> (<userStatus>)`, and
  `--json` carries `userStatus`. Each call is a fresh request, so it never
  reports `reconnecting`; a failed call is an error.
* **Tests.** Every state × stop × quiesce combination (40) is in
  `test_lifecycle_table.py`, which also asserts that `finished` never appears
  with `mutationPossible`. Every server status × connection state (28), plus
  unknown values, is in `tests/unit/userStatus.test.tsx`, with render checks.
* **Evidence (2026-10-31).** section4: 2003 passed, 59 skipped. Python SDK/CLI:
  91 passed. section7: 1053 vitest passed and tsc clean. `spec_drift.py` clean,
  because `lifecycle` is typed as `dict` and no schema changed, so no SDK
  regeneration was needed. Not deployed and not checked live.

## Exit evidence (unchanged from the continuation plan)

* A fresh-machine setup run, with the readiness report captured.
* The drift check **fails** on a deliberately stale daemon. Both faults are
  reproduced: a token mismatch (`401`) and an old image (revision mismatch).
* A stop test confirms no orphan processes (relies on the W22 drills).

## Rules carried over

* Do not edit W17's files or the W22 lane's uncommitted files.
* New HTTP routes are recorded with `scripts/spec_drift.py --write` and both SDKs regenerated.
* Commit by explicit path; do not push unless asked.
* The live stack is shared with the W22 drills: no redeploy while a drill batch runs.
* Keep failures. Record spend from `cost_ledger`.
