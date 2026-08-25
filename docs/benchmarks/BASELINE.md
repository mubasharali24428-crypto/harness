# BASELINE — platform throughput ruler (M19-B1)

> Mission 19 lane B1 (carried from M18-C4). Commit under test: **1ec49e7**
> (`feat(m19-n5): Python client SDK v0`, parent of 3805ca7). Measured
> 2026-08-25 on the isolated loadtest stack. These are the numbers the 10x
> program is measured against. Raw CSVs + console transcripts:
> `scripts/loadtest/results/b1clean{8,16,32,64}.*`.

## Headline numbers

| VU | journeys | failures | control-plane p50/p95 | engine-wait p50/p95 | journey RPS |
|---:|---:|---:|---|---|---:|
| 8  | 240 | 0 | 18 ms / 34 ms   | 4.1 s / 4.2 s  | 1.94 |
| 16 | 240 | 0 | 20 ms / 44 ms   | 8.4 s / 9.4 s  | 1.87 |
| 32 | 232* | 0 | 20 ms / 50 ms  | 19.0 s / 20.0 s | 1.69 |
| 64 | 266† | 0 | 21 ms / 98 ms   | 38.0 s / 39.0 s | 1.72 |

\* 232 not 240: the 8 s ramp ate into the fixed 120 s window at this level.
† 266 includes stragglers completing during the ramp-down/stop-timeout.

**Reading:** control-plane latency (the human-gate API path: propose +
approve + start) is essentially flat from 8 → 64 VU (p50 ≈ 20 ms, p95
34 → 98 ms). Journey throughput is already saturated at 8 VU (1.94/s) and
decays only mildly under 8× the load (1.72/s) — while engine-wait inflates
linearly with VU (closed-loop queueing: every added VU just stands in line).

## The ceiling: one claim per worker poll (Horizon NEXT validation evidence)

Little-law check: active-run concurrency A = throughput × service time
stays **A ≈ 1** at every level (1.94 × 0.53 s ≈ 1.0 at 8 VU; 1.72 × 0.53 ≈
0.9 at 64 VU) while offered parallelism grew 8× — the extra VUs pile up as
queue wait (engine-wait p50 4.1 s → 38 s), not as wider execution. Throughput
tops out just under **2 journeys/s**, exactly the shape the claim loop predicts:

- `section4/src/harness_orchestrator/worker.py` — `RunQueueWorker._loop`
  runs `await self.service.poll_once(); await asyncio.sleep(poll_interval)`:
  **at most ONE run claimed per cycle**.
- `section4/src/harness_orchestrator/service.py` — `poll_once()` performs a
  single `claim_next_run(...)` and returns after spawning that one engine task.
- `deploy/docker-compose.yml` (orchestrator-loadtest) ships the cadence:
  `HARNESS_WORKER_POLL_SECONDS: ${PROBE_WORKER_POLL_SECONDS:-0.5}`.

⇒ Admission is hard-bounded at ≈ 1/0.5 s = 2 runs/s no matter how wide
`ENGINE_MAX_CONCURRENCY` (here 8) or the replica count is. The executor is
NOT the binding constraint — containers idled at ≤ 6 % CPU at the ceiling.

**Tagged as validation evidence for N1/N2:** batching claims (or draining
the ready queue per wake-up) in `poll_once()`/`_loop` is the first lever;
it must survive N1's stateless-orchestrator rework (claims become durable
reads) and N2's slim bus. Any 10x exit criterion should be stated against
this mechanism, not against CPU or the executor width.

## Capacity math (from this run)

- **Events/day:** 5,875 `agent_events` rows over the 576.7 s active window
  = 10.19 events/s sustained ≈ **880 k events/day** at baseline load
  (~6.0 events per journey; consistent with the 6-event lifecycle chain).
- **Runs/day at ceiling:** 1.72 journeys/s ≈ **149 k runs/day** equivalent.
- **$/run (metered):** `cost_ledger` recorded 979 rows, avg **$0.0000510**
  per run, max $0.000051 — dry-engine runs are metered honestly via
  `run_metering.record_run_cost` using the catalog fallback price
  ($0.50/$1.50 per M tokens) over ~4-chars/token text estimates, model
  attributed as `stub`. Metering pipeline sustains ~170 k rows/day at the
  ceiling without loss (row count == completed-run count).

## Hardware & environment context

- Host: Apple M2 Max, 12 cores, 36 GB RAM (macOS 26.7, Docker Desktop
  29.5.3; VM budget 12 vCPU / 16 GB).
- Isolated compose profile `loadtest`: fresh PG 18 volume, identity on
  :18001, orchestrator on :18004, PG on :15499; live dev stack untouched.
- Engine: `ENGINE_LIVE_MODE=dry`, injected echo backend
  (`ENGINE_BACKEND_ECHO=1`, `ENGINE_ECHO_WORK_MS=50`) → deterministic
  ~0.5 s engine legs, zero provider spend.
- Executor/team knobs: `ENGINE_MAX_CONCURRENCY=8`,
  `HARNESS_RUN_CONCURRENCY=4`, `HARNESS_WORKER_POLL_SECONDS=0.5`.
- Container CPU at ceiling: orchestrator 6.3 %, identity 5.5 %, db 0.27 %
  — the wall is architectural, not resource saturation.

## Methodology (reproduce exactly)

```sh
# 0. pristine source snapshot of the commit under test (read-only):
git -C "/path/to/harness" archive <commit> | tar -x -C /tmp/harness_head_snapshot

# 1. build + boot the isolated stack from the snapshot
export HARNESS_IMAGE_TAG=b1clean PROBE_MASTER_KEY=dev-master-key-change-me-in-prod!
docker compose -f /tmp/harness_head_snapshot/deploy/docker-compose.yml \
  -p harness-loadtest --profile loadtest \
  up -d --build db-loadtest migrate-loadtest identity-loadtest orchestrator-loadtest

# 2. sanity journey
scripts/loadtest/run_probe.sh smoke

# 3. ladder (each level: 120 s, spawn 4 VU/s, locust headless)
export PROBE_SPAWN_RATE=4
scripts/loadtest/run_probe.sh run 8 120 b1clean8
scripts/loadtest/run_probe.sh run 16 120 b1clean16
scripts/loadtest/run_probe.sh run 32 120 b1clean32
scripts/loadtest/run_probe.sh run 64 120 b1clean64
```

Journey per VU (same endpoints the S7 UI drives): passwordless login →
create project → propose plan → approve → start → poll `/status` until the
done-gate opens → POST done-signal → poll until terminal (`done|blocked`).
Run ids are minted client-side like the e2e helper; status poll 0.5 s;
run timeout 180 s.

**Source-identity policy:** each journey sends a fresh RFC 5737 TEST-NET
X-Forwarded-For identity (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 —
verified to key distinct limiter buckets). One journey = one login, so no
identity ever repeats an auth attempt and the shipped abuse guards
(`app.py` in-process login window, DB-backed fixed windows incl.
RUN_START 30/min) model diverse traffic instead of collapsing the whole
generator into one IP bucket. The M18-C4 attempt died exactly there
(32,370 HTTP 429 RATE_LIMITED logins from localhost). Set
`HARNESS_PROBE_XFF=0` to measure the single-IP behavior instead.

### Verbatim console evidence (b1clean ladder)

```
=== 8 VU ===  Aggregated 1920 reqs, 0 fails | req/s 15.53
FLOW control-plane  p50 18  p66 19  p75 20  p80 21  p90 23  p95 34  #reqs 240
FLOW engine-wait    p50 4100  p95 4200  mean 4007   #reqs 240
=== 16 VU === Aggregated 1920 reqs, 0 fails | req/s 14.97
FLOW control-plane  p50 20  p95 44   #reqs 240
FLOW engine-wait    p50 8400  p95 9400  mean 8142   #reqs 240
=== 32 VU === Aggregated 1856 reqs, 0 fails | req/s 13.50
FLOW control-plane  p50 20  p95 50   #reqs 232
FLOW engine-wait    p50 19000 p95 20000 mean 17260 #reqs 232
=== 64 VU === Aggregated 2128 reqs, 0 fails | req/s 13.73
FLOW control-plane  p50 21  p95 98   #reqs 266
FLOW engine-wait    p50 38000 p95 39000 mean 31354 #reqs 266
```

## Honest limits

- **Single replica** per service; no multi-replica/NIC/PG-cluster effects.
  N1's two-replica simulation is the right next measurement.
- **Dry engine.** This measures PLATFORM throughput (API + queue + claim
  loop + executor + DB + event bus + metering), not provider latency,
  token-spend variance, or breaker behavior under real model calls.
- **Closed-loop generator.** Locust VUs run journeys back-to-back, so
  offered load == VU/W; open-workload (constant-arrival) numbers will
  differ in shape (same ceiling, different latency curve).
- **Generator headroom.** A single locust process warned "CPU usage above
  90 %" during one early (discarded) run; the published levels showed no
  such warning, but multi-process distribution is advisable above ~64 VU.
- **Poll quantization.** Client-side 0.5 s status polls smear engine-wait
  percentiles by up to ±0.5 s (visible as p50 landing on 100 ms gridlines).
- **Abuse guards under-represented** by design (see identity policy):
  a hostile single-IP scenario is a separate test, not part of BASELINE.
- **Commit scope.** Numbers reflect 1ec49e7 only. Working-tree changes from
  N1/N2/N3 (stateless orchestrator, slim bus, partitioned retention) were
  explicitly excluded after a mixed-vintage image produced false walls —
  see `docs/briefs/m17-findings-*` style post-mortem below.

## Post-mortem: why this took the full mission (probe-side, not product)

1. **M18-C4 carry-over:** prior sweep hit the login rate limiters with all
   VUs on one IP → 32,370× HTTP 429, zero samples. Fixed by per-journey
   TEST-NET identities (methodology above).
2. **Runner never aimed at the isolated stack:** `run_probe.sh run` didn't
   export `HARNESS_IDENTITY_URL/HARNESS_ORCHESTRATOR_URL`, so locust hit the
   LIVE dev stack (158 k RATE_LIMITED/OWNERSHIP_REQUIRED). Fixed in the runner.
3. **Empty `HARNESS_MASTER_KEY` pydantic override:** compose interpolates
   `${PROBE_MASTER_KEY:-}` → empty string overrides the runner's dev-key
   fallback → KeyVault refuses to boot ("master_key must be non-empty").
   Runner now pins the throwaway dev key (legal under ALLOW_DEV=1).
4. **Mid-flight tree drift:** N1–N4 committed 3805ca7 while the sweep ran;
   rebuilding images picked up half-landed sources (done-signal TypeError on
   `diff_ref`; partitioned-table seq collisions). Final ladder therefore
   builds STRICTLY from a git-archive snapshot with a provenance check —
   and cleanly reproduces the pre-N1..N4 baseline above.
