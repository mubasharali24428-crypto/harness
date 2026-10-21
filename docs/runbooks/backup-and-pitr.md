# Runbook: Postgres Backup & Point-in-Time Recovery (PITR)

Scope: the `db` service of `deploy/docker-compose.yml` — container
`harness-db-1`, image `harness-db-walg:${WALG_IMAGE_TAG:-v3.0.9}`
(postgres:18 + wal-g; M17-B2 promoted the live primary onto it from
`postgres:18-alpine`), data in named volume
**`harness_pgdata`**, database/user/password `harness`/`harness`/`harness`
(dev defaults — change in any real deployment), exposed host-side on
`127.0.0.1:5499`.

Status line (M17-B2 / OPS-01, 2026-08-25): **the LIVE primary archives WAL
itself** (`archive_mode=on`, `archive_command='wal-g wal-push %p'`) and the
nightly base-backup wrapper targets it by default (`WALG_PGHOST=db`). The
M13-W3-H2 sidecar-only design below is history: `walg-db` survives only as a
throwaway drill instance. What is still UNEXECUTED is anything needing real
S3 credentials or touching the live volume.

Tooling map:

| Layer | Files | What it does |
|---|---|---|
| Logical snapshots | `deploy/backup/backup.sh`, `restore.sh`, `verify_restore.sh` | `pg_dump` (custom format, gzip) → `deploy/backup/backups/<UTC>.dump`; restore + row-count verify into scratch DB |
| Physical PITR | `deploy/walg/Dockerfile.walg`, `deploy/walg/pg_hba.conf`, `deploy/runners/backup.sh`, `deploy/walg/restore-walg.sh`, compose services `walg-db` + `walg` (`--profile walg`) | Continuous WAL archiving (`archive_command='wal-g wal-push %p'`) + streaming base backups + timed restore drills |

Snapshots (both kinds) are **not encrypted at rest**. They contain
app-layer-encrypted secrets (`api_keys.key_encrypted`,
`github_connections.access_token_encrypted`) which are only decryptable with
the **same `HARNESS_MASTER_KEY`** as the environment that produced them —
restoring into a new host with a different master key yields undecryptable
secrets.

## RPO / RTO statement (updated 2026-08-24)

Two independent recovery paths exist. Pick per incident:

| Path | RPO | Measured RTO (2026-08-24 drill, dev-sized data) | Notes |
|---|---|---|---|
| **WAL-G PITR** (live `db`; drills via `--profile walg`) | ≤ `WALG_ARCHIVE_TIMEOUT` (60 s default) + detection time; **demonstrated: recovered to 4 s before a destructive DELETE** | fetch 1 s + replay-to-pause ≈ 2 s wall clock on the drill dataset; budget minutes for human verification + cutover | Physical, whole-cluster; recovers to ANY timestamp covered by base + WAL |
| Nightly `pg_dump` (`deploy/backup/`) | 24 h (cron cadence) | backup ≈ 1.2 s, restore+verify ≈ 1–2 s (2026-08-23 live proof) | Logical, per-database; survives even if physical tooling breaks |

These values are appropriate for this single-host dev/small-team deployment.
For multi-team production: real object storage (S3/R2/MinIO) with versioning +
lifecycle rules, tighter `archive_timeout`, offsite copies of BOTH layers, and
a scheduled drill cadence (monthly minimum).

## Scheduling

```
# m  h dom mon dow  command
17 2 * * * cd /path/to/harness && ./deploy/backup/backup.sh >> deploy/backup/backup.log 2>&1
03 3 * * * cd /path/to/harness && docker compose -f deploy/docker-compose.yml --profile walg run --rm walg >> deploy/backup/walg.log 2>&1
```

(First line: nightly logical dump. Second: daily WAL-G base backup via the
one-shot wrapper — since M17-B2 its `PGHOST` defaults to the LIVE `db`, so
the scheduled backup protects the real cluster with zero overrides; point a
one-off rehearsal back at the drill sidecar with `-e WALG_PGHOST=walg-db`.
Host cron is deliberately preferred over any in-container sleep loop: exact
timing, catch-up semantics, failures surface in cron mail.)
macOS launchd equivalent: `StartCalendarInterval` agents running the same
commands.

**Ordering vs vacuum (M15-W5-D2):** schedule base backups AFTER the
maintenance window — VACUUM first, then the base backup — so restores start
from a compact state with a short replay chain. The companion
[runbook](maintenance.md) §WAL-G interplay owns this contract; its weekly
default (`MAINTENANCE_INTERVAL_SECONDS=604800`, Sunday 04:07 UTC example)
sits safely before the 03:03-style backup slots only if you keep the days
straight — when in doubt, run `db-maintenance once` manually right before
any manual base backup.

**Offsite copy is mandatory for real deployments** — as shipped, everything
sits on the same filesystem as the stack:

```
20 2 * * * rsync -a --delete /path/to/harness/deploy/backup/backups/ backup-host:/srv/harness-backups/
# or: rclone copy /path/to/harness/deploy/backup/backups remote:harness-backups
```

With real S3 the object store IS the offsite copy for the PITR layer; enable
bucket **versioning** (ransomware protection) and a lifecycle rule (e.g.
expire noncurrent versions after 30 days), prefer SSE encryption at rest.

## Restore procedure — logical snapshots (unchanged)

Assumes the snapshot came from `backup.sh`:

1. Freeze writers: `docker compose -f deploy/docker-compose.yml stop identity github models orchestrator sandbox meeting review frontend`
2. Protect the broken state: one more `./deploy/backup/backup.sh` run.
3. Generate expectations from the healthy source if possible.
4. Restore (DROPS the live `harness` DB):
   `./deploy/backup/restore.sh deploy/backup/backups/<timestamp>.dump expectations.txt` (+ type `YES`; or prefix `RESTORE_FORCE=1`).
5. Read the output: restored counts + pass/fail per table. Non-zero exit ⇒
   stop and investigate.
6. Restart the stack: `docker compose -f deploy/docker-compose.yml up -d`.
7. Verify: `curl localhost:8001/health` … 8008 (no service on 8007), UI login.
8. Post-incident: copy snapshots offsite; note the event gap honestly.

Rehearse weekly with `./deploy/backup/verify_restore.sh` (scratch DB, always
self-cleaning). A backup that has never been restored is a hope, not a backup.

## PITR architecture (H-018, M13-W3-H2 — replaces the Mission-10 sketch)

History: Mission 10 B4 shipped unexercised scaffolding (sidecar script +
Dockerfile, `archive_mode` flip documented as a future change). Wave 3 lane
H2 made it real: archiving instance running, archive_command proven pushing,
base backups taken, retention pruning, and PITR restores executed twice.

### What runs where (topology updated M17-B2 / OPS-01, 2026-08-25)

**M17-B2 topology change:** the LIVE `db` service now archives WAL itself
(same `harness-db-walg` image + the identical four `-c` flags below), and the
nightly base-backup wrapper's `WALG_PGHOST` defaults to **`db`** — so the
documented daily backup protects the cluster that actually holds data. The
sidecar remains as a throwaway DRILL/REHEARSAL instance (`WALG_PGHOST=walg-db`)
with its own volume, so restore drills can never collide with live data.

| Piece | Compose | Detail |
|---|---|---|
| Live primary | service `db`, default profile | image `harness-db-walg:${WALG_IMAGE_TAG:-v3.0.9}`; exec-form command sets `archive_mode=on`, `archive_command=wal-g wal-push %p`, `archive_timeout=${WALG_ARCHIVE_TIMEOUT:-60s}`, `hba_file=/etc/harness/pg_hba.conf` (+ `max_connections=120` and autovacuum knobs, see `.env.example`). Data stays in the same **`pgdata`** volume. With no storage prefix set, every wal-push fails loudly in `pg_stat_archiver.failed_count` — set `WALG_S3_PREFIX` or `WALG_FILE_PREFIX`. |
| Archiving DB | service `walg-db`, profile `walg` | Same image/flags as `db`, but on its OWN `pgdata_walg` volume — never touches live data. Now a drill/rehearsal target only. No host port (reach via `docker exec`). |
| Backup wrapper | service `walg`, profile `walg` | same image, entrypoint `sh /opt/harness/deploy/runners/backup.sh`; modes `base` (streaming BASE_BACKUP over TCP), `wal <seg>` (what archive_command calls), `list`, `retention`, `config` (secrets-masked dump). One-shot; run logs append to the `walg_logs` volume at `/var/log/harness-walg/backup.log` so they survive `--rm`. |
| Restore driver | `deploy/walg/restore-walg.sh` (host) | inventory → fresh scratch volume → `wal-g backup-fetch LATEST` → wires `restore_command`/`recovery_target_time`/`recovery.signal` → starts scratch server (pauses at target) → prints human promote/cutover checklist. |

Why a separate archiving instance instead of flipping `db` itself: `db`
serves every backend in the default dev stack; changing ITS image/command is
outside this lane's ownership and would couple everyone's boot to the wal-g
binary. Operators who later promote archiving onto the primary: swap `db`'s
image to `harness-db-walg:*` and add the identical four `-c` flags shown
above to its command — the WAL-G/AWS env knobs are already wired in `db`'s
environment block (inert until then). *(M17-B2 did exactly this — the live
`db` now archives; this paragraph remains as the history of the original
sidecar-only design.)*

Verified live inside `harness-walg-db-1` (2026-08-24):
`archive_mode=on · archive_command=wal-g wal-push %p · archive_timeout=1min`
and after activity: `pg_stat_archiver → archived_count=12, failed_count=0`.

### The two gotchas this design exists around (both hit for real)

1. **Stock images can't do either half.** `postgres:18-alpine` has no wal-g
   binary AND no `host replication` pg_hba rule (streaming base backups use
   the replication protocol). Hence the purpose-built image + the explicit
   `pg_hba.conf` (mounted read-only, selected via `-c hba_file=`).
2. **glibc vs musl.** Upstream wal-g release binaries are glibc-linked
   (`interpreter /lib/ld-linux-aarch64.so.1`, verified with `file`) — they
   cannot execute on Alpine. That is why `Dockerfile.walg` moved from
   `postgres:18-alpine` (M10) to Debian `postgres:18` with a multi-stage
   checksum-verifying fetch stage. Consequence: uid 999 (debian postgres),
   not 70 (alpine).

Build/tag the image once per host (compose does it automatically on first
`--profile walg up`):

```
docker build -f deploy/walg/Dockerfile.walg -t harness-db-walg:v3.0.9 .
docker run --rm harness-db-walg:v3.0.9 wal-g --version
# → wal-g version v3.0.9  3e49318  2026.08.20_14:32:45  PostgreSQL
```

Binary provenance: asset names/checksums verified against the upstream
release manifest (2026-08-23, v3.0.9):
`wal-g-pg-24.04-{amd64,aarch64}` + `.sha256`. NOTE the upstream arch token is
`aarch64` (NOT Docker's `arm64`) — the Dockerfile's fetch stage translates.

### Storage targets

- Drills/no-cloud: `WALG_FILE_PREFIX=/some/dir`. Compose bind-mounts the SAME
  host dir into `walg-db` and `walg` at the identical in-container path, so a
  freshly-created restore container sees the archive (container `/tmp` would
  otherwise be per-container and ephemeral).
- Real deployments: `WALG_S3_PREFIX=s3://bucket/path` (+ AWS keys/region, or
  `AWS_ENDPOINT` for MinIO/R2/SeaweedFS; self-hosted S3 also needs
  `AWS_S3_FORCE_PATH_STYLE=true`). Secrets enter ONLY via `.env`/shell env —
  never literals in compose (hard rule). Minimal writer IAM policy:
  PutObject/GetObject/DeleteObject/ListBucket/GetBucketLocation on bucket +
  `bucket/*`.

### Operating the profile

```sh
export WALG_FILE_PREFIX=/tmp/harness-walg-drill        # or your S3 env
docker compose -f deploy/docker-compose.yml --profile walg up -d walg-db
docker compose -f deploy/docker-compose.yml --profile walg run --rm walg                    # base backup
docker compose -f deploy/docker-compose.yml --profile walg run --rm -e BACKUP_MODE=list walg # inventory
docker compose -f deploy/docker-compose.yml --profile walg run --rm -e BACKUP_MODE=config walg
```

Health signals after enabling: `pg_stat_archiver.failed_count == 0`;
`last_archived_wal` advances within ~`archive_timeout` even on an idle db;
wrapper post-check prints an archiver-age line and fails loudly when storage
ends up empty (`post-check FAILED: … treat the backup as NOT taken`).

## PITR RESTORE DRILL — full procedure with timed RTO/RPO evidence

Run monthly and after every wal-g/image bump. Everything below is safe for
the live stack: recovery happens in a SEPARATE container on the scratch
volume `harness_pgdata_pitr`; the destructive cutover at the end is a
separate, explicitly-manual decision. Evidence slots are marked ⏱ — record
every number in the drill log table.

### Preconditions

- Stack healthy (`curl localhost:8001/health` → ok) OR at least `walg-db` up.
- Storage target resolvable (`WALG_FILE_PREFIX` exported or S3 env set).
- Scratch volume absent: `docker volume rm -f harness_pgdata_pitr` (idempotent).
- Image present: `docker image inspect harness-db-walg:${WALG_IMAGE_TAG:-v3.0.9}`.

### Steps

```sh
cd <repo>
export WALG_FILE_PREFIX=/tmp/harness-walg-drill
docker compose -f deploy/docker-compose.yml --profile walg up -d walg-db

# [1] MARKER — business-level proof row
docker exec harness-walg-db-1 psql -U harness -d harness -c \
  "CREATE TABLE IF NOT EXISTS pitr_marker(id int, t timestamptz default now());"
docker exec harness-walg-db-1 psql -U harness -d harness -c \
  "INSERT INTO pitr_marker VALUES(2) RETURNING id, t;"          # ⏱ RPO anchor T0
sleep 1; date -u +%Y-%m-%d\ %H:%M:%S+00 > /tmp/pitr_target.txt  # TARGET strictly between INSERT and DISASTER
cat /tmp/pitr_target.txt

# [2] FORCE the segment to storage immediately (don't wait for archive_timeout)
docker exec harness-walg-db-1 psql -U harness -c "SELECT pg_switch_wal();"; sleep 4
docker exec harness-walg-db-1 psql -U harness -d harness -tc \
  "select archived_count, failed_count from pg_stat_archiver;"   # PASS: n,0

# [3] BASE BACKUP via the wrapper                                    ⏱ RTO slot 1
BACKUP_MODE=base docker compose -f deploy/docker-compose.yml --profile walg run --rm walg
#    PASS: "Wrote backup with name base_…" + "base backup complete" + exit 0

# [4] DISASTER — simulate the bad transaction
docker exec harness-walg-db-1 psql -U harness -d harness -c \
  "DELETE FROM pitr_marker WHERE id=2 RETURNING id;"             # the row you must resurrect

# [5] RECOVER (scripted; pauses AT the target)                       ⏱ RTO slots 2–3
./deploy/walg/restore-walg.sh --target-time "$(cat /tmp/pitr_target.txt)"
#    PASS: exit 0, "recovery stopping before commit of transaction …",
#          "UP and PAUSED at <target>", checklist printed

# [6] VERIFY ON THE PAUSED SERVER — read-only, zero risk
docker exec harness-pitr psql -U harness -d harness -c \
  "select pg_is_in_recovery(), pg_last_wal_replay_lsn();"        # PASS: true, non-empty
docker exec harness-pitr psql -U harness -d harness -c \
  "select id, t from pitr_marker order by id;"                   # PASS: BOTH rows (1 AND 2)

# [7] PROMOTE (opens writes; new timeline)
docker exec harness-pitr psql -U harness -c "SELECT pg_wal_replay_resume();"
sleep 3
docker exec harness-pitr psql -U harness -d harness -tc "select pg_is_in_recovery();"  # PASS: f

# [8] TEARDOWN the drill (do NOT cutover unless there is a real incident)
docker rm -f harness-pitr && docker volume rm harness_pgdata_pitr
```

Pass/fail checklist (ALL boxes or the drill FAILED):

- [ ] `[2]` archiver shows `failed_count = 0` and `archived_count ≥ 1`
- [ ] `[3]` wrapper exit code 0; inventory lists the new `base_*`
- [ ] `[5]` restore script exit 0; logs contain
      `starting point-in-time recovery to <TARGET>` AND
      `recovery stopping before commit of transaction …`
- [ ] `[6]` `pg_is_in_recovery()` = true while paused; marker row present
      (RPO proven: the post-target DELETE is absent)
- [ ] `[7]` promotion succeeds; `pg_is_in_recovery()` flips false
- [ ] ⏱ numbers recorded below (fetch s, replay-to-pause s, total s)

Cutover (ONLY in a real incident, after step [7]-style verification): follow
the printed MANUAL checklist in `restore-walg.sh` output — stop writers,
tar-preserve the broken volume FIRST, swap volumes, `up -d`, health checks,
immediate fresh base backup.

### Drill evidence log (append every run)

| Date (UTC) | Driver | Target | Fetch ⏱ | Replay→pause ⏱ | Result | Notes |
|---|---|---|---|---|---|---|
| 2026-08-24 04:00 | manual (pre-script-validation) | `04:00:22+00` | ~1 s | ~2 s | **PASS** | markers {1,2} both present while paused; promoted → timeline 2, writable |
| 2026-08-24 04:11 | `restore-walg.sh` run #1 | `04:00:22+00` | 1 s | 1 s | PASS | exit 0; stopped before txn 768 (the DELETE); cosmetic double-quoting found in emitted config, fixed after |
| 2026-08-24 04:14 | `restore-walg.sh` run #2 (final form) | `04:00:22+00` | 1 s | ~2 s | **PASS** | canonical config emission; paused-server row check + promotion repeated |
| 2026-08-24 05:09 | W3-VT2 verifier (independent re-execution) | `05:08:56+00` | ~1 s | ~2 s | **PASS** | own marker id=3 inserted→deleted→resurrected while paused; promoted (`pg_is_in_recovery()=f`) + post-promotion INSERT ok; base wall time 1 s (wrapper slot); bogus-PGHOST & missing-segment ⇒ exit 1; alpine ADD variant rebuilt ⇒ exit 127 confirmed |
| 2026-10-21 07:49 | `restore-walg.sh` against the **live `db` archive** (`/tmp/harness-walg-live`, base 07:32) | `07:48:17+00` | 10 s | ~3 s (13 s script total) | **PASS** (2nd run) | run #1 aborted: live `db` runs `-c max_connections=120`, scratch server defaulted to 100 → "insufficient parameter settings"; script now passes 120. Stopped before the DELETE's commit (07:48:19.656); marker id=1 back, id=99 (after target) absent; users/teams/projects/events/backup_runs counts equal live; promoted in 1 s → timeline 2; scratch `archive_mode=off`, no timeline-2 WAL in the live prefix |
| 2026-10-21 08:52 | `restore-walg.sh` from **S3** (SeaweedFS 4.48 on 127.0.0.1, `s3://walg/drill`, access keys enforced; `walg-db` archiving, base `…33` in 2 s) | `08:51:55+00` | 1 s | 1 s (4 s script total) | **PASS** (2nd run) | run #1: the DELETE and `pg_switch_wal()` were one `psql -c` transaction, so its commit sat in an unarchived segment → "recovery ended before configured recovery target was reached" (procedure error; switch WAL in its own statement). Stopped before txn 798 (the DELETE); 5000 rows with marker id=1 while paused (`walg-db` after the DELETE: 4999); promoted, INSERT ok. Found and fixed: no `AWS_S3_FORCE_PATH_STYLE` passthrough (wal-g hung), and the script mounted `/nonexistent` in S3 mode, which Docker Desktop refuses |

Base-backup wall times (⏱ RTO input, wrapper-reported): 03:57:39 → ~1.1 s
(first successful full), 03:58:36 → ~1 s (green post-check run). Dataset is
tiny; expect linear-ish growth with data size — re-time on production-sized
data before trusting these numbers. 2026-08-25 (M17-B2) → ~2 s: **first base
backup taken off the LIVE cluster** via the default `PGHOST=db` path (the
command in Scheduling above, verbatim).

## Troubleshooting

### Docker DNS crash-loop on backends (W2 carryover, VT-5 finding)

Symptoms: an app container sits in `Restarting` forever;
`docker inspect <c> --format '{{.RestartCount}}'` climbs (observed live
2026-08-24: legacy `harness-backend-1` at RestartCount=154); logs show
`socket.gaierror: [Errno -3] Temporary failure in name resolution` from
`getaddrinfo` while resolving service names like `db`.

Root cause chain: the db (or another dependency) went away/restarted while
Docker's embedded DNS for that network got wedged; the app crashes on the
failed lookup; restart races the wedged resolver; loop. The compose stack
mitigates the trigger side with `restart: unless-stopped` on every long-running
service (db included) so dependencies come back on their own — but a wedged
network DNS needs manual repair:

Fix (in order of increasing disruption):

```sh
# 1. Confirm it is really DNS (not the app failing for another reason):
docker logs <crash-looping-container> 2>&1 | grep -i 'name resolution'
# 2. Recreate the network (drops stale DNS state):
cd <repo> && docker compose -f deploy/docker-compose.yml down
docker network rm harness_default 2>/dev/null || true   # wedge-breaker
docker compose -f deploy/docker-compose.yml up -d
# 3. Verify resolution from inside a fresh container:
docker exec harness-identity-1 python -c "import socket;print(socket.gethostbyname('db'))"
# 4. Only if STILL looping: full Docker daemon restart (last resort).
```

Prevention: never let the db volume disappear out from under a running stack;
prefer `stop`/`start` over `kill` during maintenance; keep `restart:
unless-stopped` everywhere (already the case in this compose file). Note the
crash-looping `harness-backend-1` seen in August 2026 is the LEGACY
all-in-one container (not part of the current split-service topology) —
removing it is safe: `docker rm -f harness-backend-1`.

### PITR-specific failure signatures (all hit during the 2026-08-24 drill)

| Signature | Cause | Fix |
|---|---|---|
| `FATAL: no pg_hba.conf entry for replication connection` | streaming base backup without the custom HBA | ensure `walg-db` runs with `-c hba_file=/etc/harness/pg_hba.conf` (compose does); after editing `deploy/walg/pg_hba.conf`, `SELECT pg_reload_conf();` or recreate the container |
| Server can't open `/etc/harness/pg_hba.conf` though `ls` shows it (nlink=0, ENOENT) | macOS/Docker Desktop single-file bind mount pinned a stale inode after a host-side rewrite | mount the DIRECTORY (`./walg:/etc/harness:ro`), not the file — compose does this |
| `Error: in 18+, these Docker images are configured to store database data in …` and exit | official postgres:18 ENTRYPOINT refuses data mounted at `.../data` | recovery containers run `--entrypoint postgres … -D /pgdata` (restore-walg.sh handles this); don't "fix" it by switching back to alpine images (glibc!) |
| `sh: … walg-restore-command: not found` during recovery startup | helper written into a throwaway `--rm` container | restore_command is self-contained now (`wal-g wal-fetch "%f" "%p"` inline) |
| `wal-g: not found` anywhere | stock postgres image in use | use `harness-db-walg` image (build per above) |
| `Dry run: objects would be deleted … Run with --confirm` | wal-g ≥3.0 requires `--confirm` for deletes | already handled in `backup.sh prune()`; never remove that flag |
| wal-g S3 call hangs with no error against a self-hosted endpoint | virtual-host addressing: wal-g dials `<bucket>.<host>` | `AWS_S3_FORCE_PATH_STYLE=true` (compose and restore-walg.sh pass it) |
| `mounts denied: The path /nonexistent is not shared` from restore-walg.sh | pre-2026-10-21 script mounted a placeholder path in S3 mode | fixed: the file-prefix mount is added only when `WALG_FILE_PREFIX` is set |
| `tls error: server refused TLS connection` noise in wal-g errors | libpq tries TLS first, falls back; harmless when a later line shows the real cause | ignore unless it is the ONLY error |

## Environment variable reference

Compose reads `.env` next to `deploy/`; every knob below is documented in
`.env.example` under “Backup / PITR sidecar”.

| Var | Default | Notes |
|---|---|---|
| `WALG_S3_PREFIX` / `WALG_FILE_PREFIX` | — / — | exactly ONE required; S3 vs local/file |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` / `AWS_ENDPOINT` | empty | secrets via env only |
| `AWS_S3_FORCE_PATH_STYLE` | empty | `true` for self-hosted S3 (MinIO, SeaweedFS): without it wal-g dials `<bucket>.<endpoint host>` and retries a name that does not resolve, silently |
| `WALG_ARCHIVE_TIMEOUT` | `60s` | segment force-switch; bounds RPO on a quiet db |
| `WALG_IMAGE_TAG` | `v3.0.9` | harness-db-walg tag (= Dockerfile `WALG_VERSION` minus `v`) |
| `BACKUP_MODE` (legacy alias `WALG_MODE`) | `base` | `base\|wal\|list\|retention\|config` |
| `WALG_PGHOST` | `db` | backup source; since M17-B2/OPS-01 the nightly base backup targets the LIVE primary by default (`walg-db` = drill sidecar for rehearsals) |
| `WALG_COMPRESSION_METHOD` | `lz4` | lz4/gzip/brotli/zstd/none |
| `WALG_RETENTION_KEEP` | `0` | >0 prunes after each base (passes `--confirm`) |
| `WALG_LIST_JSON` | unset | `1` → `backup-list --json` |
| `WALG_EXTRA_ARGS` | empty | appended verbatim to wal-g calls |
| `BACKUP_MIN_WAL_SECONDS` | `600` | warn when archiver idle this long post-base |
| `BACKUP_STRICT` | unset | `1` escalates that warning to exit 1 |
| `POSTGRES_PASSWORD` | `harness` | db/walg-db auth (change outside dev) |
| `PITR_IMAGE` / `PITR_CONTAINER` / `PITR_VOLUME` | `harness-db-walg:$TAG` / `harness-pitr` / `harness_pgdata_pitr` | restore-walg.sh knobs |

Logical-layer vars (`CONTAINER`, `BACKUP_KEEP`, `RESTORE_FORCE`,
`VERIFY_SCRATCH_DB`, …) are unchanged — see `deploy/backup/*.sh` headers.
RETIRED M10 knobs: `WALG_LOOP`, `WALG_INTERVAL_SECONDS` (sleeping-container
scheduling had no catch-up and hid failures — host cron owns scheduling now).

## Validated vs UNEXECUTED (M13-W3-H2, 2026-08-24)

| Item | Status | Evidence |
|---|---|---|
| Image build (Dockerfile.walg, glibc fix) | ✅ EXECUTED | built locally (arm64/aarch64) → `wal-g version v3.0.9 3e49318 PostgreSQL`; checksum verify ran inside build |
| `archive_mode=on` + `archive_command` live | ✅ EXECUTED | `show` outputs recorded; segments landed in storage (`000000010000000000000001.lz4` …); `pg_stat_archiver` 12/0 after forced switches |
| **LIVE-primary archiving + live base backup (M17-B2/OPS-01)** | ✅ EXECUTED (2026-08-25, file://) | `harness-db-1`: `archive_mode=on · archive_command='wal-g wal-push %p'`; `pg_stat_archiver` 12/0 with segments in `/tmp/harness-walg-live/wal_005/`; nightly wrapper (`--profile walg run --rm walg`, zero overrides) wrote `base_…00000E` OFF THE LIVE CLUSTER, exit 0, post-check green. Real-S3 round-trip still pending below |
| Streaming base backup via wrapper | ✅ EXECUTED | two fulls (`base_…007`, `base_…009`), exit 0, post-check inventory verified |
| `wal` push mode | ✅ EXECUTED (via archiver) | every archived segment IS a `wal-push %p` execution; CLI arg-guard also tested (bad path ⇒ exit 1) |
| `list` / `config` modes | ✅ EXECUTED | inventory tables pasted in drill log; secrets masked (`<set,masked>`) |
| `retention` prune | ✅ EXECUTED | dry-run discovered → `--confirm` added → `Objects deleted successfully: count=14`, inventory reduced to newest |
| Failure honesty | ✅ EXECUTED | bogus `PGHOST` ⇒ exit 1; missing segment ⇒ exit 1; empty-storage post-check ⇒ die |
| `restore-walg.sh --fetch-only` | ✅ EXECUTED (2026-10-21) | file:// live archive into a throwaway volume, fetch 10 s, exit 0 |
| PITR restore → scratch, pause-at-target | ✅ EXECUTED ×3 | see drill evidence log; `recovery stopping before commit of transaction 768` reproduced identically |
| Promotion + write-after-recovery | ✅ EXECUTED | `pg_is_in_recovery()=false`, new timeline 2, INSERT succeeded |
| Run-log persistence | ✅ EXECUTED | `walg_logs` volume holds appended runs across `--rm` invocations |
| Static gates | ✅ EXECUTED | `docker compose config -q` (default AND `--profile walg`) exit 0; `sh -n` clean; shellcheck clean on both scripts |
| Live-stack safety | ✅ VERIFIED | `harness_db-1`/backends untouched & healthy throughout; PITR confined to `pgdata_walg`/`harness_pgdata_pitr` |
| S3 round-trip (self-hosted S3) | ✅ EXECUTED (2026-10-21) | SeaweedFS S3 gateway, `walg-db` archive + base backup + PITR restore through S3, see drill log. A cloud bucket (AWS/R2) is still untested |
| Bucket versioning/lifecycle/IAM hardening | ❌ UNEXECUTED-needs-S3 | operator task on first real bucket |
| Cutover onto LIVE `harness_pgdata` | ❌ UNEXECUTED by design | destructive; drill covers everything except the volume swap itself |
| `BACKUP_STRICT=1`, `WALG_LIST_JSON=1`, `WALG_EXTRA_ARGS`, non-arm64 image build | ❌ UNEXECUTED | implemented but not individually exercised this wave |
| amd64/linux CI build of the image | ❌ UNEXECUTED | built/verified on Apple Silicon only; multi-stage fetch is arch-generic |

**Next credentialed operator:** set the S3 env, rerun the DRILL section
verbatim against `WALG_S3_PREFIX`, move rows from UNEXECUTED here, then
tighten the RPO row at the top.

## Retention holds and backup expiry

A team admin can place a retention hold on a run (`run_retention_holds`, migration 0056). Held data may sit in any
backup, so **no backup is expired while any hold exists**: `deploy/runners/backup.sh` (`WALG_RETENTION_KEEP`) and
`deploy/backup/backup.sh` (`BACKUP_KEEP`) check the table before they delete and skip expiry when a hold is active.
They fail closed: if `psql` cannot read the table the prune is skipped, and `backup.sh retention` exits non-zero. A base
backup itself still succeeds when its prune is skipped. A database without the table (before 0056) has no holds. Backups
that other tooling expires (bucket lifecycle rules, `rsync --delete` mirrors) are not covered and need the same check.
