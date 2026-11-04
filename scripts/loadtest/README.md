# scripts/loadtest — throughput truth probe (M18-C4)

Drives the REAL user journey against a live Harness stack and publishes the
measured ceiling in `docs/benchmarks/BASELINE.md`.

```
login -> create project -> propose plan -> approve plan
      -> start run -> poll /status until terminal (done|blocked)
```

Same endpoints the S7 UI drives (`section7/e2e/journey.e2e.ts`); run ids are
minted client-side exactly like the e2e helper.

## Files

| file | role |
|---|---|
| `harness_flow.py` | sync client for the journey + `run_flow()` telemetry |
| `locustfile.py` | locust profile; emits control-plane / engine-wait / journey stats |
| `smoke_probe.py` | one-journey sanity check, no locust needed |
| `run_probe.sh` | boots the isolated compose profile + runs locust sweeps |
| `results/` | CSV/console output of each sweep (gitignored) |

## The isolated stack

The probe never touches the live compose stack. `deploy/docker-compose.yml`
carries a `loadtest` profile: `db-loadtest` (fresh PG volume), `migrate-loadtest`,
`identity-loadtest`, `orchestrator-loadtest` on ports **18001/18004** (PG on
15499). Engine boots `ENGINE_LIVE_MODE=dry` → zero provider spend; ownership
gate off so the probe's passwordless logins mint teams.

```sh
scripts/loadtest/run_probe.sh smoke                 # 1 journey end-to-end
scripts/loadtest/run_probe.sh run 8 60 baseline8    # 8 VUs for 60s
scripts/loadtest/run_probe.sh down                  # remove stack + volume
```

Knobs via env (compose interpolation): `PROBE_ENGINE_MAX_CONCURRENCY` (default
8 = section4's dedicated executor), `PROBE_RUN_CONCURRENCY` (per-team queue cap,
default 4), `PROBE_WORKER_POLL_SECONDS` (claim loop cadence, default 0.5).

## Locust directly (stack already up)

```sh
cd scripts/loadtest && ./.venv/bin/locust -f locustfile.py --headless \
  -u 16 -r 4 --run-time 120s --csv results/sweep16 --only-summary \
  HARNESS_IDENTITY_URL=http://localhost:18001 \   # env, not CLI arg
  HARNESS_ORCHESTRATOR_URL=http://localhost:18004
```

(env vars must be exported before the command, not appended after it)

## Reading the numbers

- **control-plane p50/p95** — propose+approve+start API latency (the human-gate path).
- **engine-wait mean/p95** — start→terminal: durable-queue wait + engine execution.
- **journey RPS × VU math** — concurrent-run ceiling: how many VUs before
  engine-wait inflates non-linearly (executor saturation).
- **failures** — any non-terminal run at timeout, or non-2xx anywhere.

## Honest scope

Dry engine measures PLATFORM throughput (API + queue + executor + DB + events),
not provider latency or multi-replica behavior — see BASELINE.md §Limits.


## Repeatable ladder (W17)

```sh
LADDER_REV=<commit> scripts/loadtest/run_ladder.sh [vus ...]   # default rungs 4 8 16 32 64 100
```

Pins `LADDER_REV` with `git archive` (always set it explicitly), builds once under the
image tag `w17-<rev7>` in its own compose project, runs each rung with `--no-build`,
waits for the queue to drain, audits the event log with `audit_terminals.sql`
(lost or duplicate terminal transitions, leftover claims) and writes `SUMMARY.md` plus raw
consoles, samples and service logs to `docs/briefs/evidence/w17/ladder-<rev7>/`
(`LADDER_OUT` overrides, `LADDER_HOLD` sets the seconds per rung, `LADDER_KEEP=1` leaves
the stack up). Do not edit the scripts while it runs: bash reads them incrementally.
Each VU is one team, and the product limits a team to 60 run starts per minute, so low
rungs of unpaced journeys can be refused with 429; set `HARNESS_TASK_PACING_S` to avoid it.
