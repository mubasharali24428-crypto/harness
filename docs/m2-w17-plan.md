# Marathon 2.0 W17 — reliability and scale (M2-W17)

The 2026-10-28 Marathon 2.0 roadmap calls for load and failure drills across
the queue, worker, MCP/LSP/browser processes and any native component, plus
backpressure and resource quotas. Its exit requires defined SLOs, a measured
capacity envelope, and crash/restart/cancel/cleanup, restore and saturation
evidence. This is a working plan and initial slice, not a W17 exit claim.

## Starting audit (2026-10-30)

| Area | Existing evidence or mechanism | W17 gap |
|---|---|---|
| Live events | `section1/.../eventbus.py` commits before fan-out, uses bounded per-subscriber queues and flags overflow; `app.py` closes an overflowed socket for replay. | Exercise saturation and recovery; establish live Postgres/socket and multi-process behavior under load. |
| Run queue | `section4/.../run_queue.py` persists claims and limits per-team running claims; W15 introduced fairer claim ordering. | Measure queue delay, saturation, recovery and fairness on the current revision. |
| Engine | `section4/.../engine_runtime.py` bounds the executor, applies a deadline and has a timeout breaker. Timed-out threads cannot be killed. | Drill provider failure and recovery, including exhausted worker threads; do not silently change models. |
| Baseline | `docs/benchmarks/BASELINE.md` records an older isolated dry-engine sweep; `scripts/loadtest/` contains its runner. | Repeat on a pinned current revision, report configuration, percentiles and failures; dry-engine results do not measure real-agent capacity. |
| Restore | W15 records a successful PITR drill and a scheduled automation blocked by account credits (`docs/m2-w15-plan.md`). | Re-run the applicable restore drill and verify scheduling or record the blocker. |
| Processes | Earlier W8, W10 and W11 drills cover individual cleanup and browser behavior. | Test saturation, crash, restart and cancellation for worker, MCP, LSP and browser processes with resource observations. |

The old throughput baseline is historical: subsequent queue changes mean its
ceiling must not be presented as today's measurement. No current hundred-run
capacity, SLO compliance, live-agent capacity or release readiness is asserted.

## First slice — bounded subscriber queues

`EventBus` now rejects nonpositive subscriber queue limits. Python's
`asyncio.Queue` treats those values as unbounded, defeating the memory boundary.
The default remains 1,000 and event contracts and replay behavior are unchanged.

`section1/tests/test_w17_event_backpressure.py` exercises a 100-event burst with
a four-event slow subscriber, a drained subscriber on the same project, and a
subscriber on another project. It checks bounded queue depth, isolated overflow,
uninterrupted fast delivery and recovery of the persisted sequence by replay.
It also checks rejection of zero and negative limits. This is an in-process
SQLite test, not a websocket, Postgres, multi-process or throughput benchmark.

## Further work (2026-10-30)

* Added [reliability operations](runbooks/w17-reliability.md): a proposed
  request-based error-budget calculation, triage, escalation and status
  communication procedure. A human must assign ownership and establish a
  status channel; the complete availability SLI and freshness SLI are not yet
  verified, so budget and attainment can be unknown.
* Focused local tests: Section 1 event, replay and realtime tests **35 passed**;
  Section 4 engine runtime, claim batching and heartbeat tests **44 passed**;
  worker daemon **10 passed**; MCP engine **9 passed**; Section 5 browser and
  LSP tests **57 passed** (three loopback tests required running outside the
  sandbox); observability and alert tests **35 passed, 1 skipped**. These are
  local test results, not live saturation or deployment acceptance. Section 1
  Ruff and `make static-gates` passed.
* Isolated dry-engine load and scratch MinIO/WAL-G recovery were exercised;
  details and limitations are in [W17 evidence](briefs/evidence/w17/README.md).
  At 100 offered users, 379 journeys completed without reported failures, but
  control p95 reached 1.4 s and engine-wait p95 27 s. Restore recovered the
  expected rows and promoted, with a backup-recording warning. This does not
  establish production or real-agent capacity or prove backup monitoring.
* Fixed two drill startup faults: the load runner now stops when Compose fails
  instead of potentially probing another stack, and the PITR script no longer
  expands an empty array on Bash 3.2 when `.env` is absent. Regression tests
  in `section1/tests/test_w17_load_probe.py` cover these paths. (The restore
  script's fixed names and unconditional cleanup were fixed in slice 2 below.)

## Slice 2 (2026-10-31): process cleanup, restore drill, load ladder

### Demonstrated gaps fixed (failing test first)

* **Process-group members outliving their leader.** `LspSession.kill()` and
  `close()`, `BrowserSession.close()` and `browser_gate._run_chromium()` sent
  `SIGKILL` to the process group only while the leader was still running (or only
  after a timeout). A language server or browser that forks workers and then exits
  or crashes left those workers running. Each path now signals the group
  unconditionally (the group id is the leader pid, via `start_new_session`; an
  empty group is a no-op). Tests with real forked `sleep` processes:
  `section5/tests/unit/test_w17_process_cleanup.py` (4 tests, all failed before
  the fix). Residual ceiling: a recycled pid could in theory name a different group
  after the leader was reaped; the window is the close call itself.
* **Restore drill was not safe by default.** `scripts/pitr_drill.sh` used fixed
  container and volume names, joined the live `harness_default` network (the compose
  file pins that name) and ran `compose stop`/`rm`/`volume rm` against the live
  project in its exit trap. It now creates a private compose project
  (`harness-pitr-<stamp>`), a private network (the compose network name is
  overridable through `HARNESS_NETWORK_DEFAULT`; default unchanged), refuses any
  project that already has containers, ignores `.env` unless `PITR_ENV_FILE` is set,
  and cleans up with `down -v` on its own project only. Tests with a stub `docker`
  assert unique names, no live project, no mutating call before a refusal.
* **`backup_runs` warning.** Cause: the scratch `walg-db` cluster is unmigrated, so
  the wrapper's insert failed (it logs a WARN and the drill still passed). The drill
  now applies migration 0059 to the scratch cluster and fails unless the wrapper
  records the backup and exactly one row exists. This proves the recording path, not
  that the deployed monitor/alert fires.

### Drilled and found sound (no code change)

* **Live event overflow and replay, socket level.** `test_w17_event_backpressure.py`
  drives the real `/ws/{project}` handler with a paced consumer (20 frames/s cap), an
  8-deep subscriber queue and a 60-event burst: the socket closes with 1013, fewer
  frames than published arrive live, and a resume from the last received seq recovers
  the rest in order with no gap and no duplicate. In-process SQLite, not Postgres
  NOTIFY across replicas or a real network client.
* **Cleanup of the confined shell, container and worker paths** was not re-drilled
  here: Linux confinement is unavailable on this macOS host, and the worker daemon,
  container runtime and engine are being changed by the W22 follow-up (not touched).

### Measured quotas (read from code, defaults)

| Resource | Bound | Where |
|---|---|---|
| Live subscriber queue | 1,000 events, then flagged overflow and close 1013 | `eventbus.py` (`subscriber_queue_maxsize`, now must be positive) |
| Live frames per socket | 500/s sustained; send timeout 10 s; 20 sockets per user | `WS_MAX_FRAMES_PER_SECOND`, `WS_SEND_TIMEOUT_SECONDS`, `WS_MAX_CONNECTIONS_PER_USER` |
| Replay on connect | newest 5,000 events then a `replayTruncated` marker; REST backfill pages 500 (max 2,000) | `REPLAY_MAX_EVENTS` |
| Event payload | 8,192 bytes, refused before the write | `BUS_PAYLOAD_MAX_BYTES` |
| Engine executor / deadline | 8 threads, 600 s per run, breaker after 3 timeouts, 300 s cooldown | `engine_runtime.py` (timed-out threads cannot be killed) |
| Queue | per-team running cap (default 4, plan override), global claim lock | `run_queue.py`, `HARNESS_RUN_CONCURRENCY` |
| Model relay container | 32 pids, 128 MiB, 0.25 CPU | `container_runtime.py` |
| LSP session | 5 s default deadline, 4 MB per frame, 2 MB per document | `lsp_client.py` |
| Browser session | 50 steps, 500 requests, 120 s cap, 4 MiB render | `browser_session.py`, `browser_gate.py` |
| MCP | 8 servers, tool timeout at most 300 s, no credentials | `section4/.../mcp.py` |

These are configured ceilings. Only the event-queue and group-cleanup behaviours above
were exercised in this slice. MCP server cleanup lives in the vendored runtime inside
the worker container and was not drilled; provider outage and recovery was not drilled.

### Load ladder and terminal-transition audit

`scripts/loadtest/run_ladder.sh` (new) pins a commit with `git archive`, builds once under
its own image tag and compose project, runs 4/8/16/32/64/100 closed-loop users on the
isolated dry-engine stack, drains the queue, audits the event log with
`audit_terminals.sql`, and removes only its own project. Always pass `LADDER_REV`: the
default `HEAD` moved under a run during this session. Two passes at `e9e5589`
(summaries in [W17 evidence](briefs/evidence/w17/README.md)):

* **No duplicate terminal transition** in either pass (no duplicate start, engine
  terminal or review terminal, no conflicting terminals, no duplicate seq, no leftover
  claims).
* **One lost terminal transition** in run 1 (rung 16): a run with `execution_started`
  and `model_routed`, no engine terminal event, no claim, the client stuck in
  `executing`. Not reproduced in run 2 and no service log exists for it, so the cause is
  unproven. `_publish_safe` swallowing a failed terminal publish is consistent with it and
  is pinned by a strict-xfail test (`section4/tests/unit/test_w17_terminal_publish.py`).
  **Open defect candidate**: bounded retry of `ENGINE_COMPLETED`/`ENGINE_FAILED`
  publishes in `service.py` (not changed here: it holds another lane's uncommitted work).
  Until fixed or disproved, "no lost terminal transitions" must not be claimed.
* Run 2's rung 4 hit the per-team run-start limit (60/min, fixed window) with 179
  refused starts: guard behaviour, but it makes the low rungs a rate-limit test unless
  journeys are paced.
* Absolute latency varies about 2x between passes on this shared laptop (control p95 at
  100 users 1.5 s then 2.7 s). The orchestrator container sat at its 1 CPU limit from 16
  users up. No capacity figure, safe user count or SLO attainment follows.

## Remaining work and gates

Status after slice 2 (nothing here is a W17 exit claim):

1. **Owner gate - SLO ownership. Not done; needs a human.** Decisions required, none
   invented here:
   * Name the accountable owner (person or role) for service indicators and the error
     budget policy in the [reliability runbook](runbooks/w17-reliability.md).
   * Adopt, change or reject the three proposed objectives in
     [slo-and-capacity](runbooks/slo-and-capacity.md): 99.5% request availability over 30
     days, control-plane p95 at most 250 ms, 99% of events delivered or replayable within
     5 s. For scale: this laptop measured control p95 of 0.64-1.3 s at 16 users and
     1.5-2.7 s at 100 under the dry engine, i.e. above 250 ms on this host; whether
     that is a host artefact or a design limit is unmeasured on the target deployment.
   * Decide the eligibility and exclusion rules, the retention of the time series, and who
     builds the missing eligible-request counter and the event-freshness histogram.
   * Decide the alert destination and who is paged. An Alertmanager container exists in
     the live stack, but delivery to a named human has not been verified here.
2. **Load ladder: done for the dry engine, with the open defect candidate above.**
   Real-provider measurements are **blocked** until an owner approves a budget. Needed:
   provider and model, number of runs per rung, a hard spend cap, and who approves it.
   No spend was incurred in this slice (the dry engine makes no provider calls; model
   spend: none).
3. **Failure drills: partly done.** Done and fixed: process-group cleanup for LSP and
   browser paths; socket-level overflow and replay. Not done: provider outage and recovery
   without downgrade (needs a paid or stubbed provider drill), MCP server cleanup (lives in
   the vendored runtime inside the worker container), Linux-confined shell cleanup (needs a
   Linux host), and worker-daemon kill and cancellation (the W22 follow-up owns those files
   and was running its own daemon-kill drills). Event-bus behaviour across replicas on
   Postgres NOTIFY was not re-drilled.
4. **Restore: done except scheduling.** The drill is isolated and passes with the backup
   recorded. The W15 monthly automation lives in QoderWake and could not be inspected from
   here; its trigger was blocked by account credits when last recorded. Owner action:
   confirm the account has credits and check the run at the next firing (2026-11-01T12:00Z).
   The W16 native-component decision was not inspected for an extra native drill.
5. **Owner gate - on-call and public status. Not done; needs a human.** Decisions
   required: the on-call roster and escalation contact; whether a public status page
   exists, its provider and publisher, update cadence and accessibility review; monitoring of
   the intended deployment topology. The fixed and held-out evaluation re-runs need paid
   provider calls and an approved budget (blocked, as in item 2).

W16 slice 4 and the W22/W13 follow-ups were being developed concurrently; their files were
not touched, and the live stack was not restarted or recreated.
