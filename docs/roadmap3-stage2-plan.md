# Roadmap 3.0 Stage 2 — reliability and ownership: close-out (R3-S2)

Started 2026-10-31 at HEAD `9a636b4`, after Stage 1's first slice. Scope and exit are the
Stage 2 row of [Roadmap 3.0](roadmap-3.0.md); the engineering work is W17 and was recorded on
2026-10-31 in [`reviews/stage2-reliability-2026-10-31.md`](reviews/stage2-reliability-2026-10-31.md)
(Units A–I). The owner worksheet is
[`reviews/stage2-owner-decisions-2026-10-31.md`](reviews/stage2-owner-decisions-2026-10-31.md).
This file reconciles both against HEAD, closes what engineering could still close, and says
plainly what is left. It is a plan and record, **not** a readiness claim: `STATUS.md` stays
NOT READY and **Stage 2's exit is not met**.

## Scope (from the roadmap)

> Validate terminal-event delivery, remaining failure drills, restore and monitoring. Measure
> dry and real-provider load on target topology.

**Exit evidence:** measured capacity and failure outcomes, backup/alert verification, named SLO
and on-call owner. Provider experiments require agreed budget.

## Why "finish" is not an engineering outcome

Of the exit's five nouns, engineering can produce evidence for three. The other two are
decisions the roadmap reserves for a person ("Human owners decide SLOs, legal terms, security
review, provider budget and release; implementation does not silently invent those decisions"),
and one more needs inputs only the owner has:

| Exit item | Who closes it | State at HEAD `9a636b4` |
|---|---|---|
| Failure outcomes (terminal-event retry, cross-replica NOTIFY, durable admission, Linux shell cleanup, MCP cleanup, real 5xx) | engineering | Done in W17 Units A–F. Unit tests re-run here: green (below). Drills were run at `0bc3128`–`14a7391` on isolated stacks, not at HEAD. |
| Backup and alert verification | engineering, then owner | Mechanism verified; a live base backup, a restore from the live archive, a nightly schedule and an artifact-store backup exist. The agent is loaded and the newest base backup is 14 h old (read below). **The page receiver is still dead** and there is no off-machine copy: both need the owner's destination. |
| SLO measurement | engineering | Done: eligible-request counter, event-freshness histogram, alerts and promtool tests. Nothing has been *attained*: no 30-day window exists. |
| **Named SLO adopted, on-call owner, escalation, page destination, status-page decision** | **owner** | Open. Worksheet items 1–4. |
| **Capacity on the target topology with a real provider** | **owner** (topology, provider, budget cap, stop condition) | Open. Only the laptop dry-engine figure exists. Worksheet item 5. |

## Read at HEAD, not copied from the record

Taken 2026-10-31 (evening PDT), read-only, nothing restarted:

* `promtool check rules` on `deploy/observability/alerts.yml`: 19 rules; `promtool test rules`
  on `alerts_test.yml`: SUCCESS. Run in a throwaway container from the cached
  `prom/prometheus:v2.53.0` image (`--pull=never`), so the live Prometheus was not touched.
* Live Prometheus (`localhost:9090`): **no active alerts**. `time() -
  harness_backup_last_success_timestamp_seconds` = 50,091 s (13.9 h), under the 25 h stale
  threshold. `~/harness-backups/nightly.log` agrees: hourly "not due" ticks, last backup run
  14:54Z, `store verify … ok=127 missing=0 corrupt=0`.
* `launchctl list`: `com.harness.nightly-backup` loaded, last exit 0. The log shows a 10-hour gap
  while the Mac slept and the first tick after wake; the catch-up design works, and nothing
  can wake a sleeping Mac (worksheet item 7).
* Alertmanager is up (3 days) but its email receiver still points at a Mailpit container
  (`scheduler-release-test-mailpit-1`) that exited 6 days ago. A `severity="page"` alert would be
  evaluated and then go nowhere.

## Slices

1. **Plan** (this file). *Verify:* each row above cites a record or a read.
2. **Unit I's open bug: an unbound run is no longer queued.** `POST /plans` leaves a run
   unbound when the team has no project, or several, and the propose named none. Approve and
   `POST /runs/{id}/start` then answered `200 executing` and queued a claim under `project_id=''`,
   which the claim query's `JOIN projects` can never match. That is the one-row cause of the
   67-day-old `HarnessRunQueueStalled` and the `HarnessEventBusLag` alerts. *Verify:* a test fails
   on the old code and passes after; the neighbouring plan-gate tests, section4 and section1
   stay green.
3. **Unit E's residual: a teardown script that forgets to scope the runs volume.** The
   regression test covered four named scripts. *Verify:* a scan over every script under
   `scripts/` and `deploy/`, with a synthetic tree that proves the scan can fail.
4. **Owner decisions:** put the worksheet's questions to the owner and record the answers where
   the worksheet says (`slo-and-capacity.md`, `alerting.md`). *Verify:* each recorded decision
   names who made it and when; nothing is filled in on anyone's behalf.

## Results

Recorded 2026-10-31. No drill was run, no model was called, the live stack was not touched:
spend $0.

### 2. Unbound runs are refused at start (met in tests; not deployed)

`OrchestratorService.start_execution` now raises `InvalidStateTransitionError` in the queued
branch when the run has no project, before any claim is written. Both start routes
(`api.py` and `plan_gate.py`) already map that to a documented `400 INVALID_STATE`, so the
OpenAPI spec, SDKs and `dist` are unchanged. The message tells the caller to propose again with
a `projectId`. A run recovered on another replica is unaffected: `_rehydrate` restores
`projectId` from `agent_runs`. Direct-spawn mode (no queue) is unchanged.

Test-first in `section4/tests/test_projectless_start.py`: the unbound-run test failed on the old
code with `200 {"state":"executing"}` and passes after; a bound-run control (queued under its
project) passes before and after, so the refusal is not vacuous.

**Not claimed:** the fix is not on the live stack (still `vw24a1`). The old stuck claim was
already parked by hand in Unit I. This does not change what `/plans` does when a team has
exactly one project (it binds to it).

### 3. Teardown scan (met)

`section1/tests/test_deploy_volume_isolation.py` now scans every `.sh`, `.py` and `.mk` file
under `scripts/` and `deploy/` for `down -v` / `down --volumes` and requires the file to scope
`HARNESS_RUNS_VOLUME`. Today it finds none unscoped. A synthetic tree shows it flags a shell
script and a Python script that forget, and accepts one that scopes. The default is still the
shared volume name, so a teardown in a file type or directory the scan does not read (CI
workflows are deliberately out: they run on a throwaway runner) is still unprotected.

### 4. Owner decisions (four of seven recorded; none verified)

Put to Mubashar Ali in chat on 2026-10-31; recorded as given, in the worksheet and the runbooks:

| Worksheet item | Decision | Applied in | What is still missing |
|---|---|---|---|
| 1. Service objectives | Adopt the three as **pilot objectives**, review **2026-11-30** | `slo-and-capacity.md` status line | A 30-day window of data; the objectives are adopted, not attained. |
| 2. On-call | **Mubashar Ali, sole primary**; no backup, no escalation | `alerting.md` section 6b | Nothing to build. A single person with no escalation is the stated pilot rule. |
| 3. Page destination | **Webhook + email** | `alerting.md` section 6b | The URL and address are the owner's to put in the gitignored `.env`, then the delivery drill against the real destination. **Until then nothing pages anyone.** |
| 5. Capacity budget | **Hard cap $5** for a real-provider experiment | worksheet item 5 | **Not started.** Topology, target load and the stop condition are unanswered, and the live stack is shared with a rebuild. |

Not asked, so still open: 4 (status page; the worksheet's recommendation is none for the pilot),
6 (off-machine backup destination and encryption) and 7 (`pmset` wake).

### Verification

Run 2026-10-31 on `9a636b4` plus the working-tree changes, sequential shards (no
pytest-xdist), default SQLite environment:

| Check | Result |
|---|---|
| `section4/tests/test_projectless_start.py` before the fix | 1 failed, 1 passed |
| the new test and its neighbours (`test_plan_gate`, `test_stateless_orchestrator_n1`, `test_edge_cases_decisions`) after | 38 passed |
| `section4/tests/unit` | 1,861 passed, 41 skipped, 0 failed |
| `section4/tests` outside `unit` | 251 passed, 18 skipped, 0 failed |
| `section1/tests` (before the scan test was added) | 1,233 passed, 63 skipped, 0 failed (the record's count) |
| `section1/tests` (final, with the scan test) | 1,235 passed, 63 skipped, 0 failed |
| `section1/tests/test_deploy_volume_isolation.py` with the scan | 5 passed |
| `ruff` on every changed Python file | clean |

The skips are the PostgreSQL-only, opt-in Docker and opt-in live-provider tests, as in the Stage
0 ledger; none was run here.

## What only the owner can close

Copied from the worksheet so this record stands alone; nothing below is decided:

1. Adopt the three service objectives (availability ≥ 99.5% per 30 days, control-plane p95 ≤
   250 ms, event freshness ≥ 99% within 5 s), with a review date.
2. Name the on-call primary, backup and escalation rule.
3. Choose where pages go (email, webhook, both); then run
   `scripts/e2e/w17_alert_delivery_drill.py` against the real destination.
4. Decide whether there is a public status page.
5. Capacity: target topology, provider and model, hard budget cap, target load, who may stop it.
6. Choose the off-machine backup destination and whether it is encrypted.
7. Run `sudo pmset repeat wakeorpoweron MTWRFSU 03:15:00`, or accept catch-up only.

Plus the separate human gates the roadmap lists: independent release-candidate review, external
security review, legal review, Hermes rights, the W20 release decision.

## Not claimed

* Stage 2's exit. No SLO is adopted, no on-call owner is named, no page destination exists and
  no real-provider or target-topology load has been measured.
* Any availability, latency or freshness objective as *attained*.
* That the drills in the W17 record still pass at HEAD: they ran on isolated stacks at earlier
  revisions, and re-running them needs a compose stack the shared rebuild is using.
* Anything about a second host.

## Rules carried over

* Do not edit the other lane's files (`docs/m2-continuation-plan.md`, `docs/m2-w22-plan.md`,
  `scripts/e2e/w22_repeat_batch.py`, `docs/briefs/evidence/w22/*`, `packages/language/*`,
  `docs/hx-*`, `CHANGELOG.md`).
* No `docker compose` build/up/down, no service recreation, never `down -v` on a shared
  project. Commit by explicit path; do not push unless asked.
* Keep failures. Unknown stays unknown.
