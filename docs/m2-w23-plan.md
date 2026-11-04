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
3. **Steering timing — built, see "Slice 3" below.** State when queued guidance takes effect, sourced from
   the engine's actual delivery point, not a guess.
4. **Stop confirmation — built, see "Slice 4" below.** Planned for after the Stage 1 gate: the UI and CLI report
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
  (Superseded by the W23 exit run: the frontend now runs `vw23exit1`.)
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

## Slice 3 — steering timing

* **Delivery point (measured from code, not assumed).** The engine reads guidance
  once per engine call, as the call starts: `service._execute` calls
  `collab.guidance_since` and prepends it to the prompt. After the call returns,
  `mark_guidance_delivered` writes the cursor. Nothing injects guidance into a
  call already in flight, so the honest answer for a running run is "not this pass".
* **API.** `POST runs/{id}/redirect` (guidance or scope change) and an accepted
  `control/decide` now answer `takesEffect`, from `collab.takes_effect(state, queued)`:
  `after_acceptance` (queued for the holder), `next_start` (idle, planning, plan
  approval, paused, blocked), `next_execution` (executing, done-gate, reviewing,
  PR open: only if the run executes again) or `never` (done). Permission
  changes are not injected and get no value. The field is additive, and the
  response is typed `dict`, so the spec and SDKs are unchanged.
* **UI.** The "Send direction" notice and the holder's "Suggestion accepted"
  notice say when the agent sees it. The latter used to say "sent to the agent",
  which was not true for a running run. An unknown or missing value shows no
  timing rather than a guess.
* **CLI.** There is no steering command in `harness`, so there is nothing to
  show. Add it with one when a CLI steering command lands.
* **Conservative edge.** An `executing` run whose engine call has not started yet
  (queued, unclaimed) is reported as `next_execution`, although its first call
  will read the guidance. The report can say later than the truth, never earlier.
* **Not covered.** A per-guidance "delivered" marker. The cursor exists
  (`guidance.delivered`), but it is filtered out of the event stream as noise,
  so the UI cannot yet confirm delivery after the fact.
* **Tests.** Every `AgentState` × queued (20 cases) is in `test_control.py`, with
  a check that the table covers every state. The route tests cover queued →
  `after_acceptance`, accepted → `next_start`, direct guidance → `next_start`,
  and a permission change carrying no value. `tests/unit/steeringTiming.test.ts`
  covers each text and unknown values.
* **Evidence (2026-10-31).** section4: 2025 passed, 59 skipped. section7: 1055
  vitest passed and tsc clean. `spec_drift.py` clean; ruff clean. Not deployed.

## Slice 4 — stop confirmation

* **Gate note.** Built by owner direction ("complete the W23") while the Stage 1
  exit is still open. It changes **no stop behaviour**: no backend file
  changed. It changes only what the UI and CLI claim, and they now claim less,
  so it cannot make a stop less truthful. The Stage 1 gate itself is not closed
  or relaxed by this.
* **CLI.** New `harness stop <run> [--wait 30] [--json]`. It exits 0 only when
  the server answers `stopped` or `completed`, or when the lifecycle phase
  reaches it within `--wait`. A stop that stays `stopping` exits 5 (timeout)
  and says the agent may still change files. A server without a lifecycle stays
  unconfirmed.
* **UI.** The Stop button no longer discards the server's answer. It says
  "Stopped. Confirmed…" only for `stopped`, and otherwise "not confirmed yet:
  the agent may still change files". The note upgrades once the refreshed
  status carries `cancelConfirmed`.
* **Run page.** It used to stop polling as soon as the state read `done`, which
  froze an unconfirmed stop on "Working". `runSettled` now keeps polling while
  `mutationPossible`.
* **Tests.** `clients/python/tests/test_cli.py`: confirmed at once, polled until
  confirmed, never confirmed (bounded by `--wait`), no lifecycle, and exit codes
  and text. `tests/unit/stopConfirmation.test.tsx`: every stop answer, plus
  `runSettled` for each lifecycle case.

* **Live (2026-10-31).** This passed on the deployed stack as part of
  `w23-stop-33f65a53927a` (see the exit evidence below). `harness stop --json`
  exited 0 with `confirmed: true` in 0.61 s, and `harness status` then read
  `finished`.

## Exit evidence (unchanged from the continuation plan)

* A fresh-machine setup run, with the readiness report captured.
* The drift check **fails** on a deliberately stale daemon. Both faults are
  reproduced: a token mismatch (`401`) and an old image (revision mismatch).
* A stop test confirms no orphan processes (relies on the W22 drills).

## W23 exit run (2026-10-31)

**Build and deploy.** Images `vw23exit1` were built from `git archive de62c0e`
plus the 7 uncommitted slice 4 files, for backend, engine-worker,
worker-daemon and **frontend** (the `node:22-slim` pull works again). All 13
app containers, the frontend included, run that tag. The code digest
`9725a594…` is identical in the orchestrator, the daemon and the worktree.

| Exit item | Evidence | Result |
|---|---|---|
| Drift check fails on a stale daemon | `w23-ready-bb838bc7787e` on `vw23exit1` (and `w23-ready-3b505b2afab7` earlier) | **Met.** Token fault → `WORKER_UNAUTHORIZED`; old image → `WORKER_PREDATES_READINESS`; `/ready` 200 throughout; both recovered |
| A stop test confirms no orphan processes | `w23-stop-33f65a53927a` (shared stack), `w23-stop-264d359b6cf0` (fresh stack) | **Met, 9/9 checks each.** A live `python3 … time.sleep(180)` child was in the worker at stop. `userStatus` read `working`; guidance mid-run answered `next_execution`; `harness stop` confirmed; worker and relay containers gone, none left on the run; lifecycle read `stopped` / `cancelConfirmed` / `finished` |
| Fresh-machine setup run, readiness captured | `w23-fresh-bare-6ac4b2e8`, `w23-fresh-configured-2976bfdb`, `w23-fresh-configured-patch-000391bd` | **Met with limits (below):** a fresh checkout and fresh database on this host, not a separate machine |

**Fresh setup, step by step.** The shared stack was stopped (not removed) from
06:22 to 06:37. `docs/quickstart.md` was followed in a clean tree under compose
project `harnessfresh`:

1. **The quickstart as written failed (F1).** `migrate` refused to start
   because no master key was set. Compose reads `.env` from the compose file's
   directory (`deploy/`), so the key the quickstart appends to the repo-root
   `.env` never reached the services. `make e2e-compose` runs against an
   already-running stack, so it never exercised this path. **Fixed:**
   `docs/quickstart.md` now passes `--env-file .env`. With that, the stack was
   ready in 18 s (cached images), and `/ready` 200 and the UI 200 were confirmed.
2. **Bare `.env.example`:** `harness doctor` exited 1, naming
   `provider: fail ENGINE_DRY` and `worker: fail WORKER_TOKEN_UNSET`, with model
   and revision `unknown`. The seeded demo project counts as a connected
   repository, and permissions were `ok`.
3. **Provider, model, live mode and worker token set:** all six checks `ok`,
   exit 0. **The first run then blocked (F2):** `engine_failed: Invalid
   Anthropic Messages API request` (`w23-stop-0e024cb381c5`, FAIL). The model
   this deployment uses needs the OpenRouter patch (`DSH_PATCH_FILES`, which
   `.env.example` documents). The readiness report said `model: ok` anyway,
   because it checks configuration, not that the provider accepts the model.
   This is the "provider reachability not proven" gap from slice 1, now shown
   to matter on a real first run.
4. **With the patch file set:** all six `ok`, exit 0, and the stop drill
   passed 9/9.

**Limits, stated plainly.**
* Same host, with Docker already installed. The `vw23exit1` images were
  reused, not rebuilt on the fresh setup.
* The database and store volumes were fresh (`harnessfresh_*`, removed by name
  afterwards). **Run workspaces were not (F3):** at `de62c0e` the runs volume
  name is pinned to `harness_runs`, so the fresh stack wrote its two runs'
  workspaces into the live volume (`w23stop-1793453186`, `w23stop-1793453423`;
  left there, unknown to the live database). W17 committed the per-project
  scoping (`14a7391`) during this run, after the archive. Nothing used
  `down -v`.
* **Spend (F4):** `cost_ledger` has **0 rows** for the three `w23stop-*` runs,
  although each made one short model turn. A stopped run's turn is not metered.
  Bounded by three short haiku turns, but not recorded.
* The other lane's `harness-w17-5xx` load-test project was running before the
  shared stack was stopped and was gone afterwards. No command here addressed
  that project; it most likely went in that lane's cleanup after its 06:31
  commit.

**Follow-ups this run found, and where they stand (build `vw23f24`):**
* **F2, narrowed without spend.** `readiness.check_engine` now checks the
  request format: a base URL ending in `/v1` with no `DSH_PATCH_FILES` fails as
  `PROVIDER_FORMAT_MISMATCH`, and a patch path that does not exist fails as
  `PATCH_FILE_MISSING`. This is the exact failure the first run hit, caught
  before any run. The live report now reads `openrouter.ai; request format set
  by DSH_PATCH_FILES` (`w23-fresh-f2live-9719c90a`). Still open: proving the
  provider accepts the *model* costs one call per check and needs a spend rule.
  The `/v1` test is a heuristic: a gateway that is OpenAI-shaped on another
  path is not caught. **Update (W24):** the spend rule is "only when asked":
  `harness doctor --probe` sends one one-token request, at most one a minute
  (see `docs/m2-w24-plan.md`, "After the commit").
* **F4, fixed.** `_execute` meters the cancelled call from the transcript
  (`allow_estimate=False`, shielded) before returning. Delta metering keeps a
  resume from billing it twice. Live: the stop drill on `vw23f24`
  (`w23-stop-f59816cce46d`, PASS 9/9) wrote `cost_ledger` 7045 in / 64 out,
  $0.000736 for `w23stop-1793462964`. Before the fix was deployed, the same
  drill on the same database left no row (`w23stop-1793452864` and
  `w23stop-1793460273`, the latter `w23-stop-8e61b8c977ce`: 0 ledger rows each).
  Tests: `test_lifecycle_races.py::test_a_cancelled_call_is_still_metered` (stop
  and pause; fails without the fix) and
  `test_run_metering.py::test_stopped_run_with_proven_usage_lands_in_the_ledger`
  (real stop path and real writer, reads the ledger row back).

**Separate-machine run: steps for the owner.** No second machine is reachable
from this session (no saved SSH connection, no cloud environment), so this is
written for a person to run on one. On a machine with Docker and Python 3.11+
and nothing from Harness on it:

1. Clone at the tested revision, then follow `docs/quickstart.md` §1 exactly.
   It now uses `--env-file .env`. Build the images there (no image tag), so the
   build is part of the run.
2. Capture the bare report: `python3 -m venv .venv && .venv/bin/pip install -e
   clients/python`, then `.venv/bin/python scripts/e2e/w23_fresh_setup_capture.py
   --label bare-machine` (it logs in with an OTP and seeds a run). Expect exit 1,
   naming `ENGINE_DRY` and `WORKER_TOKEN_UNSET`.
3. Set the provider in `.env`: key, base URL, `DSH_MODEL`, `ENGINE_LIVE_MODE=live`,
   and, for an OpenAI-compatible gateway, `DSH_PATCH_FILES`. Also set
   `HARNESS_WORKER_DAEMON_TOKEN`. Start again with `--profile worker`, then run
   the capture with `--label configured-machine`. Expect exit 0. Since F2, a
   missing patch on a `/v1` gateway fails here, not on the first run.
4. Run `scripts/e2e/w23_stop_drill.py` (one short model turn) and send back the
   three JSON files from `docs/briefs/evidence/w23/`.

**W23 verdict.** Every exit item has evidence, with the fresh-machine item met
only as a fresh checkout and database on this host. A separate machine was
not exercised. Slice 4 was built before the Stage 1 gate by owner direction,
and changes no stop behaviour. Stage 1's exit is still open, and `STATUS.md`
stays NOT READY.

## Rules carried over

* Do not edit W17's files or the W22 lane's uncommitted files.
* New HTTP routes are recorded with `scripts/spec_drift.py --write` and both SDKs regenerated.
* Commit by explicit path; do not push unless asked.
* The live stack is shared with the W22 drills: no redeploy while a drill batch runs.
* Keep failures. Record spend from `cost_ledger`.
