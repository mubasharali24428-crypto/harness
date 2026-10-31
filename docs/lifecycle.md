# Run lifecycle (master prompt parts 10, 22, 23)

Single page for what pause, resume, stop, timeout, failure and completion do, what is durable,
and what the UI/API may claim. The transition table below is generated from
`section4/src/harness_orchestrator/lifecycle.py` (`RULES`); `tests/unit/test_lifecycle_table.py`
drives the real `AgentStateMachine` through every row and fails if this page, the table or the
machine drift apart.

## Vocabulary

| Word | Meaning |
|---|---|
| **state** | `AgentState` in the machine and `state` on the run aggregate: idle, planning, awaiting_plan_approval, executing, paused, awaiting_done_signal, reviewing, blocked, pr_opened, done. |
| **phase** | What an operator may be told (`lifecycle.phase` on `GET /runs/{id}`): pending, running, pausing, paused, blocked, reviewing, stopping, stopped, completed. |
| **cancel requested** | A stop/pause was accepted. Durable immediately (event + `run.cancel_requested` audit). |
| **cancel confirmed** | The engine is confirmed unable to mutate files (worker process group dead / container gone). `run.cancel_confirmed` audit, `lifecycle.cancelConfirmed`. |
| **user status** | What a person reads (`lifecycle.userStatus`, W23): working (running, planning, reviewing, pausing, stopping), waiting (idle, plan approval, paused, done-gate, PR open), blocked, finished (stopped or completed only). The UI adds a fifth, reconnecting, when its own refreshes fail. |

`done` is the only terminal state and is shared by *accepted review* and *stop*. The two are told apart by
the durable stop tombstone (see Stop below): `phase` is `stopped` for a stop, `completed` for an accepted review.

## Honest status rules

1. `phase = stopped` only when cancel is **confirmed**; until then `stopping` and `mutationPossible = true`.
2. `phase = paused` only when the engine is gone; until then `pausing`.
3. `mutationPossible` is true while the run is `executing` (even if queued and not yet claimed) or any engine is still alive.
4. `POST /runs/{id}/stop` returns `stopped` or `stopping` (or `completed` for a repeat on a run that finished normally);
   `POST /runs/{id}/pause` returns `paused` or `pausing`.
5. A stop/pause issued on a replica that does not run the engine (the claim is held by another worker) always reads
   `stopping`/`pausing`: that process cannot be observed from here. The claim is released, the owning worker's next
   heartbeat (default 10 s) sees it lost and kills its engine (see Reclaim). The grace window is `2 x heartbeat + 5 s`.

## Transition table

Outcomes: `transition` (state changes), `noop` (accepted, nothing changes), `refused` (HTTP 409 `RUN_TERMINAL` for pause;
the state machine raises `InvalidStateTransitionError`).

| Trigger | From | To | Outcome |
|---|---|---|---|
| pause | idle | idle | noop |
| pause | planning | planning | noop |
| pause | awaiting_plan_approval | awaiting_plan_approval | noop |
| pause | executing | paused | transition |
| pause | paused | paused | noop |
| pause | awaiting_done_signal | awaiting_done_signal | noop |
| pause | reviewing | reviewing | noop |
| pause | blocked | blocked | noop |
| pause | pr_opened | pr_opened | noop |
| pause | done | done | refused |
| resume | idle | idle | noop |
| resume | planning | planning | noop |
| resume | awaiting_plan_approval | awaiting_plan_approval | noop |
| resume | executing | executing | noop |
| resume | paused | executing | transition |
| resume | awaiting_done_signal | awaiting_done_signal | noop |
| resume | reviewing | reviewing | noop |
| resume | blocked | executing | transition |
| resume | pr_opened | pr_opened | noop |
| resume | done | done | noop |
| stop | idle | done | transition |
| stop | planning | done | transition |
| stop | awaiting_plan_approval | done | transition |
| stop | executing | done | transition |
| stop | paused | done | transition |
| stop | awaiting_done_signal | done | transition |
| stop | reviewing | done | transition |
| stop | blocked | done | transition |
| stop | pr_opened | done | transition |
| stop | done | done | noop |
| complete | idle | idle | noop |
| complete | planning | planning | noop |
| complete | awaiting_plan_approval | awaiting_plan_approval | noop |
| complete | executing | awaiting_done_signal | transition |
| complete | paused | paused | noop |
| complete | awaiting_done_signal | awaiting_done_signal | noop |
| complete | reviewing | reviewing | noop |
| complete | blocked | blocked | noop |
| complete | pr_opened | pr_opened | noop |
| complete | done | done | refused |
| fail | idle | idle | noop |
| fail | planning | planning | noop |
| fail | awaiting_plan_approval | awaiting_plan_approval | noop |
| fail | executing | blocked | transition |
| fail | paused | paused | noop |
| fail | awaiting_done_signal | awaiting_done_signal | noop |
| fail | reviewing | reviewing | noop |
| fail | blocked | blocked | noop |
| fail | pr_opened | pr_opened | noop |
| fail | done | done | refused |
| timeout | idle | idle | noop |
| timeout | planning | planning | noop |
| timeout | awaiting_plan_approval | awaiting_plan_approval | noop |
| timeout | executing | blocked | transition |
| timeout | paused | paused | noop |
| timeout | awaiting_done_signal | awaiting_done_signal | noop |
| timeout | reviewing | reviewing | noop |
| timeout | blocked | blocked | noop |
| timeout | pr_opened | pr_opened | noop |
| timeout | done | done | refused |

### What is durable for each trigger

| Trigger | Durable record |
|---|---|
| pause | event `paused` (reason), claim released, worker process group killed |
| resume | event `resumed`, claim re-enqueued (one live claim per run), engine re-spawned with the same session id |
| stop | event `paused` with reason `user_requested_stop` (the replay tombstone that makes `done` mean *stopped* after a restart); audit `run.cancel_requested`, `run.stopped`, and `run.cancel_confirmed` once the worker is gone |
| complete | event `engine_completed`; later `done_signal_received` -> `review_passed` -> `done` |
| fail | event `engine_failed` (reason); `heartbeat_lost` is emitted by the zombie reaper |
| timeout | event `engine_failed` with reason `timeout: ...` |

Replay (`OrchestratorService._rehydrate`) rebuilds the state from these rows after a restart: events after the stop
tombstone or after `done` are inert (`test_replay_terminal_sealing.py`, `test_stateless_orchestrator_n1.py`).

## Races (all pinned by `tests/unit/test_lifecycle_races.py`)

| Race | Rule |
|---|---|
| pause vs stop | Stop wins in either order. Pause after stop is refused (409 `RUN_TERMINAL`, counted as `refused`); stop after pause ends `done`. |
| stop vs completion | If the provider call returns after stop, the result is discarded: no `engine_completed`, engine result reads `cancelled`. |
| pause vs completion | A result arriving after pause is discarded; the run stays `paused` and resume re-runs exactly one continuation leg. |
| completion then stop/pause | Completion stands (`awaiting_done_signal`, real result kept); pause is a no-op, stop ends the run. |
| repeated / concurrent stop | Idempotent. One tombstone, one `run.stopped`, one `run.cancel_confirmed`; every request still leaves `run.cancel_requested`. |
| resume after stop | Silent no-op; nothing is re-spawned (`done` is sealed). |

## Recovery (`tests/unit/test_lifecycle_recovery.py`)

* **Worker crash.** The isolated worker exiting non-zero is an honest failure: engine result `error`, state `blocked`,
  one `engine_failed`, queue claim released, run resumable.
* **Service restart / dead worker.** A claim whose heartbeat stopped is released by the zombie reaper (`reap_zombies`,
  DB clock), the run is `blocked` with `engine_failed(heartbeat_lost)`, and a resume re-enqueues it. The dead worker
  executed nothing during recovery; the resumed run executes exactly once.
* **Restart while paused.** The paused state replays from the log; resume continues.
* **Reclaim never runs a run twice.** If a worker loses its claim (reaped, re-queued and re-claimed by another worker, or a
  stop/pause on another replica) its heartbeat sees `UPDATE ... WHERE worker_id = me` match no row, cancels its own engine
  task, kills its worker process, and marks its copy of the run stale. Its unwind releases the claim only
  `WHERE worker_id = me`, so it can never delete the new owner's claim.

## Cancellation limits (stated honestly)

* **Provider-side cancellation does not exist.** Stop kills our worker (process-group SIGKILL, or `docker rm -f` for
  the container boundary) and closes our connection to the model provider. No provider API is called to cancel an
  in-flight completion: the provider may finish generating and bill the tokens already in flight. Cost after stop is
  bounded by the single in-flight request.
* **In-process engines cannot be killed.** With `HARNESS_ENGINE_ISOLATED=0` (tests, dev) the SDK call is an OS thread.
  Stop cancels the awaiting task and discards the result, but the thread may keep writing files until its provider call
  returns; the run reads `stopping`, never `stopped`, until it exits. Only the isolated worker gives a real guarantee.
* **Confirmation is per process.** `cancelConfirmed` is true when *this* service sees its worker gone. Another replica's
  worker is covered by the claim-release fence above (confirmation window, not an instant guarantee).
* **Confirmation audit is recorded once**, by the request that stopped the run. If that request returned `stopping`, the
  later moment the worker actually dies is visible in `lifecycle` but is not a separate audit row.
* **Approvals are not engine-gated per file write.** The SDK edits files itself; high-impact changes are staged
  and withheld until approved (`staging.py`), not intercepted mid-edit.

## Cleanup after terminal states

| What | When | Rule |
|---|---|---|
| per-run scratch `.run-tmp/<id>` (worker TMPDIR/HOME) | immediately when the run is `done` and the worker is confirmed gone | `engine.cleanup_scratch`: refuses while a worker runs, refuses symlinks and anything resolving outside the workspace root |
| staging baseline, withheld files, session dir, scratch, approval diffs, collab events | `retention.py` after the retention window, only for runs finished *and quiet* for that long with no pending/approved/executing approval | every path is checked to stay under its root, symlinks are never followed or removed, hostile run ids never become paths |
| run workspace (outputs) | only with `--include-workspace` | default keeps user-approved outputs and review artifacts |
| audit log, approval metadata (digest, paths, state) | never by `retention.py` | audit age-out is the separate 365-day sweep |

A `blocked` run (failure/timeout) is **not** terminal: it keeps its workspace and staging material until it either
resumes or goes quiet past the retention window.

## Workspace identity

`GET /runs/{id}` carries `workspace: {id, fingerprint, isolated, isolation}` for isolated engines (null otherwise):
the run-scoped directory name, a 16-hex hash of the resolved location, and the confinement mode. No path, environment or
key is included (`test_workspace_lifecycle_s2.py`).

## Metrics

`harness_run_cancel_total{outcome}` with `requested | confirmed | unconfirmed | noop | refused`. Defined in
`domain_metrics.py`; see `docs/audit-and-events.md` for scraping.
