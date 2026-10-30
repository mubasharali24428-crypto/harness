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

## 3.7 A seat was revoked and the person still got in (W16)

`users.is_active` is the authority. Until W16 it was a hint — it steered
notification targeting only — so a deactivated account could request a fresh
login code and keep acting. Five doors resolve a principal in this codebase and
all five now consult the seat:

| Door | Code | Behaviour on a revoked seat |
|---|---|---|
| Section 1 HTTP bearer | `harness_identity/auth.py get_principal` | 401 `invalid or expired session` (no oracle: a bearer surface must not reveal why) |
| Every other service | `harness_contracts/fastapi_auth.py make_bearer_dep` (sections 2/8/11, the deployed runners, the extensions server) | same 401, via the `JOIN users … is_active` term |
| Interactive OTP / legacy login | `auth.py login_or_register` | 403 `ACCOUNT_DISABLED` — deliberately actionable, the caller already proved inbox control |
| OIDC SSO | `sso.py _map_email_to_member` | 403 `ACCOUNT_DISABLED` vs 403 `SSO_EMAIL_NOT_MEMBER` (invite problem and seat problem are different queues) |
| WebSocket + its ticket | `app.py _principal_from_token`, `ephemeral.py redeem_ws_ticket` | socket close 4401 / ticket returns nothing |

Diagnose in one line — the store, not the app, is the witness:

```sh
docker exec harness-db-1 psql -U harness -d harness -c \
  "select id, email, is_active from users where email = 'someone@example.com';"
```

If `is_active` is true and the person should be out, the revocation never
happened: check whether the caller removed the `team_members` row (that also
kills the token, by membership) or the account itself. `_revoke_access` deletes
session rows on org removals; the seat rule is the backstop for any path that
forgets to.

Prove all of it on a live stack with `make drill-seat-revocation` — it uses a
throwaway user, never deletes a session row, and shows the SAME token go 200 →
401 on identity and 404 → 401 on a service using the shared dependency, then
200 again after re-enabling the seat.

## 3.8 An IdP group changed and the person's role did not (W16)

Group-mapped SSO is opt-in per team. A login consults the map ONLY when the
team has both `groupsClaim` and at least one `groupRoles` rule; otherwise the
role on the `team_members` row is used exactly as before. When a mapping does
exist, every group pattern that matches the claim is considered and the
STRONGEST role wins (`guest < viewer < engineer < admin`), so a person in both
`harness-viewers` and `harness-admins` is an admin. A claim that matches nothing
leaves the stored role alone — silence is not demotion, because a broken claim
mapper at the IdP must not lock a whole team out.

"Which rule won, and did it fire?" is answered by the login's audit row, not by
the IdP:

```sh
docker exec harness-db-1 psql -U harness -d harness -c \
  "select ts, detail from audit_log where action = 'sso.login'
   order by seq desc limit 5;"
```

`detail.source` is `claim` when a group decided the role, `membership` when
the stored role stood, and `membership_last_admin` when the claim lost to the
last-admin invariant; `detail.role` is the role actually used; `detail.matched`
lists the group names that matched (names only — no token, no email). A role
that actually changed also leaves a `member.role_changed` row with
`source: sso_claim`.

Diagnose in order:

1. **Is the mapping configured?** `GET /api/v1/teams/{team}/sso/config` must
   show non-empty `groupsClaim` and `groupRoles`. A PUT with rules but no claim
   name is refused (`422 VALIDATION_ERROR`) rather than stored as dead config.
2. **Does the ID token carry the claim?** Keycloak only emits groups when the
   client has a group-membership mapper (`deploy/keycloak/harness-realm.json`,
   claim name `groups`). The drill adds it idempotently if a long-lived IdP
   predates the realm file.
3. **Does the group name match?** Patterns are case-sensitive `fnmatch`
   globs (`*`/`?` allowed); `harness-admins` does not match `Harness-Admins`.
4. **Was the person in the group at login time?** Membership is read from the
   token minted for THAT login; a seat is not retroactively re-roled when the
   directory changes. The next login applies the new map.
5. **Is the seat disabled?** A deactivated account is refused before any role
   mapping (§3.7), regardless of group membership.

The live proof is the Keycloak drill's group leg: join the admin group → login
is `admin` with `source=claim` and the minted token passes the admin-only
`/audit` door; leave it → the baseline group gives `engineer` with
`source=claim` and `/audit` answers 403 (`scripts/oidc_drill.py`, evidence in
`docs/briefs/evidence/pilot-prep/oidc-keycloak-drill.json`).

## 3.9 "We deprovisioned them in Okta and they are still in Harness" (W16)

SCIM is opt-in per team and reaches the product through two separate surfaces:

| Surface | Path | Credential |
|---|---|---|
| Token management (our API) | `POST/GET/DELETE /api/v1/teams/{id}/scim/tokens[/{tid}]` | an admin's session bearer, Team tier |
| The directory protocol | `GET /scim/v2/Users[/{id}]`, `PATCH`, `PUT` | a `hsc_…` SCIM token, nothing else |

Work the failure from the connector inward:

1. **Is the connector talking to the right URL?** The path is `/scim/v2/Users`,
   not `/api/v1/scim/...`; `deploy/Caddyfile` routes it to identity. A 204 from
   the proxy's catch-all means the request never reached the service.
2. **Is the credential alive?** `GET /api/v1/teams/{id}/scim/tokens` lists live
   and revoked tokens with `lastUsedAt`. A token that never shows a use means the
   connector is authenticating somewhere else. Revoked and unknown answer the
   same 401 — the service does not say which.
3. **Did the write land on a seat this team has?** SCIM here reads and narrows
   seats; it never creates one. An `id` the team does not contain answers
   `404 user not found`, the same bytes as a nonsense id.
4. **Was it refused rather than ignored?** The two refusals worth reading in the
   log: `403` "cannot deprovision the team's last admin", and `400 invalidPath`
   for an attribute this service does not own (`name.givenName`, `groups`, …) —
   refused on purpose, because a connector that believes it wrote a field it did
   not is worse informed than one that got an error.
5. **What did the mutation actually do?** `active=false` sets `users.is_active`
   and deletes that team's `auth_sessions` rows:

```sh
docker exec harness-db-1 psql -U harness -d harness -c \
  "select ts, action, detail from audit_log
    where action in ('scim.user_changed','scim.token_created','scim.token_revoked')
    order by seq desc limit 10;"
```

`detail.kind` is `deprovision` / `reactivate` / `rename` / `link`, `detail.refId`
is the SCIM token id (the actor is a credential, so `actor_kind` is `system` and
there is no human actor), and `before`/`after` carry the changed attributes.

**Scope of `active=false`, stated plainly:** `users` describes a human, not a
seat, so disabling through one team's directory disables that human at every
door of every team — which is what "the source of truth says they left the
company" means. Row cleanup (`auth_sessions`) is scoped to the team the
credential belongs to. If a person must lose only one team's seat, use the
membership API (`DELETE /api/v1/teams/{id}/members/{uid}`), not SCIM.

**Error bodies are SCIM, not ours.** `/scim/v2` failures return the RFC 7644
object (`schemas`/`status`/`detail`, `scimType` where defined) and a malformed
body answers `400 invalidSyntax` rather than our 422 envelope. The envelope
middleware keeps normalizing everything else — the carve-out keys on the SCIM
schema URI only (`envelope_middleware.py:_is_scim_error`).

Prove it end to end with `make drill-scim`: 36 checks including the seat's own
session token going 200 → 401 on identity and 404 → 401 on the shared
dependency after a single PATCH, with no admin touching Harness in between.

## 3.10 The SIEM stopped receiving the trail (W16)

A sink is an outbound push, not a pull, so "the SIEM shows nothing new" is usually
one of three things: the worker is not running, the collector is refusing, or the
destination stopped being allowed. Work it in that order.

1. **Is `siem-push` up, and is it the version you think?**
   `docker compose -f deploy/docker-compose.yml ps siem-push` and
   `docker logs harness-siem-push-1 --tail 50`. The loop logs a batch line only
   when it did something; silence on an idle stack is normal, silence on a team
   with a growing `pendingRows` is not.
2. **Read the sink, not the collector.** The admin listing carries the whole
   story for each sink:

```sh
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://127.0.0.1:8001/api/v1/teams/$TEAM_ID/siem/sinks | python3 -m json.tool
```

| Field | What it means |
|---|---|
| `pendingRows` | rows this team still owes the collector past `cursor` |
| `attempts` | consecutive failed deliveries; 0 means healthy |
| `lastStatusCode` / `lastError` | the collector's answer (or `OSError`/`ValueError` for transport/egress) |
| `nextAttemptAt` | when the next try is due — backoff is `min(60·2^(n-1), 3600)` |
| `state` | `active`, `sending` (claimed right now), or `blocked` |
| `deliveredRows` | rows accepted by the collector over the sink's life |

3. **`blocked` means eight consecutive failures and no more sends.** Fix the
   cause (collector back, DNS, allow-list), then resume explicitly:
   `PATCH /api/v1/teams/{id}/siem/sinks/{sinkId}` with
   `{"resume": true, "expectedVersion": <version>}`. The backlog goes out in
   order; nothing was dropped while it was blocked.
4. **`lastError: ValueError` is our egress rule, not the collector.** The URL is
   HTTPS, port 443, and its host must be on `HARNESS_SIEM_ALLOWED_HOSTS` **with a
   public DNS answer** at delivery time. An empty allow-list refuses every
   destination by design; a host that resolves to `10./172./192.168.` is refused
   even if it was allow-listed (rebind-after-save). The same knob must be present
   on the `identity` service, because that is where registration is validated —
   if it is only on the worker, creating the sink fails with 422 and nothing is
   ever attempted.
5. **A re-created sink starts at the newest row.** `startFrom` defaults to
   `latest`; use `startFrom: "earliest"` when registering if the team wants the
   retained history replayed. Delivery is at-least-once and every batch carries
   `Idempotency-Key: <sinkId>:<from>-<to>`, so a collector should dedupe on that
   key rather than on row content.
6. **What did we actually send?** The same rows an export would give:

```sh
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://127.0.0.1:8001/api/v1/audit/export?format=ndjson&afterSeq=$CURSOR&limit=50"
```

7. **Can the collector prove it was us?** Every POST carries
   `X-Harness-Signature: t=<ts>,v1=<hex>` where the hex is
   `hmac_sha256(sharedSecret, "<ts>." + body)`. Verify it with the secret from
   your vault (the API only ever shows a hint). Reject timestamps older than your
   tolerance — we do not retry with an old `t`.

**Verifying the trail itself.** `POST /api/v1/audit/verify` with
`{"afterChainPos": N, "maxRows": M}` returns a per-row verdict, the chain head,
and `prunedPrefix`/`anchored` when retention removed the row before the window.
Two honest limits: it needs the deployment's PostgreSQL (SQLite answers `503
AUDIT_CHAIN_UNAVAILABLE` rather than a fake `ok`), and it is tamper-evident — a
privileged attacker who re-seals every row after an edit is invisible to a chain
check and only the external anchors catch it. `ok: false` names the first
offending rows in `problems`; that is an integrity incident, not a support
ticket.

Prove the whole path with `make drill-siem` (59 checks): registration and its
refusals on the live stack, a real verdict from the deployment's Postgres, the
deployed worker failing a dead collector without moving the cursor, and — on a
throwaway database with a real HTTP collector — signed delivery, replay dedupe,
backoff → blocked → resume and the tamper/pruned-prefix verdicts.

## 4. Prevention checklist

- [ ] No service removed from compose while its container runs (Cause A rule).
- [ ] Default network stays explicitly declared (pinned name/driver/attachable).
- [ ] `docker network rm` only during a full maintenance window, all profiles
      down, never against a running stack.
- [ ] Prefer `stop`/`start` over `kill` for db; let policies bring things back.
- [ ] After ANY network surgery: run the §2.6 verify block before walking away.
