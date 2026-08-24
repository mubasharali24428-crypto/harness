# Runbook: Postgres Vacuum & Scheduled Maintenance

Scope: the `db` service of `deploy/docker-compose.yml` — container
`harness-db-1`, image `postgres:18-alpine`, data in named volume
**`harness_pgdata`**, database/user `harness`/`harness` (dev defaults — change
in any real deployment), host-side `127.0.0.1:5499`. Companion to
[backup-and-pitr.md](backup-and-pitr.md) — that runbook covers **copying the
data out**; this one covers **keeping the live copy healthy and compact**.

Status line (M15-W5-D2, 2026-08-24): the maintenance job is implemented,
wired as the opt-in `maintenance` compose profile, and was executed for real
against the live stack (host-side AND via the profile's one-shot container);
see §Local proof at the end.

## Why this exists (the bloat story)

Postgres never physically removes dead rows during normal operation —
`DELETE`s and `UPDATE`s leave "dead tuples" behind, and only `VACUUM` reclaims
that space for reuse. Autovacuum handles most of it, but three tables deserve
explicit attention:

| Table | Why it churns |
|---|---|
| `agent_events` | every EventBus publish inserts a row (Section 1 delivery layer); retention sweeps delete old rows in batches → constant insert/delete churn |
| `rate_buckets` | upserted on every rate-limited request (`INSERT .. ON CONFLICT DO UPDATE`) → update-heavy, dead tuples accumulate fast |
| `audit_log` | append-only audit trail; grows monotonically |

The first live snapshot of this lane already showed why: `rate_buckets` sat
at **dead_ratio = 0.9592** (47 dead vs 2 live rows) with autovacuum having
never fired on it — see §Local proof. Small dev numbers, same failure mode as
production bloat.

## The pieces

| Piece | File | What it does |
|---|---|---|
| Job script | `scripts/maintenance.sh` | per-table BEFORE/AFTER bloat snapshots from `pg_stat_user_tables`, `VACUUM (ANALYZE)` per table, optional `REINDEX TABLE CONCURRENTLY`, lock guard, honest exit codes 0/1/2/3, appends runs to the log volume |
| Compose sidecar | `deploy/docker-compose.yml` service `db-maintenance`, profile `maintenance` | stock `postgres:18-alpine` image (ships psql; the slim backend image deliberately does not), libpq env only, depends on db health, logs + state on named volumes `maintenance_logs` / `maintenance_state` |
| Runbook | this file | operator procedures |

## Commands (what each does)

### One-shot pass — `once`

```sh
docker compose -f deploy/docker-compose.yml --profile maintenance run --rm --no-deps db-maintenance once
```

Runs exactly one full pass (bloat snapshot → per-table `VACUUM (ANALYZE)` →
optional reindex → after-snapshot → SUMMARY) and exits. **Its exit code IS
the pass result** — cron-safe honesty, never exit 0 on failure.

⚠️ On `--no-deps` (observed live 2026-08-24): a plain `run --rm
db-maintenance once` reconciles dependencies and may RECREATE `db` when its
config hash drifted. That proved benign here — data lives on the `pgdata`
named volume and every backend stayed healthy throughout — but add
`--no-deps` once `db` is already healthy to keep the run strictly
sidecar-only. (Omit it on a first-ever bring-up so the db gets started.)

Host-cron pairing (exact timing, mail-on-failure):

```sh
# m  h dom mon dow  command  (weekly vacuum, Sunday 04:07 UTC — off-peak)
07 4 * * 0 cd /path/to/harness && docker compose -f deploy/docker-compose.yml --profile maintenance run --rm --no-deps db-maintenance once >> deploy/backup/maintenance.log 2>&1
```

macOS launchd equivalent: a `StartCalendarInterval` agent running the same
command.

### Loop mode — container-owned cadence

```sh
docker compose -f deploy/docker-compose.yml --profile maintenance up -d db-maintenance
```

Default `MAINTENANCE_MODE=loop`: one pass immediately at startup, then every
`MAINTENANCE_INTERVAL_SECONDS` (default `604800` = weekly). Scheduling
semantics (deliberate contrast to the M13 WALG_LOOP retirement):

- Downtime collapses missed cycles into ONE catch-up pass at next start —
  nothing is silently skipped.
- A restart within the same interval does NOT re-vacuum: the last-success
  stamp (volume `maintenance_state`) dedups until due again.
- A failed pass logs loudly and retries NEXT cycle; the container stays up so
  failures are visible in logs instead of hidden by restart loops.
- If another holder owns the lock, loop waits (60 s retry) instead of
  stacking a second vacuum against the same table.

Choose host cron + `once` for exact timing and cron mail; choose `up -d` loop
mode when the schedule should travel with the stack.

### Status (read-only)

```sh
docker compose -f deploy/docker-compose.yml --profile maintenance run --rm --no-deps db-maintenance status
```

Prints config, last-success stamp age, lock state, recent log lines, and the
live bloat snapshot — touches nothing.

### Manual one-liners via docker exec (no wrapper)

```sh
docker exec harness-db-1 psql -U harness -d harness -c 'VACUUM (ANALYZE) agent_events;'
docker exec harness-db-1 psql -U harness -d harness -c 'VACUUM (ANALYZE) rate_buckets;'
docker exec harness-db-1 psql -U harness -d harness -c 'VACUUM (ANALYZE) audit_log;'
```

Use these when you want zero wrapper machinery; use the wrapper when you want
before/after evidence, logging, locking, and honest exit codes.

## Manual vs scheduled — decision guide

| Situation | Use |
|---|---|
| Normal steady state | scheduled weekly loop OR cron'd `once` (default knobs are fine) |
| Right before taking a base backup | manual `once` (see §WAL-G interplay) — do not move the schedule for this |
| After a huge deletion / retention backlog sweep | manual `once`, consider `MAINTENANCE_REINDEX=1 once` if indexes ballooned too |
| `n_dead_tup` climbing past thresholds below while autovacuum lags | manual `once` immediately, then investigate autovacuum settings |
| Fresh dev DB mid-migration | nothing — absent tables WARN, only an entirely empty configured set fails |

## Bloat indicators to watch

All from `pg_stat_user_tables` (the wrapper prints exactly this before and
after every pass; `status` shows it live):

```sql
SELECT relname, n_live_tup, n_dead_tup,
       ROUND(n_dead_tup::numeric / GREATEST(n_live_tup + n_dead_tup, 1), 4) AS dead_ratio,
       n_mod_since_analyze, last_analyze, last_autoanalyze, last_vacuum, last_autovacuum
FROM pg_stat_user_tables
WHERE relname IN ('agent_events','rate_buckets','audit_log')
ORDER BY n_dead_tup DESC;
```

| Signal | Meaning | Act when |
|---|---|---|
| `n_dead_tup` | dead rows awaiting reuse | sustained growth across consecutive checks |
| `dead_ratio` | dead ÷ total | > 0.2 sustained, or > 0.5 anytime (rate_buckets hit 0.9592 pre-fix) |
| `n_mod_since_analyze` | changes since stats were refreshed | large ⇒ planner statistics stale ⇒ bad plans; VACUUM ANALYZE fixes |
| `last_autovacuum` = NULL long-term | autovacuum never fired on this table | expected for tiny tables; combined with rising dead counts it means tune or intervene |

Index-side: bloat also lives INSIDE indexes (REINDEX fixes; see
§Troubleshooting for the `_ccnew` leftover). Watch table-vs-index size ratio
via `pg_relation_size()` if disk is the concern.

## Exit codes (honest, cron-safe)

| Code | Meaning |
|---|---|
| 0 | success — all present tables maintained (absent-but-configured tables WARN only) |
| 1 | real failure — postgres unreachable, a maintenance statement failed, or NONE of the configured tables exist |
| 2 | usage/config error — unknown command, non-positive/garbage interval, bad REINDEX value |
| 3 | lock held by another LIVE run (`once` refuses to stack) |

## Environment reference

| Var | Default | Notes |
|---|---|---|
| `MAINTENANCE_MODE` | `loop` | `once \| loop \| status`; CLI arg wins over env |
| `MAINTENANCE_INTERVAL_SECONDS` | `604800` | loop cadence (weekly); must be a positive integer |
| `MAINTENANCE_TABLES` | `agent_events rate_buckets audit_log` | whitespace-separated; validated as identifiers before touching SQL |
| `MAINTENANCE_REINDEX` | `0` | `1/true/yes` adds `REINDEX TABLE CONCURRENTLY` per table |
| `MAINTENANCE_PGHOST` | `db` | compose service name inside the network |
| `PGUSER` / `PGPASSWORD` / `PGDATABASE` / `PGPORT` | `harness` / `${POSTGRES_PASSWORD:-harness}` / `harness` / `5432` | libpq env; secrets via `.env`/shell only |
| `MAINTENANCE_LOG_DIR` | `/var/log/harness-maintenance` (volume `maintenance_logs`) | appended runs survive `--rm`/restarts |
| `MAINTENANCE_LOCK_DIR` | `/var/lib/harness-maintenance/lock` (volume `maintenance_state`) | atomic-mkdir guard |
| `MAINTENANCE_STAMP_DIR` | `/var/lib/harness-maintenance/stamp` | last-success stamps power loop dedup/catch-up |
| `MAINTENANCE_LOCK_STALE_SECONDS` | `21600` | lock older than this is provably abandoned and may be broken |

## WAL-G interplay — vacuum BEFORE the base backup

Read with [backup-and-pitr.md](backup-and-pitr.md). Ordering matters:

1. **VACUUM first, then base backup.** A vacuum moves tuples around and
   emits full-page images into WAL; taking the base backup AFTER the pass
   means the restore chain starts compact and replay stays short. The nightly
   cadence in backup-and-pitr.md's §Scheduling already orders things this way
   naturally (vacuum window off-peak, base backup later) — keep that order
   when you change either schedule.
2. **VACUUM generates WAL.** Every pass lands in the archive via
   `archive_command='wal-g wal-push %p'` on the archiving instance — that is
   normal and bounded; don't panic at post-vacuum archive traffic spikes.
3. **PITR restores include the bloat of that era.** Vacuuming does not shrink
   files on disk by itself (space is reused, not returned to the OS);
   restoring an old backup restores old bloat. Regular passes keep BOTH live
   data and future restores compact.
4. **VACUUM cannot run during recovery/pause.** During PITR drills the paused
   server is read-only — maintenance targets the live primary only.
5. **One-shot backups and vacuums share the "host cron owns scheduling" law**
   (the M13 WALG_LOOP retirement). Both wrappers log into their own volumes
   (`walg_logs` / `maintenance_logs`) and both fail with non-zero exit codes
   so cron mail catches them.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| exit 3 "another maintenance run holds the lock" | previous run still going (or container restarted mid-pass without cleanup) | wait; check owner pid/epoch in `$MAINTENANCE_LOCK_DIR/owner`; it self-heals after `MAINTENANCE_LOCK_STALE_SECONDS` |
| exit 1 "cannot reach postgres" | db down / wrong PGHOST | `docker ps` the db, check `pg_isready -h 127.0.0.1 -p 5499 -U harness`; then rerun |
| exit 1 "NONE of the configured tables exist" | schema never migrated (migrate service didn't run) | bring up the stack normally; verify `docker exec harness-db-1 psql -U harness -d harness -c '\dt'` |
| `REINDEX TABLE CONCURRENTLY` failed midway | cancelled reindex leaves an INVALID `_ccnew` twin index behind | find it: `SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;` then `DROP INDEX <name>;` and rerun with `MAINTENANCE_REINDEX=1` |
| `VACUUM ... is already running` warnings | concurrent autovacuum on the same table | harmless — skip and let the next cycle catch it; do NOT disable autovacuum |
| `WARN: table 'x' not found in database` | typo or pre-migration DB | fix the knob or migrate; single-table absence is deliberately non-fatal |
| `run --rm db-maintenance once` recreated `db` | dependency reconciliation (config hash drift) | benign — data on `pgdata`; use `--no-deps` once healthy (see §Commands) |

## Local proof (M15-W5-D2, 2026-08-24)

Executed for real; outputs abridged where repetitive.

1. Static gates:

```
$ sh -n scripts/maintenance.sh                      # exit 0
$ docker compose config -q                          # exit 0 (default profile)
$ docker compose --profile maintenance config -q    # exit 0
# plus proxy/walg/events-retention/legacy-backend profiles all resolve OK
```

2. First live snapshot (host psql against `127.0.0.1:5499`) — the smoking gun
   this lane exists for: `rate_buckets` at dead_ratio **0.9592**, never
   autovacuumed.

3. Wrapper pass (exit 0): BEFORE snapshot → `VACUUM (ANALYZE)` ×3 → AFTER
   snapshot showed every `last_vacuumed`/`last_analyzed` stamped today and
   `rate_buckets` dead_ratio **0.9592 → 0.0000**:
   `[db-maintenance …] SUMMARY: PASS — 3 table(s) maintained in 0s`.

4. Guard rails (all observed): held-lock `once` → **exit 3**; unreachable
   port → **exit 1** with diagnostics; unknown command / garbage knob →
   **exit 2**; bogus extra table → WARN but **exit 0**; fully-absent table
   set → **exit 1**; `MAINTENANCE_REINDEX=1` ran `REINDEX TABLE CONCURRENTLY`
   ×3, exit 0; loop mode with fresh stamp logged "sleeping until due" (dedup).

5. End-to-end through compose itself:

```sh
$ docker compose -f deploy/docker-compose.yml --profile maintenance \
    run --rm db-maintenance once
… [db-maintenance] SUMMARY: PASS — 3 table(s) maintained in 0s
EXIT=0
```

(Note: that first run recreated `harness-db-1` via dependency reconciliation;
stack verified fully healthy afterwards — identity `/health` 200, all
services up — and the `--no-deps` variant documented above avoids it.)

6. Post-run status shows stamp + persistent log lines in the volumes.

Not exercised here (honest gaps): multi-week loop uptime (dedup logic proven
only against a fresh stamp); REINDEX failure-path `_ccnew` cleanup performed
by hand, not scripted.
