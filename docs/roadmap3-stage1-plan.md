# Roadmap 3.0 Stage 1 — trust and recovery: remaining work (R3-S1)

Started 2026-10-31 at HEAD `3a2c7ad`, after Stages 0 and 3 were recorded. Scope and exit are
the Stage 1 row of [Roadmap 3.0](archive/roadmap-3.0.md); the work itself is W21 and W22
([W21](archive/m2-w21-plan.md), [W22](archive/m2-w22-plan.md)). This file is named for the stage, not
`m2-w25-plan.md`, because W25 is Stage 4 (the benchmark). It is a plan and record, **not** a
readiness claim: `STATUS.md` stays NOT READY and **Stage 1's exit gate is not claimed here**.

## Scope (from the roadmap)

> Finish confinement coverage for each supported backend; validate worker/child cancellation,
> orphan cleanup, lifecycle truth, partial-write reconciliation, replay, restart and provider
> recovery.

**Exit evidence:** deployment-level drills capture before/after state and process
disappearance; failures and unknowns retained; no false cancellation confirmation; repeat
where reliability is claimed.

## Where Stage 1 stood before this session

| Scope item | Record | State |
|---|---|---|
| Confinement per backend (W21 / M01) | [`m2-w21-plan.md`](archive/m2-w21-plan.md), `7341d50` | `container`, `off`, `seatbelt` each PASS after the two owner decisions (secret files masked at mount; native file tools off outside the container). Known limits are in that file. |
| Worker/child cancellation, orphan cleanup, lifecycle truth | W22 drills: stop-neighbour, crash-cleanup, daemon-kill (both modes), restart | Each drill passed 3/3 in the 2026-10-31 repeat batch on image `vw23f24`, one host (Docker Desktop, macOS). |
| Partial-write reconciliation | `w22_partial_write_drill` | 3/3 on `vw23f24`. |
| Replay, restart | `w22_replay_overflow_drill`, `w22_restart_replay_drill` | 6/6 on `vw23f24`. |
| Provider recovery | `w22_response_cut_drill` | 3/3 on `vw23f24`. A genuine upstream 5xx cannot be produced on demand and is not claimed. |
| Lease/heartbeat | `w22_suspend_reap_drill` | 3/3 on `vw23f24`. |

Three attempts per drill is a repeat, not a rate. The records are the other lane's
uncommitted files (`docs/briefs/evidence/w22/*`, `docs/m2-w22-plan.md`,
`scripts/e2e/w22_repeat_batch.py`); this plan cites them and edits none of them.

## Gate position carried in

* **Deployed revision differs from the drilled one.** The drills ran on `vw23f24`; the live
  stack now runs the `w24a1` overlay images, and a from-scratch rebuild (`w24b1`) is in flight
  in another session. A drill is evidence for the revision it ran on. After the rebuild the
  repeat batch has to be rerun on the images that will actually be deployed.
* **Not covered by any record** (from [`gap-reconciliation`](reviews/gap-reconciliation-2026-10-31.md)):
  MCP-server cleanup inside the vendored worker runtime, Linux-confined shell cleanup (needs a
  Linux host), event-bus behaviour across Postgres NOTIFY replicas, a genuine upstream 5xx.
* **Owner gates** (SLO, on-call, security review, release decision) are not engineering work and
  are not touched here.

## Inventory — code read for this plan

Found by reading the code behind the exit words "orphan cleanup" and "lifecycle truth", not by
a drill:

| # | Where | What the code does | Why it matters |
|---|---|---|---|
| 1 | `container_runtime.sweep_orphan_boundaries` via `_attached_container_ids` and `_running_containers` | A failed `docker network inspect` returns `[]` (the comment says the caller treats it as "in use"; it does not). A failed `docker ps` returns no running containers. Either way a network with no durable "settled" proof reads as quiet, and the sweep runs `docker rm --force` on the worker by name. | The daemon's periodic sweep has no settled proof (`settled_run_ids=None`) and an age gate of 300 s, so it is the only judge of a long-running live run. `run_boundary_running` already treats the same failures as "running"; the sweep does not. Orphan cleanup must not kill a live run. |
| 2 | `exports.py` run-history header | `header["run"]["state"]` is `agent_runs.state`. `service._settled_run_ids`, `retention.py` and `pilot_metrics.py` each say that column is not maintained, and every W22 drill saw it stay `idle` while runs ran, were reaped and stopped. | An exported record of a stopped run says `idle`. Lifecycle truth is in the event log. |
| 3 | `api.stop_agent` | `run.cancel_confirmed` is audited only if the engine is already quiesced when the stop request returns. A stop that returns `stopping` and finishes a second later never gets the audit row, although `lifecycle_view` later reports `cancelConfirmed: true`. | Under-reports confirmed cancels (`pilot_metrics`); it never over-reports. Needs a design choice, so it is recorded, not changed here. |

## Slices, in order

1. **Plan** (this file). *Verify:* each claim above names a file, commit or record.
2. **Sweep fails closed on unreadable Docker state.** Inspect or `ps` failing makes an
   unsettled network *kept*, never removed; a settled run (durable proof) is still reaped, since
   the proof is the licence. *Verify:* new tests fail on the current code and pass after;
   `test_boundary_sweep.py`, `test_worker_daemon.py`, `test_w22_orphan_window.py` stay green;
   `ruff` clean on the changed files.
3. **Run-history export does not report the unmaintained column.** The run state comes from
   the event log or is reported as unknown, never `idle` for a run that finished. *Verify:* a
   test with a stopped run; `test_exports.py` and the OpenAPI/spec checks green.
4. **Hand-over:** what a person, another session or another host has to do.

Slice 3 is taken only if its smallest honest form is small; if it needs a contract change it
becomes a recorded finding instead.

## Results

Recorded 2026-10-31. Nothing here is a readiness claim and no drill was run.

### 1. Plan (met)

This file. Items 1–3 of the inventory were read from the code at HEAD `3a2c7ad`; the
`file:line` of each is in the hand-over note and below.

### 2. Sweep fails closed on unreadable Docker state (met in unit tests, not deployed)

`container_runtime._attached_container_ids` and `_running_containers` now return `None` when
Docker could not answer (`network inspect` non-zero or unparseable; `docker ps` non-zero),
and `sweep_orphan_boundaries` **keeps** a network whose state is unknown unless the durable
log already calls the run settled. Before, the sweep took "unknown" as "nothing attached /
nothing running" and ran `docker rm --force` on the worker by name. A settled run is still
reaped by name, exactly as before, because the log is the licence; a test pins that.

Test-first: the two new "unknown is not quiet" tests failed on the old code (the live network
was removed, `kept == []`) and pass after; the settled-run test passed before and after.
`FakeDocker` gained `inspect_fails` / `ps_fails` and now honours `rm --force` (real Docker
kills a running container with it; the old fake only dropped stopped ones, which could not
have shown this bug). `ruff` is clean on both changed files.

**What this is not.** A unit test against a fake Docker. It is not a deployed-topology drill,
so it does not move Stage 1's exit; the code is not on the live stack, which keeps running
`w24a1`. Reaching a failure mode on a real daemon (a hung or restarting Docker during a long
run's sweep) was not attempted: it would disturb the shared stack.

### 3. Run-history export and the unmaintained state column (recorded, not changed)

`exports.py:533` puts `agent_runs.state` in the run-history header, and `search.py:295`,
`:430` and `:466` index the same column as the run's `status`. The service says that column is
not maintained (`service.py:2629`, `retention.py:5`, `pilot_metrics.py:184`) and every W22
drill saw it stay `idle`. A finished or stopped run therefore exports and searches as `idle`.

A real fix changes documented contracts (`docs/search-and-export.md` lists `state` in the
header and `status` in search), so it needs a decision first: derive the state from
`agent_events` (the way `_settled_run_ids` already proves settlement) or report it as unknown.
No test was added, because a test would pin a design that has not been chosen.

### Findings kept (not fixed here)

1. Slice 3 above.
2. `api.stop_agent` writes `run.cancel_confirmed` only if the engine is already quiesced when
   the stop response returns (`api.py:876–880`). A stop that returns `stopping` and finishes a
   second later never gets the row, while `lifecycle_view` later says `cancelConfirmed: true`;
   `pilot_metrics` counts the audit rows, so it under-counts. It never over-reports, and
   `test_stop_without_confirmation_records_no_cancel_confirmed` pins the first half. Fixing it
   needs a place that notices the later quiesce; `DURABLE_RECORD` in `lifecycle.py` currently
   promises the row "once the worker is gone".

### Verification

Run on 2026-10-31 in `section4` (`.venv/bin/python -m pytest`, sequential shards because there
is no pytest-xdist), with the default SQLite environment (no `HARNESS_TEST_PG`):

| Check | Result |
|---|---|
| `tests/unit/test_boundary_sweep.py` before the fix | 2 failed (the new "unknown is not quiet" tests), 17 passed |
| Sweep, daemon, orphan-window, boundary-recovery and stop-truth tests after the fix | 55 passed |
| `tests/unit` (1,902 collected) | 1,861 passed, 41 skipped, 0 failed. My `-q -q` hid pytest's own summary line, so the counts are the progress characters, checked against `--collect-only`. |
| `tests` outside `unit` | 249 passed, 18 skipped, 0 failed |
| `ruff check` on `container_runtime.py` and `test_boundary_sweep.py` | clean |

The 59 skips are the PostgreSQL-only, opt-in Docker and opt-in live-provider tests, the same
count and kind as the Stage 0 ledger; none was run here. The `unit` log also holds
"Event loop is closed" warnings and a duplicate-operation-id warning from `spec_drift`; neither
is a failure and neither was investigated. Nothing was run against the live stack and no model
was called: spend $0.

## Not claimed

* Stage 1's exit gate. No drill was run in this session and the live stack was not touched.
* Any reliability rate. The existing drills are three attempts each on one host.
* Anything about a host other than Docker Desktop on macOS, or about the Linux-native backend.

## Rules carried over

* Do not edit the other lane's files (`docs/m2-continuation-plan.md`, `docs/m2-w22-plan.md`,
  `scripts/e2e/w22_repeat_batch.py`, `docs/briefs/evidence/w22/*`, `packages/language/*`,
  `docs/hx-*`, `CHANGELOG.md`).
* No `docker compose` build/up/down, no service recreation, never `down -v`. The live stack is
  shared with a rebuild in flight.
* Commit by explicit path; do not push unless asked.
* Keep failures. Unknown stays unknown.
