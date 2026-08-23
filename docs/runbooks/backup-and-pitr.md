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

> **Update (Mission 10, node B4):** the tooling below now EXISTS as opt-in,
> unexercised scaffolding — see **"WAL-G operation"** (next section). The db
> container still runs with `archive_mode=off` until a future wave switches it
> to the wal-g-enabled image, so the RPO statement above still describes the
> live deployment.

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

## WAL-G operation (Mission 10, node B4 — NEW tooling, see honesty note)

**Honest status:** the scripts and compose profile below are **shipped but not
exercised end-to-end**: they were validated with `sh -n`/`bash -n`,
shellcheck, `docker compose config -q`, and each script's own `config`
(dry-run) mode. The build environment has **no S3 credentials**, so no base
backup has ever actually been pushed to or fetched from object storage by this
tooling. Treat everything below as reviewed-but-unproven until a credentialed
operator completes the first drill; see the validated/not-validated table at
the end of this section.

WAL-G gives continuous WAL archiving on top of periodic base backups: instead
of nightly logical dumps with RPO ≈ 24 h, you get physical recovery to any
timestamp (seconds/minutes of RPO). References followed:

- WAL-G PostgreSQL usage: <https://github.com/wal-g/wal-g/blob/master/docs/PostgreSQL.md>
  (rendered: <https://wal-g.readthedocs.io/PostgreSQL.html>)
- `archive_command` / `restore_command` contract:
  <https://www.postgresql.org/docs/current/runtime-config-wal.html>
- PITR concepts (`recovery.signal`, targets):
  <https://www.postgresql.org/docs/current/continuous-archiving.html>

### What shipped

| File | Purpose |
|---|---|
| `deploy/walg/backup-walg.sh` | Modes: `base` (base backup), `wal <seg>` (one WAL segment, i.e. what archive_command calls), `list`, `retention`, `config` (secrets-masked dry-run dump). Mode = first CLI arg, else `$WALG_MODE`, else `base`; `WALG_LOOP=1\|loop` re-runs the chosen mode every `WALG_INTERVAL_SECONDS` instead of single-shot |
| `deploy/walg/restore-walg.sh --target-time '<ts>'` | Full PITR into a scratch volume + printed manual verification/cutover checklist; `--fetch-only` stops after `backup-fetch` |
| `deploy/walg/Dockerfile.walg` | `postgres:18-alpine` + checksum-verified `wal-g-pg-24.04-{amd64,aarch64}` binary from upstream releases (pinned v3.0.9 at authoring time — re-check before building) |
| `walg` service in `deploy/docker-compose.yml` | Opt-in sidecar behind `profiles: ["walg"]`; one-shot or interval loop; never starts with plain `up -d` |

### Prerequisites

1. **Object storage + IAM.** One private bucket, e.g. `s3://harness-backups/pg`.
   Minimal IAM policy for the writer:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"],
       "Resource": ["arn:aws:s3:::harness-backups", "arn:aws:s3:::harness-backups/*"]
     }]
   }
   ```

   Enable bucket **versioning** (ransomware protection) and a lifecycle rule
   (e.g. expire noncurrent versions after 30 days); prefer SSE-S3/KMS
   encryption at rest. For local drills only, `WALG_FILE_PREFIX=/some/dir`
   works without any cloud account.
2. **An image that contains wal-g.** The stock `postgres:18-alpine` does NOT.
   Build once per host and pin the digest in your deployment notes:

   ```
   docker build -f deploy/walg/Dockerfile.walg -t harness-db-walg --build-arg WALG_VERSION=v3.0.9 .
   ```

3. **Env for the storage target** (compose reads them from your shell/env
   file): `WALG_S3_PREFIX`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   `AWS_DEFAULT_REGION` (or `AWS_ENDPOINT` for MinIO/R2-compatible stores).
   Never commit these; they enter the containers via `environment:` mapping.

### Turning WAL archiving on — REQUIRES A COMPOSE DB CHANGE (future wave)

The `db` service must run an image containing `wal-g` and set two GUCs. This
is deliberately NOT done in Mission 10 (the compose db service is owned by no
B4 lane and changing it would break the default dev stack for everyone).
When approved, the change is exactly:

```yaml
  db:
    image: harness-db-walg        # built from deploy/walg/Dockerfile.walg
    environment:
      # ...existing vars...
      WALG_S3_PREFIX: ${WALG_S3_PREFIX}
      AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID}
      AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY}
      AWS_DEFAULT_REGION: ${AWS_DEFAULT_REGION:-}
    command:
      - postgres
      - -c
      - archive_mode=on
      - -c
      - archive_command=wal-g wal-push %p
      - -c
      - archive_timeout=60s       # bounds RPO even when the db is quiet
```

(`%p` is the absolute path of the finished segment — precisely what
`backup-walg.sh wal` expects. Until this lands you can still point
`archive_command` at a wrapper script mounted from outside **only if** the db
image can execute something that reaches wal-g — which stock images cannot;
external config hosts are the interim alternative documented here.) Verify
afterwards: `docker exec harness-db-1 psql -U harness -c 'SELECT * FROM
pg_stat_archiver;'` — `failed_count` should stay at 0 and `last_archived_wal`
should advance within ~archive_timeout. Also schedule `./deploy/walg/
backup-walg.sh list && ./deploy/walg/backup-walg.sh retention` next to the
base backup cron.

### Base backup cadence (suggestion)

- Base backup: **daily** (`17 3 * * * ./deploy/walg/backup-walg.sh base`),
  keep ≥ 2 fulls before the oldest WAL you might need (`WALG_RETENTION_KEEP=7`
  matches the pg_dump retention philosophy).
- WAL archiving: continuous via `archive_command`; `archive_timeout=60s`
  means a quiet database still cuts a segment every minute → worst-case RPO ≈
  1 min + detection time. Tighten to 15 s if needed (more small objects in S3).
- After any schema migration lands: take one extra manual base backup.
- Retention/DR hygiene: versioned bucket + lifecycle rules (prerequisites §1);
  the wave8 offsite-rsync rule stays satisfied because the object store IS
  offsite now.

### Operating the sidecar (one-shot vs loop)

```sh
# Dry-run / config check (secrets masked; a storage target must be resolvable,
# e.g. WALG_FILE_PREFIX=/tmp/drill for a local rehearsal):
docker compose -f deploy/docker-compose.yml --profile walg run --rm walg

# One-shot base backup right now:
docker compose -f deploy/docker-compose.yml --profile walg run --rm \
  -e WALG_MODE=base walg

# Cron-ish loop instead (base backup every 24 h inside the container;
# WALG_LOOP accepts "1" or "loop"):
docker compose -f deploy/docker-compose.yml --profile walg up -d \
  -e WALG_MODE=base -e WALG_LOOP=1 -e WALG_INTERVAL_SECONDS=86400 walg
```

One-shot + host cron is recommended over `loop` (exact timing, catch-up after
downtime, no long-lived container). `loop` exists for hosts without cron
access. Inspect inventory any time: `... run --rm -e WALG_MODE=list walg`.

### PITR drill (do this BEFORE you need it, with WALG_FILE_PREFIX)

```sh
# 1. Local-storage rehearsal env (no cloud needed):
export WALG_FILE_PREFIX=/tmp/harness-walg-drill
export PGHOST=127.0.0.1 PGPORT=5499 PGUSER=harness PGPASSWORD=harness

# 2. Baseline + a data marker:
./deploy/walg/backup-walg.sh base
docker exec harness-db-1 psql -U harness -d harness \
  -c "CREATE TABLE pitr_marker(t timestamp default now()); INSERT INTO pitr_marker VALUES (now());"
date -u    # <-- choose TARGET between marker insert and the destructive step

# 3. Generate some WAL, then simulate disaster (drop the table):
docker exec harness-db-1 psql -U harness -d harness -c "SELECT pg_switch_wal();"
sleep 5
docker exec harness-db-1 psql -U harness -d harness -c "DROP TABLE pitr_marker;"

# 4. Recover into the scratch volume (NEVER touches live harness_pgdata):
unset PGHOST   # restore-walg.sh talks to storage, then uses docker networking
./deploy/walg/restore-walg.sh --target-time "$(date -u -v-2M '+%Y-%m-%d %H:%M:%S+00')"
#    (GNU date: date -u -d '2 minutes ago' ...)

# 5. Follow its printed MANUAL VERIFICATION STEPS: confirm the paused server
#    still sees pitr_marker, promote, cutover, verify stack health.
```

A drill passes when `SELECT count(*) FROM pitr_marker` = 1 inside the paused
recovery container and the post-cutover stack passes the standard health
checks. Record every drill (date/target-time/result) below the table at the
end of this section.

### Validated vs not validated (Mission 10 B4, 2026-08-22)

| Item | Status | Evidence |
|---|---|---|
| Shell syntax of both scripts | ✅ validated | `bash -n deploy/walg/backup-walg.sh deploy/walg/restore-walg.sh` clean |
| Style/static analysis | ✅ validated | shellcheck (where available) — see mission report for exact output |
| Compose file validity incl. profile | ✅ validated | `docker compose config -q` and `--profile walg config -q` both exit 0 |
| Config/dry-run path of backup script logic | ✅ validated | `config` mode runs locally without wal-g installed only for argument handling; full check requires the binary — operator rerun required. Post-review re-validation (2026-08-23): `WALG_MODE=config ./deploy/walg/backup-walg.sh` now honors the env knob and fails on the same wal-g-binary guard as plain invocation |
| Docker image build (Dockerfile.walg) | ❌ NOT validated | No image pulls from build env; asset names verified against upstream release manifest only |
| Base backup push to real storage | ❌ NOT validated | No S3 credentials in build env |
| WAL push/fetch round-trip | ❌ NOT validated | Same |
| End-to-end PITR restore + promote | ❌ NOT validated | Same — first credentialed operator MUST run the drill above and record results here |
| archive_mode flip on the live db service | ❌ NOT done (by design) | Future-wave compose change, section above |

**First credentialed operator checklist:** build image → run the drill with
`WALG_FILE_PREFIX` → repeat against real S3 → fill in evidence rows above →
only then update the RPO row at the top of this runbook.
