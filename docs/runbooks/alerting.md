# Runbook — Alerting (Mission 20, lane T2)

> Status: shipped with the M20-T2 observability minimum. Metrics are exposed
> by every backend at `GET /metrics` (Prometheus text format) and scraped by
> the `prometheus` compose service (profile `observability`). This file
> defines the five alert rules the mission plan requires — as ready-to-load
> PromQL with thresholds, plus what to do when each one fires.

## 1. Loading the rules

The five rules live in this file in loadable form; copy the PromQL blocks
into `deploy/observability/alerts.yml` and add it via a bind-mount + flag on
the `prometheus` service when you want them enforced:

```yaml
# deploy/observability/alerts.yml
groups:
  - name: harness-slo
    rules:
      - alert: HarnessQueueDepthHigh
        expr: harness_queue_depth > 50
        for: 5m
        ...
```

```yaml
# compose service addition (prometheus block):
#     volumes:
#       - ./observability/alerts.yml:/etc/prometheus/alerts.yml:ro
#     command:
#       - --config.file=/etc/prometheus/prometheus.yml
#       - --storage.tsdb.retention.time=15d
```
plus `rule_files: [/etc/prometheus/alerts.yml]` in `prometheus.yml`.

Syntactic validation without a running Prometheus (pick one):

- with Docker: `docker run --rm -v "$PWD/deploy/observability":/cfg prom/prometheus:v2.53.0 promtool check rules /cfg/alerts.yml`
- CI fallback: every rule below is kept inside a fenced ```promql``` block in
  this file and is parsed line-by-line by
  `section1/tests/test_deploy_observability.py` (balanced braces/parens,
  known metric names, valid durations). It is a structural check, not a full
  promtool parse — prefer promtool where available.

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
- `harness_db_pool_checked_out_connections` — histogram over QueuePool
  checked-out connections (buckets 2/4/6/8/10/12; pool math ceiling is 12
  per service, see docs/pool-math.md)
- `harness_runs_started_total`, `harness_runs_completed_total{outcome}`

Known limitation (honest): Postgres internals (`pg_stat_archiver`,
backup freshness) are NOT directly scraped — no postgres_exporter sidecar was
added in this lane (no new heavy deps). The two backup alerts therefore watch
the signals the platform itself emits; see §4 d/e.

## 3. The five alert rules

### a) Queue depth (runs stuck waiting)

```promql
harness_queue_depth > 50
```

- **for:** 10m — brief spikes during bursts are normal; sustained depth is not.
- **severity:** warning (>200 for 30m ⇒ page).
- **why 50:** executor width is ENGINE_MAX_CONCURRENCY=8 with per-team cap 4;
  depth above ~6× width means producers outpace execution for minutes.
- **runbook:** [queue-depth.md](queue-depth.md) — worker liveness first
  (`orchestrator` logs for "worker" lines), then HARNESS_RUN_CONCURRENCY.

### b) Event-bus lag (event pipeline stalled)

```promql
rate(harness_event_bus_lag_seconds[10m]) >= 0
and on()
harness_event_bus_lag_seconds > 300
```

- **for:** 5m.
- **why 300s:** archive_timeout is 60s and the busiest table writes on every
  run; five minutes of zero event inserts means the bus/orchestrator stopped
  committing (or the DB is down — which also fires other signals).
- **note:** the gauge is monotonically non-decreasing between commits, so a
  plain threshold is the real signal; the rate clause documents intent and
  keeps the alert anchored to a windowed evaluation.
- **runbook:** [eventbus.md](eventbus.md) — check orchestrator↔db connectivity
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
- **per-service variant:** append `{job="models"}` etc.; the ledger counts
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
- **emitting side:** `deploy/runners/backup.sh` appends to the walg_logs
  volume; expose the timestamp of the last successful base backup as this
  gauge (a tiny textfile-sidecar or log-tailer writing
  `harness_backup_last_success_timestamp_seconds` into a node-level scrape is
  the intended producer — the gauge NAME is contractual here, its emitter is
  operator-pluggable until the exporter lane lands).
- **fallback while no exporter runs:** host cron check per OPS-02:
  `[ $(( $(date +%s) - $(stat -f %m "${WALG_FILE_PREFIX}/base_$(ls ...)" ) )) -gt 90000 ] && echo ALERT`
  against the storage prefix mtimes.
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
- **emitting side:** same textfile/exporter contract as (d):
  `harness_pg_wal_archive_failed_count_total` mirrors `pg_stat_archiver.
  failed_count` (monotone counter). Until the exporter lane lands, a 1-minute
  cron can push the value via the Pushgateway or fail loudly to mail — the
  alert expression stays identical either way.
- **runbook:** [backup-and-pitr.md](backup-and-pitr.md) §"Archiving" — check
  `archive_command` stderr in db logs, storage prefix writability, then
  `SELECT * FROM pg_stat_archiver`.

## 4. Alert → action quick table

| Alert | Threshold | First actions |
|---|---|---|
| QueueDepthHigh | >50 for 10m | worker alive? concurrency caps? provider outage? |
| EventBusLag | >300s for 5m | db reachable? orchestrator crash-loop? notify worker stuck? |
| PoolSaturation | ≥12 checked out, 5m | leak hunt via pg_stat_activity; restart worst offender |
| BackupStale | >90000s since last success | run `walg` base manually; inspect walg_logs volume |
| WalArchiveFailing | any increase in 15m | storage target writable? wal-g creds? disk full? |

## 5. What deliberately did NOT land here

- No postgres_exporter / Grafana / Alertmanager containers (lane scope: no
  new heavy deps; Prometheus itself is the single added image).
- No notification routing: rules fire in Prometheus only; wire Alertmanager
  (+ PagerDuty/SMTP receiver) before trusting pages.
- The legacy `/api/v1/orchestrator/metrics` route keeps serving the
  orchestrator-only Wave 8 collector; both endpoints coexist by design.
