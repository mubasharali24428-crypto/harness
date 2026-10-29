# Ops Runbook — harness Docker stack

Mission 14 P2 (Squad BETA). Owner: deploy/ops lane. Baseline: 9eeb896.
Related: `docs/runbooks/backup-and-pitr.md` §Troubleshooting (original W2 carryover
note), `docs/runbooks/maintenance.md` (scheduled vacuum, M15-W5-D2),
`docs/GAP_ANALYSIS_AND_FIXES.md` DB-RESTART entry, wave graphs W3→W4.

---

## 1. Topology at a glance

One image (`harness-backend`, built from `deploy/backend.Dockerfile`), selected by
`HARNESS_SERVICE`; `deploy/entrypoint.sh` dispatches:

| Service | Port | Notes |
|---|---|---|
| db | 127.0.0.1:5499→5432 | postgres:18-alpine, `max_connections=120` |
| migrate | — | one-shot, must exit 0 before backends start |
| identity | 8001 | first backend up; frontend waits on its health |
| github | 8002 | |
| models | 8003 | |
| orchestrator | 8004 | |
| sandbox | 8005 | |
| meeting | 8006 | |
| review | 8008 | |
| frontend | 3000 | Next.js |
| backend | 8000 | LEGACY all-in-one, **profile `legacy-backend` only** |
| proxy | 80/443 | profile `proxy` |
| walg-db, walg | — | profile `walg` |
| events-retention | — | profile `events-retention` |
| db-maintenance | — | profile `maintenance` (M15-W5-D2): scheduled VACUUM (ANALYZE) for agent_events/rate_buckets/audit_log — see [maintenance.md](maintenance.md) |

Network: single user-defined bridge `harness_default` (declared explicitly in
compose since M14-P2: pinned name/driver/attachable). All service-name lookups
(`db`, `identity`, …) go through Docker's embedded DNS at `127.0.0.11`.

Bring-up (secrets live in the macOS launchd env on this machine):

```sh
cd deploy
for v in HARNESS_MASTER_KEY HARNESS_ALLOW_DEV_MASTER_KEY DEEPSEEK_API_KEY \
         DEEPSEEK_BASE_URL DSH_MODEL GITHUB_CLIENT_ID GITHUB_CLIENT_SECRET \
         FRONTEND_BASE GITHUB_REDIRECT_URI POSTGRES_PASSWORD WALG_IMAGE_TAG; do
  val="$(launchctl getenv "$v" 2>/dev/null)"; [ -n "$val" ] && export "$v=$val"
done
export HARNESS_ALLOW_DEV_MASTER_KEY="${HARNESS_ALLOW_DEV_MASTER_KEY:-1}"  # F9 escape hatch, LOCAL DEV ONLY
docker compose --profile walg -f docker-compose.yml up -d
```

Boot refuses without a master key (`deploy/runners/services.py` F9 fail-fast);
the dev opt-in above is the sanctioned local path. Never set it in prod.

---

## 2. Incident: backend DNS crash-loop

### 2.1 Symptom

- `docker ps` shows the container flapping `Restarting (1)` with a seconds-old
  timestamp; `docker inspect <c> --format '{{.RestartCount}}'` climbs forever
  (live 2026-08-24: legacy `harness-backend-1` at **294**).
- Logs end in:
  `socket.gaierror: [Errno -3] Temporary failure in name resolution`
  raised from `getaddrinfo` while resolving a service name (`db`).
- Container's healthcheck (if any) goes `unhealthy`; dependent UI shows dead APIs.

### 2.2 Diagnose (in order)

```sh
# 0. Confirm it is really DNS, not an app bug:
docker logs --tail 100 harness-backend-1 2>&1 | grep -i 'name resolution'

# 1. Is the container attached to ANY network? Empty {} is the tell:
docker inspect harness-backend-1 --format '{{json .NetworkSettings.Networks}}'

# 2. What resolver does it see? (needs a running window; retry in the loop gap)
docker exec harness-backend-1 cat /etc/resolv.conf

# 3. Is the expected network alive, and who is on it?
docker network inspect harness_default

# 4. Reproduce the failure mode deterministically (no network => EAI_AGAIN):
docker run --rm --network none python:3.13-alpine \
  python -c "import socket;
try: socket.getaddrinfo('db', 5432)
except socket.gaierror as e: print(e)"
```

Interpretation:

- `Networks: {}` → **Cause A** (orphaned container; see 2.3).
- Attached but resolution still fails while other containers resolve fine →
  **Cause B** (wedged embedded DNS on the network; see 2.4).
- Whole-host symptom, all containers failing → **Cause C** (daemon/upstream; 2.5).

### 2.3 Cause A — orphan container whose service left the compose file

Root cause chain observed 2026-08-22→08-24: a pre-split revision defined a
monolithic `backend` service; a later revision split it into eight services and
dropped the block. Compose no longer owned the old container, so nothing
reattached it after a network recreate → detached (`Networks: {}`) → no embedded
DNS → `getaddrinfo("db")` → EAI_AGAIN → entrypoint exits 1 → instant restart →
forever loop. `restart: unless-stopped` cannot help: every restart re-enters the
same state.

Fix (executed 2026-08-24):

```sh
docker rm -f harness-backend-1          # safe: not part of the split topology
```

Prevention (shipped same day): the service is **declared again** in
`deploy/docker-compose.yml` behind the `legacy-backend` profile, with explicit
`networks: [default]`, `dns: [127.0.0.11, 8.8.8.8]`, a `/health` healthcheck and
`restart: unless-stopped`. An operator can still run the monolith
(`--profile legacy-backend up -d backend`) but compose now always owns it — the
orphan state is structurally impossible. Rule: **never remove a service block
while its container exists; migrate or remove the container in the same change.**

### 2.4 Cause B — wedged embedded DNS on an otherwise healthy network

The failure mode VT-2/VT-5 documented in W2: dependency churn wedges the
network's resolver; app crashes on lookup; restart races the wedge. Manual
repair, in order of increasing disruption:

```sh
cd deploy
# 1. Stop EVERYTHING attached, including profile sidecars (walg-db holds an
#    endpoint too — forgetting it makes the next step fail):
docker compose --profile walg -f docker-compose.yml down

# 2. Recreate the network (drops stale DNS state); volumes are NOT touched
#    (never add -v here — that is live pgdata):
docker network rm harness_default 2>/dev/null || true

# 3. Bring the stack back (see bring-up block in §1 for the env vars):
docker compose --profile walg -f docker-compose.yml up -d
```

Field notes from the 2026-08-24 execution:

- If step 1 was partial, `up` can fail twice: first with
  `network harness_default has active endpoints (name:"harness-walg-db-1")`
  (a profile sidecar still attached), then — after stopping it mid-flight — with
  `container … is not connected to the network harness_default` (stale-endpoint
  race during teardown). Both resolve by completing the FULL
  `--profile walg down`, then `up` fresh. Do not try to patch half-torn-down state.
- `migrate` exiting 1 with "Refusing to start: set HARNESS_MASTER_KEY…" means
  your shell lacks the operator env (§1 bring-up block), not a schema problem.

### 2.5 Cause C — daemon/upstream level (last resort)

Only if 2.3 + 2.4 leave lookups failing from freshly started containers:
restart Docker Desktop / the daemon. Expect the whole stack back on its own
afterwards (`restart: unless-stopped` everywhere); verify with §2.6.

### 2.6 Verify recovery

```sh
# Embedded DNS answers from inside a real service container:
docker exec harness-identity-1 python -c \
  "import socket; print(socket.gethostbyname('db'))"

# Every API edge answers:
for p in 8001 8002 8003 8004 8005 8006 8008; do
  printf "%s: " "$p"; curl -s -o /dev/null -w '%{http_code}\n' "http://localhost:$p/health"
done
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000   # frontend: 200

# Config still valid on every profile path:
docker compose -f deploy/docker-compose.yml config -q
docker compose -f deploy/docker-compose.yml --profile legacy-backend config -q
```

---

## 3. Healthchecks & restart semantics (M14-P2 contract)

Every long-running service carries both a healthcheck and a restart policy.
Semantics, precisely:

- **Restart policy** (`unless-stopped` on db, all seven split backends,
  frontend, legacy backend, events-retention) fires on *process exit*.
  It survives daemon/host restarts unless you stopped the container yourself.
  `"no"` on migrate/walg: they are one-shots whose exit code is signal, not fault.
- **Healthcheck** marks `healthy`/`unhealthy` for `depends_on: service_healthy`
  gating and humans; Docker does NOT restart a merely-unhealthy-but-running
  container. The backends' supervisor (`entrypoint.sh`) exits nonzero when a
  child uvicorn dies, which converts internal death into a process exit the
  restart policy can act on — that pair (supervisor exit + policy) is the
  effective "unless-alive guard".
- Legacy backend healthcheck probes `http://127.0.0.1:8001/health` (identity,
  the first listener the all-in-one boots) with `interval 15s / timeout 4s /
  retries 12 / start_period 30s` — tolerant of the monolith's slower boot.
- Split backends use identical probes against their own ports at 5s interval.

### 3.1 Two probes: liveness vs readiness (M20-T3, S-GAMMA OPS-06)

Every split backend answers TWO probe routes with different jobs:

| Route | Job | Touches db? | Failure meaning |
|---|---|---|---|
| `/health` | LIVENESS — process can serve | never | process wedged/dead → supervisor exit + restart policy |
| `/ready` | READINESS — fit for traffic | yes (`SELECT 1`) + drift gate | dependency down/schema mismatch → NOT-ready, container stays RUNNING |

`/ready` gates on (1) db reachable through the service's own pooled factory
and (2) `schema_migrations ⊆ this image's migration chain`
(`deploy/runners/migrate.check_drift`, the same law entrypoint.sh enforces at
boot, re-evaluated per probe). Any failed check ⇒ HTTP 503 with the reasons.

The compose healthchecks hit **`/ready`** at `interval 10s / timeout 5s /
retries 6 / start_period 20s`. Consequence, precisely:

- db dies ⇒ backends report `unhealthy` for humans/load balancers within
  ~1 minute while their uvicorn processes keep serving `/health` 200. There
  is NO restart storm: Docker does not restart a merely-unhealthy container,
  and nothing in the app tier exits just because a query fails. When db
  returns, the next probe flips them back to healthy.
- The frontend probes a cheap TCP connect to its own port (its hard
  dependency, identity, is itself gated by `/ready`). This closes OPS-05:
  frontend was the one long-running service with no healthcheck at all.
- The legacy all-in-one keeps probing identity's `/health` on 8001 (the first
  listener its monolith supervisor boots); its restart semantics are owned by
  the supervisor-exit + policy pair described above.

Resource limits (OPS-03) and log rotation caps (OPS-04) live in
`docker-compose.yml`: every service carries `deploy.resources.limits`
(mem/cpu/pids; db = 1g/1.0/512, orchestrator+sandbox = 768m/1.0/256 for
engine subprocesses, app tier = 384m/0.50/128, frontend = 512m/0.50/192,
jobs/sidecars = 256m/0.25/64) and a `json-file` logging driver capped at
`max-size=10m, max-file=3` (≤30 MiB per container, ever). Knobs are
env-tunable — see `.env.example` "Container resource limits".

## 3.2 What /ready does and does NOT detect (ops acceptance, part 28)

Verified by simulation against a real Postgres (evidence:
`docs/briefs/evidence/s1/ops-acceptance.md`):

| Failure | `/health` | `/ready` | Where you see it |
|---|---|---|---|
| Postgres unreachable | 200 | 503 `{"not_ready":["db-unreachable"]}` | compose health, probes |
| Schema ahead of image (rolled-back code) | 200 | 503 `{"not_ready":["schema-drift"]}` | compose health, probes |
| App process wedged | no answer | no answer | supervisor exit + restart policy |
| Orchestrator queue worker dead / runs stalled | 200 | **200 (not probed)** | `harness_oldest_queued_run_age_seconds`, `harness_stale_running_claims` ([alerting.md](alerting.md) rule f) |
| Event delivery stalled (bus not committing) | 200 | **200 (not probed)** | `harness_event_bus_lag_seconds` (rule b) |

There is no Redis and no external broker in this stack: the run queue is the
Postgres `run_claims` table, the event bus is Postgres (`agent_events` +
`pg_notify`), and the "worker" is the `RunQueueWorker` task inside the
orchestrator process. So "queue/worker down" is observed through metrics, not
through `/ready`.

## 3.3 Stalled runs / broken event delivery

Symptoms: run stuck `QUEUED`, UI not updating, alert `RunQueueStalled`,
`StaleRunningClaims` or `EventBusLag`.

```sh
# 1. Queue truth (oldest first). run_claims.state: queued | running | orphaned
docker compose -f deploy/docker-compose.yml exec db psql -U harness -c \
  "SELECT state, count(*), min(claimed_at), min(heartbeat_at) FROM run_claims GROUP BY state;"
# 2. Metrics straight from the orchestrator
curl -s localhost:8004/metrics | grep -E 'harness_(queue_depth|oldest_queued|stale_running|event_bus_lag)'
# 3. Is the worker loop alive / erroring?
docker compose -f deploy/docker-compose.yml logs --since 15m orchestrator | grep -E '"level":"(ERROR|WARNING)"'
# 4. Newest event actually committed (bus lag):
docker compose -f deploy/docker-compose.yml exec db psql -U harness -c "SELECT max(ts) FROM agent_events;"
```

Decision: queued claims old + worker logs silent -> `docker compose restart
orchestrator` (queued claims survive in Postgres and are claimed by the new
worker; stale `running` claims are released by the zombie reaper and their runs
go BLOCKED with `engine_failed reason=heartbeat_lost` for a human to re-drive,
never silently re-executed).
Queued claims old + team at cap -> expected, see
[engine-concurrency.md](../engine-concurrency.md). `max(ts)` stale while runs
are executing -> DB connectivity / `pg_notify` listener errors in orchestrator
logs; fix DB first.

## 3.4 Correlating a request through the logs

Every backend logs one JSON line per request (`HARNESS_LOG_FORMAT=json`,
default) and echoes/mints `X-Request-ID` (send your own to trace). Other log
lines emitted while that request is being served inherit it as `requestId`.

```sh
curl -si -H 'X-Request-ID: trace-42' localhost:8004/health | grep -i x-request-id
docker compose -f deploy/docker-compose.yml logs orchestrator | grep '"requestId":"trace-42"'
```

Run ids appear in the message text of orchestrator lines (`run <id> ...`);
grep for the run id, not a structured field. Collaboration actions carry their
client `request_id` in the `run_collab_events` table (idempotency key), not in
log lines: `SELECT * FROM run_collab_events WHERE request_id = '<id>';`.
Background-worker log lines (outside any request) have `requestId: null`.

## 3.5 Concurrency limits (documented vs enforced)

See [engine-concurrency.md](../engine-concurrency.md) for the full table.
Enforcement status as audited:

| Limit | Enforced where | Status |
|---|---|---|
| Per-team running runs (`HARNESS_RUN_CONCURRENCY`, 4) | `run_queue.py::claim_next_run` (claim SQL + PG advisory lock) | enforced, incl. concurrent claimers (lock added 2026-10-18; before it, 16 parallel claimers overshot cap 4 to 12-16) |
| Engine executor width (`ENGINE_MAX_CONCURRENCY`, 8) | `engine_runtime.py` ThreadPoolExecutor | enforced |
| Per-run deadline / provider breaker | `engine_runtime.py` | enforced (thread keeps running after deadline; see engine-concurrency.md) |
| Tier caps (Pro 8 / Team 16 concurrent runs, `PRICING.md`) | - | NOT enforced: marked `[M2]`, only the single env-wide cap exists |

## 3.6 Which model did this run use? (W15)

Routing is opt-in per team (W14), so "why did this run use the cheap model?"
needs a durable answer rather than a log line. Every execution attempt writes a
`model_routed` agent event BEFORE the engine call, and `GET /runs/{id}` returns
it as `routing`:

```sh
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8004/api/v1/orchestrator/runs/<runId> | jq '.routing'
# {"class":"mechanical","enabled":true,"model":"cheap/model","runId":"…","source":"policy"}
```

Read `source`, not just `model`:

| `source` | Meaning |
|---|---|
| `policy` | the team's routing policy chose this model for this plan class |
| `primary` | routing is off for the team; the configured primary model ran |
| `policy-fallback` | routing is on but the policy has no model for this plan class, so the primary ran — routing never downgrades or invents a model |
| `null` (`routing` absent) | no attempt was recorded: the run predates W15 or never started executing. Absence is NOT evidence that routing was off |

The same fact straight from the store, when the API is not the question:

```sh
docker exec harness-db-1 psql -U harness -d harness -c \
  "select seq, ts, payload from agent_events where run_id='<runId>' and type='model_routed' order by seq;"
```

One row per attempt: a resume re-resolves the policy, so the newest row is the
one that describes the newest attempt (the aggregate returns the newest).
Reproduce the whole check with `make drill-routing` — it uses probe model ids
that exist in no catalog, so no provider call can succeed and no money can be
spent, while still proving the decision is recorded before the attempt dies.

## 4. Prevention checklist

- [ ] No service removed from compose while its container runs (Cause A rule).
- [ ] Default network stays explicitly declared (pinned name/driver/attachable).
- [ ] `docker network rm` only during a full maintenance window, all profiles
      down, never against a running stack.
- [ ] Prefer `stop`/`start` over `kill` for db; let policies bring things back.
- [ ] After ANY network surgery: run the §2.6 verify block before walking away.
