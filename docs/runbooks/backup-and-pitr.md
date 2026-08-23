# Runbook: Postgres Backup & Point-in-Time Recovery (PITR)

Scope: the `db` service of `deploy/docker-compose.yml` — container
`harness-db-1`, image `postgres:18-alpine`, data in named volume
**`harness_pgdata`**, database/user/password `harness`/`harness`/`harness`
(dev defaults — change in any real deployment), exposed host-side on
`127.0.0.1:5499`.

Tooling (this directory's sibling scripts, all POSIX `sh`, no deps beyond
`docker` + coreutils):

| Script | What it does |
|---|---|
| `deploy/backup/backup.sh` | `pg_dump` (custom format, gzip) → `deploy/backup/backups/<UTC-timestamp>.dump`, integrity-checks it via `pg_restore --list`, prunes to newest `BACKUP_KEEP` (default 7) |
| `deploy/backup/restore.sh <dump> [expectations]` | Drops + recreates a target DB (confirmation prompt unless `RESTORE_FORCE=1`; override `RESTORE_TARGET_DB` to rehearse), restores with `pg_restore --exit-on-error`, row-count sanity check of `users`, `teams`, `projects`, `agent_events` against an optional expectations file |
| `deploy/backup/verify_restore.sh` | One-shot drill: scratch DB inside the running container ← newest snapshot, compares key-table counts vs live db, prints PASS/FAIL per table, always drops the scratch DB |

Snapshots are logical dumps (schema **and** data) and are **not encrypted at
rest**. They contain app-layer-encrypted secrets (`api_keys.key_encrypted`,
`github_connections.access_token_encrypted`) which are only decryptable with
the **same `HARNESS_MASTER_KEY`** as the environment that produced them —
restoring into a new host with a different master key yields undecryptable
secrets.

## RPO / RTO statement

| Metric | Chosen value | Basis | How to tighten |
|---|---|---|---|
| **RPO** | **24 h** | Nightly cron keeps one dump per day; worst case loses up to 24 h of writes | Cron hourly → RPO ≤ 1 h; every 15 min → RPO ≤ 15 min. Below that, logical dumps stop making sense — go to WAL archiving (PITR section below) for seconds-level RPO |
| **RTO** | **30 min** target | Measured on the current dataset: full backup ≈ 1.2 s, restore + verify ≈ 1–2 s (2026-08-23 live proof). Even at 100× data volume the mechanics stay in minutes; the budget is dominated by human decision-making, stopping services, and restarting the stack | Automate the checklist below; pre-stage offsite copies near the restore host |

These values are appropriate for this single-host dev/small-team deployment.
They are NOT appropriate for a multi-team production instance without WAL
archiving and offsite copies.

## Scheduling (nightly backup)

Cron (Linux; also works on macOS):

```
# m  h dom mon dow  command
17 2 * * * cd /path/to/harness && ./deploy/backup/backup.sh >> deploy/backup/backup.log 2>&1
```

(`17 2` = 02:17 local time; use `17 2 * * *` with `CRON_TZ=UTC` or convert if
you care about the exact UTC instant. The script logs UTC timestamps either
way.)

macOS launchd equivalent (cron still works but is semi-deprecated there):
a `RunAtLoad=false`, `StartCalendarInterval={Hour=2, Minute=17}` agent running
the same command line.

**Offsite copy is mandatory for real deployments** — as shipped, snapshots sit
on the same filesystem as the stack, so host/disk loss takes both down
together. Add a second crontab line after the backup:

```
20 2 * * * rsync -a --delete /path/to/harness/deploy/backup/backups/ backup-host:/srv/harness-backups/
# or: rclone copy /path/to/harness/deploy/backup/backups remote:harness-backups
```

## Restore procedure (step by step)

Assumes the snapshot came from `backup.sh` and you are on the compose host.

1. **Freeze the writers** (keep the db itself up for now):
   ```
   docker compose -f deploy/docker-compose.yml stop identity github models orchestrator sandbox meeting review frontend
   ```
2. **Protect the broken state**: run `./deploy/backup/backup.sh` once more.
   If the restore goes wrong you now have a snapshot of the damaged state too,
   and its timestamp marks the incident boundary.
3. **Generate expectations from the healthy source**, if you have any way to
   know what the data should look like (see the header comment of
   `restore.sh` for the exact loop). Skip if you don't — the restore still
   runs; only the automated comparison is lost.
4. **Restore** (this DROPS the live `harness` database):
   - Interactive, with confirmation prompt:
     `./deploy/backup/restore.sh deploy/backup/backups/<timestamp>.dump expectations.txt`
     Type `YES` when prompted.
   - Non-interactive: prefix with `RESTORE_FORCE=1`.
5. **Read the script output**: every restore logs restored row counts and,
   with an expectations file, an explicit pass/fail per table. A non-zero exit
   means stop and investigate — do not "just restart" over a failed restore.
6. **Restart the stack**: `docker compose -f deploy/docker-compose.yml up -d`.
7. **Verify**: `curl localhost:8001/health` → `{"ok":true}` on each backend
   port (8001–8008), then log into the UI and confirm projects/teams render.
8. **Post-incident**: copy the incident snapshot + new post-restore snapshot
   offsite, note the event gap in the audit trail (event sourcing means
   writes during the lost window are simply absent — say so honestly in the
   incident report).

**Rehearse regularly**: run `./deploy/backup/verify_restore.sh` weekly and
after any schema migration lands. It never touches production tables — it
exercises restore into a throwaway `harness_verify_restore` DB and cleans up
after itself. A backup that has never been restored is a hope, not a backup.

## PITR — documented NEXT STEP, not implemented

**Honest status:** point-in-time recovery does **not exist** in this
deployment today. Evidence (checked live 2026-08-23): the db container runs
with `wal_level=replica, archive_mode=off` and no archive/restore commands are
configured anywhere. Snapshots are discrete nightly dumps; between two dumps
there is no recoverable intermediate state.

What PITR would add: continuous archiving of write-ahead-log segments on top
of periodic *base backups* lets you replay to **any** timestamp — e.g.
"restore to 30 seconds before the bad migration ran" — turning RPO from hours
to seconds/minutes.

Recommended implementation path (**not built yet** — this is the next work
item, estimated ~half a day plus testing):

1. Pick a tool: **WAL-G** (simpler, good S3/local support) or **pgBackRest**
   (more features, stricter config). Both ship for postgres:18 containers.
2. Add to the db service (compose env / mount):
   - WAL-G: `WALG_S3_PREFIX` (or `WALG_FILE_PREFIX` for local/NFS),
     `PGWAL_DIR`-appropriate wiring, and set `archive_mode=on`,
     `archive_command='wal-g wal-push %p'`, adequate `archive_timeout`
     (e.g. 60s bounds RPO even on quiet databases).
3. Schedule base backups (cron inside a sidecar or on the host:
   `wal-g backup-push /var/lib/postgresql`) — e.g. daily, keeping ≥ 2.
4. Define retention & encryption at the object store (lifecycle rules,
   SSE, bucket versioning against ransomware).
5. Document + test `wal-g backup-fetch` restore into a fresh volume, and add
   a `verify_pitr.sh` analog of `verify_restore.sh`.
6. Only then update this runbook's RPO row.

Until that lands: the nightly `backup.sh` cron + offsite rsync IS the disaster
recovery story, with RPO 24 h as stated above.

## Disaster scenario checklists

### A. Disk loss / `harness_pgdata` volume destroyed / host dead

- Symptoms: container crash-looping or gone; `docker volume inspect
  harness_pgdata` fails or shows zero usage; backends DNS-crash-looping.
- Accept up to RPO (24 h) of data loss before starting; announce the window.
- Steps:
  1. Reprovision disk/host; install Docker; restore the repo checkout.
  2. Recreate an empty volume: `docker volume create harness_pgdata`.
  3. Bring up only the db: `docker compose -f deploy/docker-compose.yml up -d db`
     (let `migrate` do nothing yet — the restore will bring the schema).
  4. Copy the newest offsite snapshot back if the local `backups/` died too.
  5. Restore: `./deploy/backup/restore.sh <snapshot>.dump` (+ `YES`).
  6. Full stack up, verify per the restore procedure steps 6–8.
- If the snapshots were on the same dead disk and no offsite copy exists:
  total data loss. This is exactly why the offsite copy step is mandatory.

### B. Bad migration / schema corruption

- Symptoms: backends 500-ing on specific routes, `schema_migrations` showing a
  migration whose code change is broken, tests failing against real PG only.
- Do **not** drop anything immediately — the broken state is evidence.
- Steps:
  1. Stop the app containers (step 1 of the restore procedure) but leave db up.
  2. Emergency-dump the broken state (`backup.sh`) for post-mortem.
  3. Identify the last known-good snapshot taken **before** the migration ran
     (`ls deploy/backup/backups/` and cross-check with git log / STATUS.md).
  4. Restore it: `./deploy/backup/restore.sh <pre-migration>.dump` (+ `YES`).
  5. Re-apply migrations deliberately: fix or revert the offending numbered
     migration first (per AGENTS.md: new numbered migration, never edit an
     applied one), re-run `migrate`, then `verify_restore.sh`.
- Prevention: every schema change lands with tests against real PG
  (`HARNESS_TEST_PG=1 ... integration/test_checkpoint1.py`).

### C. Ransomware-style corruption (encrypted/garbage rows, malicious wipe)

- Symptoms: rows suddenly unreadable/gibberish, tables truncated, ransom
  notes, unexpected auth activity in `audit_log`.
- Steps:
  1. **Isolate**: stop the whole stack (`docker compose down`), disconnect the
     host from the network if compromise is suspected beyond the db. Every
     further command happens read-only until you understand entry point.
  2. **Preserve evidence**: copy `harness_pgdata` (`docker run --rm -v
     harness_pgdata:/data -v "$PWD":/out alpine tar czf /out/pgdata-evidence.tgz
     /data`) and keep the newest pre-corruption snapshots untouched/read-only.
  3. **Find the corruption boundary** (timestamp T): audit_log / agent_events
     seq gaps, file mtimes, application logs. This decides which snapshot to
     trust — must be strictly older than T. *(This is precisely where PITR
     would shine — restoring to T minus epsilon — and precisely what we cannot
     do until WAL archiving ships; today the granularity is the nightly
     snapshot.)*
  4. **Rebuild clean**: new volume, restore chosen snapshot, per procedure A.
  5. **Rotate everything that could have leaked**: `POSTGRES_PASSWORD`,
     `HARNESS_MASTER_KEY` (note: rotating it invalidates previously encrypted
     secrets — re-mint API keys / reconnect GitHub afterwards), bearer-token
     ecosystem, host SSH keys.
  6. **Root-cause** via audit_log + access logs; close the hole; only then
     reconnect the network and unfreeze writers.
- Prevention: offsite *versioned* backups (object-store versioning or
  `rsnapshot`-style history) so a wiped live site can't destroy the backups;
  restrict who can `docker exec` on the host.

## Environment variable reference (all three scripts)

| Var | Default | Notes |
|---|---|---|
| `CONTAINER` | `harness-db-1` | Used when `BACKUP_USE_COMPOSE != 1` |
| `BACKUP_USE_COMPOSE` | `0` | `1` → drive `docker compose exec -T db` via `COMPOSE_FILE` instead of `docker exec` |
| `COMPOSE_FILE` | `<script>/../docker-compose.yml` | Compose mode only |
| `BACKUP_DIR` | `<script>/backups` | Where `.dump` files land |
| `BACKUP_KEEP` | `7` | Newest-N retention; `0` disables pruning |
| `PGUSER` / `PGDATABASE` | `harness` / `harness` | Backup source identity |
| `RESTORE_TARGET_DB` | `harness` | **This DB gets dropped** by restore.sh; override to rehearse |
| `RESTORE_FORCE` | unset | `1` skips the drop-recreate prompt |
| `EXPECTATION_FILE` | — | Expectations file for restore.sh (arg 2 wins) |
| `VERIFY_SCRATCH_DB` | `harness_verify_restore` | Throwaway DB used by verify_restore.sh |
| `VERIFY_TABLES` | `users teams projects agent_events` | Tables compared by verify_restore.sh |
