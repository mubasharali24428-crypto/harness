# Runbook — Alerting (Mission 20, lane T2; rules file added in session 2)

> Status: `deploy/observability/alerts.yml` exists and is loaded by
> `rule_files` in `deploy/observability/prometheus.yml`. Metrics are exposed
> by every backend at `GET /metrics` and scraped by the `prometheus` compose
> service (profile `observability`). **alerts.yml is the source of truth for
> thresholds**; the PromQL blocks in section 3 mirror the original rules a-f
> and are kept for the explanation and the structural test. Firing rules are
> emailed by Alertmanager (section 7); rules without an emitter never fire (section 5).

## 1. Loading the rules

Nothing to add in compose: the `prometheus` service bind-mounts
`./observability` at `/etc/prometheus`, so `alerts.yml` lands at
`/etc/prometheus/alerts.yml`, which `prometheus.yml` lists under `rule_files`.

```sh
docker compose -f deploy/docker-compose.yml --profile observability up -d prometheus
# UI: http://localhost:9090/alerts
```

Validation (both were run in session 2, see `docs/briefs/evidence/s2/lane-d.md`):

- with Docker (image `prom/prometheus:v2.53.0`, the one compose pins):
  `docker run --rm --entrypoint promtool -v "$PWD/deploy/observability":/cfg prom/prometheus:v2.53.0 check rules /cfg/alerts.yml`
  and `... check config /cfg/prometheus.yml` (mount at `/etc/prometheus` for the
  config check so `rule_files` resolves).
- without Docker: `section1/tests/test_alert_rules_load.py` (valid YAML, required
  keys, balanced `expr`, severity label, `runbook` annotation that resolves to an
  existing file and heading anchor, `rule_files` points at the file) and
  `section1/tests/test_deploy_observability.py` (section 3 blocks). These are
  structural checks, not a PromQL parse; promtool is the real parser.

## 2. Scrape topology (what exists after M20-T2)

| Service | Port | `/metrics` | Domain series |
|---|---|---|---|
| identity | 8001 | yes | golden signals only |
| github | 8002 | yes | golden signals only |
| models | 8003 | yes | golden signals only |
| orchestrator | 8004 | yes (+legacy `/api/v1/orchestrator/metrics`) | queue depth, event-bus lag, pool saturation, runs started/completed |
| sandbox | 8005 | yes | golden signals only |
| meeting | 8006 | yes | golden signals only |
| review | 8008 | yes | golden signals only |

Golden signals on every service: `http_requests_total{method,path,status}`
(counter), `http_request_duration_seconds` (histogram).

Orchestrator domain series:

- `harness_queue_depth` — run_claims rows in state='queued'
- `harness_event_bus_lag_seconds` — seconds since newest agent_events row
- `harness_oldest_queued_run_age_seconds` — age of the oldest `queued` claim
  (0 when none); the dead-worker / stalled-run signal
- `harness_stale_running_claims` — `running` claims whose heartbeat is older
  than `HEARTBEAT_TIMEOUT_SECONDS` (default 90s) and not yet reaped
- `harness_db_pool_checked_out_connections` — histogram over QueuePool
  checked-out connections (buckets 2/4/6/8/10/12; pool math ceiling is 12
  per service, see docs/pool-math.md)
- `harness_runs_started_total`, `harness_runs_completed_total{outcome}`

Known limitation (honest): Postgres internals (`pg_stat_archiver`,
backup freshness) are NOT directly scraped — no postgres_exporter sidecar was
added in this lane (no new heavy deps). The two backup alerts therefore watch
the signals the platform itself emits; see §4 d/e.

## 3. The alert rules (a-e original; f added by ops acceptance, part 28)

### Request latency objective

`HarnessRequestLatencySloBurn` watches the route-level p95 of
`http_request_duration_seconds` per backend and path. It warns when the p95
exceeds 250 ms for 10 minutes. This is a sustained breach signal for the
proposed target in [slo-and-capacity.md](slo-and-capacity.md), not a complete
availability SLI; low-volume routes and provider-execution time need separate
interpretation. The alert evaluates in Prometheus only until a delivery route
is configured.

### a) Queue depth (runs stuck waiting)

```promql
harness_queue_depth > 50
```

- **for:** 10m — brief spikes during bursts are normal; sustained depth is not.
- **severity:** warning (>200 for 30m ⇒ page).
- **why 50:** executor width is ENGINE_MAX_CONCURRENCY=8 with per-team cap 4;
  depth above ~6× width means producers outpace execution for minutes.
- **runbook:** [engine-concurrency.md](../engine-concurrency.md) — worker
  liveness first (`orchestrator` logs for "worker" lines), then
  HARNESS_RUN_CONCURRENCY.

### b) Event-bus lag (event pipeline stalled)

```promql
harness_event_bus_lag_seconds > 300
  and on() (sum(harness_queue_depth) > 0 or sum(increase(harness_runs_started_total[15m])) > 0)
```

- **for:** 5m.
- **why 300s:** archive_timeout is 60s and the busiest table writes on every
  run; five minutes of zero event inserts means the bus/orchestrator stopped
  committing (or the DB is down — which also fires other signals).
- **note:** the gauge is "seconds since the newest agent_events row", so it
  grows without bound on an idle system. The `and on()` clause restricts the
  alert to periods with queued claims or runs started in the last 15m (session 2
  fix; the earlier rule would have fired permanently when nobody was running).
- **runbook:** [ops.md](ops.md) — check orchestrator↔db connectivity
  (`pg_stat_activity`), EventBus pg_notify listener errors in logs.

### c) DB pool saturation (connection exhaustion imminent)

```promql
histogram_quantile(1,
  sum by (le, job) (rate(harness_db_pool_checked_out_connections_bucket[5m]))
) >= 12
```

- **for:** 5m.
- **why 12:** pool_size=8 + max_overflow=4 = 12 per service is the hard
  ceiling from docs/pool-math.md; touching it means requests start queueing
  on pool.acquire() (latency cliff), and PG max_connections=120 leaves only
  ~36 slots of global headroom.
- **per-service variant:** append `{instance="models:8003"}` etc. (one scrape
  job, so `job` does not split services); the ledger counts
  models' extra cost-ledger pool separately (OPS-07).
- **runbook:** [pool-math.md](../pool-math.md) — find the leaking consumer via
  `pg_stat_activity` grouped by application/client_addr.

### d) Backup freshness (base backups stopped)

```promql
time() - harness_backup_last_success_timestamp_seconds > 90000
```

- **for:** 0m (fires immediately once stale).
- **why 25h:** one daily base backup cadence (backup.sh `base` mode); a full
  day + one hour of silence means last night's wrapper never succeeded.
- **emitting side (Part 28, session 12):** `deploy/runners/backup.sh` inserts a `backup_runs` row
  (migration 0059) after its storage post-check proves the base backup landed; the orchestrator's
  domain poll exposes the newest row as this gauge (`observability._read_backup_gauges`,
  `section1/tests/test_backup_metrics_pg.py`). A failed insert is logged and the alert then fires.
- **never ran:** with no row at all (a stack that never ran `walg`) the series is absent and this rule
  cannot fire. `HarnessBackupNeverRan` covers that case: `absent(harness_backup_last_success_timestamp_seconds)`
  for 30m (warning). It also fires while the orchestrator is not exporting the gauge, so check
  `HarnessServiceDown` first.
- **runbook:** [backup-and-pitr.md](backup-and-pitr.md) — verify wal-g storage
  inventory (`BACKUP_MODE=list`), then pg_stat_archiver on the live db.

### e) Archiver failure count (WAL not leaving the primary)

```promql
increase(harness_pg_wal_archive_failed_count_total[15m]) > 0
```

- **for:** 0m.
- **why:** any failed_count increase means WAL segments are piling up inside
  pgdata (RPO silently growing toward total-loss-on-volume-failure, exactly
  finding OPS-01's 3am story).
- **emitting side (Part 28, session 12):** the orchestrator's domain poll reads
  `pg_stat_archiver.failed_count` every `HARNESS_METRICS_DB_POLL_SECONDS` and exposes it as
  `harness_pg_wal_archive_failed_count_total` (a monotone counter; a server restart resets it,
  which `increase()` tolerates).
- **runbook:** [backup-and-pitr.md](backup-and-pitr.md) §"Archiving" — check
  `archive_command` stderr in db logs, storage prefix writability, then
  `SELECT * FROM pg_stat_archiver`.

### f) Stalled runs / dead queue worker

```promql
harness_oldest_queued_run_age_seconds > 300
```

- **for:** 2m.
- **why:** `harness_queue_depth` cannot see ONE stuck run (depth 1 never
  crosses 50). A queued claim older than 5 minutes means the
  `RunQueueWorker` is dead/wedged, every team sits at its per-team cap, or the
  executor is saturated (`ENGINE_MAX_CONCURRENCY`). Normal admission latency is
  under `HARNESS_WORKER_POLL_SECONDS` (0.5s).
- **runbook:** [ops.md](ops.md) "Stalled runs".

```promql
harness_stale_running_claims > 0
```

- **for:** 3m.
- **why:** the zombie reaper (`release_timed_out_claims`) deletes stale
  `running` claims and BLOCKs their runs (`heartbeat_lost`) every poll. A stale claim that PERSISTS for minutes means the
  reaper (i.e. the orchestrator worker loop) is not running.
- **runbook:** [ops.md](ops.md) "Stalled runs".

## 4. Alert → action quick table

| Alert | Threshold | First actions |
|---|---|---|
| HarnessQueueDepthHigh | >50 for 10m | worker alive? concurrency caps? provider outage? |
| HarnessQueueDepthCritical (page) | >200 for 30m | same, plus tell the pilot team and stop admitting runs |
| HarnessEventBusLag | >300s for 5m | db reachable? orchestrator crash-loop? notify worker stuck? |
| HarnessDbPoolSaturated | ≥12 checked out, 5m | leak hunt via pg_stat_activity; restart worst offender |
| HarnessRequestLatencySloBurn | route p95 >250ms for 10m | compare route volume, DB pool and queue metrics; separate provider wait from API latency |
| HarnessBackupStale | >90000s since last success | run `walg` base manually; inspect walg_logs volume |
| HarnessBackupNeverRan | backup series absent for 30m | no backup ever recorded: run `walg` base; or the orchestrator is not exporting (check HarnessServiceDown) |
| HarnessWalArchiveFailing (page) | any increase in 15m | storage target writable? wal-g creds? disk full? |
| HarnessRunQueueStalled | oldest queued >300s for 2m | orchestrator logs for worker loop; per-team cap; executor saturation |
| HarnessStaleRunningClaims | >0 for 3m | worker/reaper dead: restart orchestrator (stale runs go BLOCKED for re-drive) |
| HarnessServiceDown (page) | `up == 0` for 2m | `docker compose ps`, container logs, ops.md section 2 |
| HarnessHigh5xxRate | >5% 5xx for 10m | logs of the named instance; recent deploy? rollback.md |
| Pilot alerts (section 6) | see section 6 | see section 6 |

## 5. What deliberately did NOT land here

- Backup/WAL gauges (d, e) are emitted from PostgreSQL by the orchestrator (session 12), not by a
  separate exporter; if every orchestrator replica is down they go stale with it (HarnessServiceDown
  covers that case).
- No postgres_exporter / Grafana containers (lane scope: no new heavy deps).
- Delivery is the email address plus one optional webhook (section 7): no pager integration, no
  Alertmanager high availability.
- The legacy `/api/v1/orchestrator/metrics` route keeps serving the
  orchestrator-only Wave 8 collector; both endpoints coexist by design.

## 6. Pilot alerts (session 2)

Defined in `alerts.yml`, group `harness-pilot`; they implement master prompt
part 32 directive 007 (monitor failed approvals, cancellation, isolation,
event delivery). All four read closed-label counters defined in
`section4/src/harness_orchestrator/domain_metrics.py` (event delivery also in
`section1/.../domain_metrics.py`); every series is rendered at 0 from the first
scrape. **No series at all means the service is not being scraped, not that it
is healthy** - check
`curl -s localhost:8004/metrics | grep -c '^harness_isolation_violation_total'`
before relying on them. A test (`test_alert_label_values_are_ones_the_code_emits`)
fails if an alert matches a label value the code never emits.

| Alert | Severity | Counter | Fires when | First actions |
|---|---|---|---|---|
| HarnessApprovalDenialSpike | warning | `harness_approval_denied_total{reason}` | >10 denials in 10m | group by `reason`: stale digest = proposal changed under reviewers; self-approval/role = confusion about who approves; expired = TTL too short. Ask the operator what the team was doing. |
| HarnessRunCancelFailure | warning | `harness_run_cancel_total{outcome}` | `outcome="unconfirmed"` in 15m (kill sent, engine not confirmed gone; `refused` is a normal 409 and does not alert) | open the run, confirm the worker process is gone and the UI says stop is incomplete; see engine-concurrency.md and the stop-truth tests. |
| HarnessIsolationViolation | page | `harness_isolation_violation_total{kind}` | any increase in 5m | treat as a security incident: stop the pilot (pilot.md, stop conditions), stop the affected runs, preserve logs and the sandbox workspace, read `kind` against `docs/briefs/threat-model.md` T15-T17. |
| HarnessEventDeliveryDrops | warning | `harness_event_delivery_total{outcome}` | `outcome="dropped"` in 10m, sustained 5m | teammates may see stale state; check orchestrator logs and DB; polling remains the fallback. |
| HarnessEventDeliveryDuplicates | warning | `harness_event_delivery_total{outcome}` | `outcome="duplicate"` >5 in 10m, sustained 5m | cursor-resume or client dedup fault; collect the run id and cursor values. |

| HarnessNotificationEmitErrors | warning | `harness_notifications_total{status}` | `status="error"` in 15m, sustained 5m | `notify.emit` swallowed an exception (it never raises into the caller); read the orchestrator log for `notify`, check the DB and migration 0046 tables. |

Also in `alerts.yml`: `HarnessServiceDown` (page; `up == 0` for 2m) and
`HarnessHigh5xxRate` (warning; 5xx ratio >5% for 10m on one instance, from
`http_requests_total`). The per-service split is the `instance` label
(`orchestrator:8004`); there is one scrape job, `harness-backends`.

## 6b. Who is paged (pilot rule, decided 2026-10-31)

Decided by the owner, Mubashar Ali, in chat on 2026-10-31:

- **Primary on-call:** Mubashar Ali, for the four `severity="page"` alerts (`HarnessServiceDown`,
  `HarnessIsolationViolation`, `HarnessQueueDepthCritical`, `HarnessWalArchiveFailing`).
- **Backup and escalation: none.** One person for the pilot, written down on purpose. If the primary
  does not respond, nothing escalates; fifteen `warning` alerts are read, not paged.
- **Destination:** a phone-push webhook (`HARNESS_ALERT_WEBHOOK_URL`) plus email
  (`HARNESS_ALERT_EMAIL_TO`), so a sleeping laptop is not the only path. **Decided, not yet
  configured or proven:** the running Alertmanager's email receiver still points at a Mailpit that
  is not running. Set both variables in the gitignored `.env`, recreate Alertmanager, run
  `python3 scripts/e2e/w17_alert_delivery_drill.py` against the real destination and confirm a
  person received it. Until that is done, nothing pages anyone.
- Review with the SLOs on 2026-11-30, or at the first external user, whichever comes first.

## 7. Delivery: Alertmanager emails every alert (and posts to a webhook if set)

`alertmanager` (profile `observability`) receives every firing rule from Prometheus and emails it, reusing the stack's
`HARNESS_SMTP_*` settings. Set the recipient and start both:

```sh
# .env: HARNESS_ALERT_EMAIL_TO=oncall@example.com (plus HARNESS_SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/STARTTLS)
docker compose --env-file .env -f deploy/docker-compose.yml --profile observability up -d prometheus alertmanager
```

- Routing (`deploy/observability/alertmanager.yml`): `page` mails after 10 s and repeats hourly; `warning` repeats every 12 h;
  resolved alerts mail too. Grouped by `alertname` and `instance`.
- The file is a template: the entrypoint substitutes the `${VAR}` placeholders and puts the password in `/tmp/smtp_password`
  (0600). It refuses to start without `HARNESS_ALERT_EMAIL_TO`. Check the rendered file with
  `docker compose ... exec alertmanager amtool check-config /tmp/alertmanager.yml`.
- **Second channel (optional):** set `HARNESS_ALERT_WEBHOOK_URL` and Alertmanager also POSTs each alert (resolved too) to that URL
  in the standard Alertmanager webhook JSON, which Slack-compatible bridges, PagerDuty/Opsgenie relays and chat gateways accept.
  The URL is written to `/tmp/webhook_url` (0600, `url_file`), never into the config. Unset, the receiver is email only.
  A webhook that is down is retried by Alertmanager and shows in `alertmanager_notifications_failed_total{integration="webhook"}`.
- `HARNESS_SMTP_STARTTLS=true` (default) makes Alertmanager require TLS; set it to `false` only for a local catcher.
- Prove delivery after any change: post a test alert
  (`curl -XPOST localhost:9093/api/v2/alerts -H 'Content-Type: application/json' -d '[{"labels":{"alertname":"DeliveryCheck","severity":"warning"}}]'`)
  and confirm the mail arrives. The last full run is `docs/briefs/evidence/s11/alert-delivery.md`.
- **Prove the routing *to a live target* without a mail catcher:** `scripts/e2e/w17_alert_delivery_drill.py`
  renders this same template (enabling the webhook receiver), runs a throwaway Alertmanager, POSTs a
  synthetic `severity="page"` alert and asserts it lands on a local HTTP sink. It proves the mechanism,
  not a chosen destination — a team points `HARNESS_ALERT_WEBHOOK_URL` at their own relay. Stage-2 run:
  `docs/briefs/evidence/w17/w17-alert-delivery-*.json` (config valid, delivered in ~10 s, `status=firing`).
  **A running stack whose only receiver is a dead SMTP catcher delivers nothing** (on 2026-10-31 the live
  `mailpit` was six days dead while `HarnessBackupStale` was firing). Check delivery, not just evaluation.
- Event-freshness alert: `HarnessEventFreshnessSlo` fires when fewer than 99% of live collaboration
  frames land in the 5 s bucket for 10m; see [slo-and-capacity](slo-and-capacity.md#objectives) and
  `scripts/e2e/w17_event_freshness_drill.py` for the end-to-end measurement.
- Silence during maintenance: `docker compose ... exec alertmanager amtool silence add alertname=HarnessServiceDown --duration 30m --alertmanager.url=http://localhost:9093`.

