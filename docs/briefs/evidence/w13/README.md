# W13 execution leg — drill evidence (2026-10-30)

`scripts/e2e/w13_exec_drill.py`, run three times. Each run is **isolated**: its own throwaway
PostgreSQL (`initdb` under a temp dir, port 55613), the real migrations, and the real identity
and orchestrator services (`deploy/runners/services.py` under uvicorn, ports 18601/18604).
Nothing touched the compose stack. Verdicts come from durable rows (`agent_events`,
`run_claims`, `runner_jobs`, `audit_log`), not from the runner's own output.

| file | tree | result |
|---|---|---|
| `before.json` | `git archive` of `e9e5589` (worker slowed to 30 s so an offer can be made at all) | 5 observations of the gap |
| `before-race.json` | same tree, normal 0.5 s worker | 2 observations: the local engine takes the run |
| `after.json` | this working tree | **16/16 pass** |

Service logs (tail) sit next to each JSON. Model calls: **0**; spend **$0** (the orchestrator's
engine is `ENGINE_BACKEND_ECHO`, no provider key).

## Before (what the previous commit did)

* An offer made before `start` is refused (`404`): there is no product seam, so a run can be
  offered only after `start` has queued it, in a race with the orchestrator's own worker.
* With the normal worker, the orchestrator's engine claims the run first and executes it on the
  harness host (`echo-backend completed …`); the late offer is refused.
* With the worker slowed so the offer wins, the runner claims, finishes `completed`, and the
  log never leaves `execution_started`: **a run reported completed that nothing executed.**

## After

* the offer before `start` is accepted; after `start` the claim stays `queued/enqueuer` for
  four seconds (eight worker polls) with no `engine_*` event, so the local worker skipped it;
* a stolen team-B token gets `404` from `spec` and `events`, byte-identical to an unknown id;
* a real `byo_runner.py` process runs `pytest tests/` through `shell_gate.py` under Seatbelt;
  the log reads `tool_call → tool_result → engine_completed`, the call is the approved command
  attributed to the runner, the result is a real passing pytest run (exit 0), and the
  settling summary is written by the server (`BYO runner customer-host executed 1 approved
  command(s)`), not by the echo engine;
* the runner token appears in no event; the lease is released and the job `completed`; the
  orchestrator's `/engine` endpoint replays the settle; assignment and completion are audited;
* a lying host — an out-of-plan command (`curl … | sh`) is `422 COMMAND_NOT_IN_PLAN` and not
  logged; `finish completed` with nothing recorded is settled `failed` and the run is `blocked`
  by an `engine_failed` the server wrote.

## What is simulated

* The "customer host" is this machine. The runner is a real, separate process running real
  commands under real OS confinement, but it shares the host and network with the services:
  this proves the protocol, the gate and the log path, **not** a WAN, a different OS, or a
  hostile host.
* The plan's command is always `pytest tests/` (API-proposed plans cannot set `tests`), and no
  agent loop runs on the host: no code is edited and no diff/PR is produced.
* Mid-command cancellation is unit-tested, not drilled. Transport hardening (mTLS/signing,
  per-runner rate limits) and UI are separate, still open work.

Re-run (needs `initdb`/`pg_ctl`/`psql` on `PATH`):

```
git archive e9e5589 | tar -x -C /tmp/w13-before
section1/.venv/bin/python scripts/e2e/w13_exec_drill.py --tree /tmp/w13-before --label before
section1/.venv/bin/python scripts/e2e/w13_exec_drill.py --tree /tmp/w13-before --label before-race
section1/.venv/bin/python scripts/e2e/w13_exec_drill.py --tree . --label after
```
