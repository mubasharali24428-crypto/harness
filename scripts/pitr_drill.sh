#!/usr/bin/env bash
# WAL-G point-in-time drill against real object storage (S3 API, MinIO).
#
#   ./scripts/pitr_drill.sh
#
# What it does, and why the order differs from the old note in the runbook:
#   1. seed a marker table, then take a BASE BACKUP (so the backup holds id=1
#      only);
#   2. insert id=2 AFTER the backup and record a target second between that
#      insert and the disaster;
#   3. force WAL archiving to the bucket, then DELETE id=2;
#   4. restore from the bucket to the recorded second in a scratch volume.
# Everything happens in a PRIVATE compose project (default
# harness-pitr-<utc stamp>, own network, volumes, MinIO and `walg-db`); the
# live stack is never touched. Safe by default: the script refuses a project
# that already has containers, never reads .env unless PITR_ENV_FILE names one,
# and its cleanup only ever removes things it created (`down -v` on its own
# project, its own restore container and volume). PITR_KEEP=1 skips cleanup.
#
# Proof of a real rewind: id=2 can only exist after replaying WAL written
# after the base backup, and can only be absent from later WAL — so both rows
# at the paused target, plus a clean promote, is the whole claim.
#
# Evidence: docs/briefs/evidence/pilot-prep/walg-s3-pitr-drill.{json,log}
set -euo pipefail

cd "$(dirname "$0")/.."

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
PROJECT=${PITR_PROJECT:-harness-pitr-$(printf '%s' "$STAMP" | tr 'A-Z' 'a-z')}
case "$PROJECT" in
    harness | harness-loadtest | '' | *[!a-z0-9_-]* | [-_]*)
        echo "refusing PITR_PROJECT='$PROJECT': must be lowercase [a-z0-9_-] and not the live or load-test project" >&2
        exit 1 ;;
esac
# A project that already owns containers is somebody's stack (possibly live):
# the cleanup below is only safe because this run created everything it removes.
if [ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")" ]; then
    echo "refusing PITR_PROJECT='$PROJECT': project already has containers; pick a fresh name" >&2
    exit 1
fi

COMPOSE=(docker compose -p "$PROJECT")
if [ -n "${PITR_ENV_FILE:-}" ]; then
    COMPOSE+=(--env-file "$PITR_ENV_FILE")
fi
COMPOSE+=(-f deploy/docker-compose.yml --profile walg)
# The compose file pins the default network name (shared with the live stack);
# this override keeps the drill off it.
export HARNESS_NETWORK_DEFAULT="${PROJECT}_default"
PITR_CONTAINER="${PROJECT}-restore"
PITR_VOLUME="${PROJECT}_pgdata_restore"
if docker volume inspect "$PITR_VOLUME" >/dev/null 2>&1; then
    echo "refusing: restore volume '$PITR_VOLUME' already exists" >&2
    exit 1
fi
BUCKET=${WALG_DRILL_BUCKET:-harness-walg}
MINIO_USER=${MINIO_ROOT_USER:-harnessdrill}
MINIO_PASSWORD=${MINIO_ROOT_PASSWORD:-harness-drill-secret}
MINIO_IMAGE=${MINIO_IMAGE:-bitnamilegacy/minio@sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c}
OUT_JSON=${PITR_DRILL_OUT:-docs/briefs/evidence/pilot-prep/walg-s3-pitr-drill.json}
OUT_LOG=${PITR_DRILL_LOG:-docs/briefs/evidence/pilot-prep/walg-s3-pitr-drill.log}
BACKUP_OUT=$(mktemp "${TMPDIR:-/tmp}/pitr-backup.XXXXXX")

# Storage target for every wal-g invocation in this run. A fresh prefix per
# run keeps drill histories from mixing in one bucket.
export WALG_S3_PREFIX="s3://${BUCKET}/pitr-$(printf '%s' "$STAMP" | tr 'A-Z' 'a-z')"
export WALG_FILE_PREFIX=""
export AWS_ACCESS_KEY_ID="$MINIO_USER"
export AWS_SECRET_ACCESS_KEY="$MINIO_PASSWORD"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_ENDPOINT=http://minio:9000
export AWS_S3_FORCE_PATH_STYLE=true
export WALG_DRILL_BUCKET="$BUCKET"
export MINIO_ROOT_USER="$MINIO_USER" MINIO_ROOT_PASSWORD="$MINIO_PASSWORD"
# The wrapper defaults to the LIVE primary; the drill must stream from its own
# walg-db instance.
export WALG_PGHOST=walg-db

mkdir -p "$(dirname "$OUT_JSON")"
: >"$OUT_LOG"
exec > >(tee -a "$OUT_LOG") 2>&1

log() { printf '[pitr-drill %s] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }

cleanup() {
    rm -f "$BACKUP_OUT"
    [ "${PITR_KEEP:-0}" = "1" ] && { log "PITR_KEEP=1: leaving project $PROJECT in place"; return 0; }
    docker rm -f "$PITR_CONTAINER" >/dev/null 2>&1 || true
    docker volume rm "$PITR_VOLUME" >/dev/null 2>&1 || true
    "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

psql_exec() {
    docker exec "$DB" psql -U harness -d harness "$@"
}

archive_stat() {
    docker exec "$DB" psql -U harness -d harness -tAc \
        "select archived_count || ',' || failed_count from pg_stat_archiver;"
}

# Force the current segment out and WAIT until the archiver reports it as the
# last archived WAL. Without the wait, a restore can start while the segment
# that holds the very record it needs is still in flight.
switch_and_wait() {
    local lsn seg last=""
    lsn=$(psql_exec -qAt -c "SELECT pg_switch_wal();" | tr -d '[:space:]')
    seg=$(psql_exec -qAt -c "SELECT pg_walfile_name('$lsn');" | tr -d '[:space:]')
    for _ in $(seq 1 120); do
        last=$(psql_exec -qAt -c "SELECT last_archived_wal FROM pg_stat_archiver;" | tr -d '[:space:]')
        [ "$last" = "$seg" ] && { echo "$seg"; return 0; }
        sleep 1
    done
    log "archive wait timed out for $seg (last archived: $last)"
    return 1
}

log "bucket prefix $WALG_S3_PREFIX"
# Bucket first: walg-db starts archiving the moment it boots, and a missing
# bucket would show up as permanent failures in pg_stat_archiver.
"${COMPOSE[@]}" up -d --wait --wait-timeout 180 minio
"${COMPOSE[@]}" run --rm minio-init >/dev/null
"${COMPOSE[@]}" up -d --wait --wait-timeout 180 walg-db
DB=$("${COMPOSE[@]}" ps -q walg-db)
[ -n "$DB" ] || { log "walg-db container not found in project $PROJECT"; exit 1; }
# The wrapper records each verified base backup in backup_runs (migration
# 0059). The scratch cluster is unmigrated, so apply just that table;
# otherwise the wrapper warns and the recording path is never exercised.
docker exec -i "$DB" psql -U harness -d harness -q -v ON_ERROR_STOP=1 \
    <section1/migrations/0059_backup_runs.sql >/dev/null
# PostgreSQL 15+ persists cumulative archiver counters in the data directory,
# so failures from earlier drill runs survive a container restart. Reset them
# to measure THIS run, then assert a clean record below.
psql_exec -q -c "SELECT pg_stat_reset_shared('archiver');" >/dev/null
log "object store ready (bucket $BUCKET, versioning on)"
# Warm-up: the first wal-push after a boot can be slow (DNS + connection
# setup). Get that out of the way before any timed step.
switch_and_wait >/dev/null || { log "warm-up archive did not reach storage"; exit 1; }

# --- 1. baseline + base backup ------------------------------------------------
psql_exec -q -c "DROP TABLE IF EXISTS pitr_marker;"
psql_exec -q -c "CREATE TABLE pitr_marker(id int primary key, label text, t timestamptz not null default now());"
psql_exec -q -c "INSERT INTO pitr_marker(id, label) VALUES (1, 'before-backup');"
log "baseline row id=1 written"

BACKUP_START=$(date +%s)
BACKUP_MODE=base "${COMPOSE[@]}" run --rm walg >"$BACKUP_OUT" 2>&1 || {
    cat "$BACKUP_OUT"
    log "base backup FAILED"
    exit 1
}
cat "$BACKUP_OUT"
BASE_BACKUP_S=$(($(date +%s) - BACKUP_START))
grep -q "base backup complete" "$BACKUP_OUT" || {
    log "base backup did not report completion"
    exit 1
}
# Backup freshness monitoring depends on this row; a WARN here is a failure.
BACKUP_RECORDED=1
grep -q "recorded the base backup in backup_runs" "$BACKUP_OUT" || BACKUP_RECORDED=0
BACKUP_RUNS=$(psql_exec -tAc "SELECT count(*) FROM backup_runs WHERE kind='base';" | tr -d '[:space:]')
[ "$BACKUP_RUNS" = "1" ] || BACKUP_RECORDED=0
log "backup_runs rows for this backup: $BACKUP_RUNS (recorded=$BACKUP_RECORDED)"

# --- 2. post-backup change + target ------------------------------------------
psql_exec -q -c "INSERT INTO pitr_marker(id, label) VALUES (2, 'after-backup');"
INSERT_TS=$(psql_exec -qAt -c \
    "SELECT to_char(t, 'YYYY-MM-DD HH24:MI:SS+00') FROM pitr_marker WHERE id = 2;")
[ -n "$INSERT_TS" ] || { log "marker insert did not land"; exit 1; }
log "row id=2 committed at $INSERT_TS"
sleep 1
TARGET=$(date -u +"%Y-%m-%d %H:%M:%S+00")
log "recovery target $TARGET (strictly after id=2, strictly before the delete)"

switch_and_wait >/dev/null
ARCHIVED=$(archive_stat)
log "archiver after switch: archived,failed=$ARCHIVED"
[ "${ARCHIVED#*,}" = "0" ] || { log "archiver has failures"; exit 1; }

# --- 3. disaster --------------------------------------------------------------
sleep 1
psql_exec -q -c "DELETE FROM pitr_marker WHERE id=2;"
switch_and_wait >/dev/null
LIVE_COUNT=$(psql_exec -tAc "SELECT count(*) FROM pitr_marker;" | tr -d '[:space:]')
log "after DELETE the drill database has $LIVE_COUNT row(s) (expect 1)"

# --- 4. timed restore from the bucket ----------------------------------------
RESTORE_START=$(date +%s)
PITR_CONTAINER="$PITR_CONTAINER" PITR_VOLUME="$PITR_VOLUME" PITR_NETWORK="${PROJECT}_default" \
    ./deploy/walg/restore-walg.sh --target-time "$TARGET"
RESTORE_S=$(($(date +%s) - RESTORE_START))
FETCH_S=$(grep -oE 'base backup fetched in [0-9]+s' "$OUT_LOG" | tail -1 | grep -oE '[0-9]+' || echo 0)
REPLAY_S=$(grep -oE 'server ready \(paused at target\) in [0-9]+s' "$OUT_LOG" | tail -1 | grep -oE '[0-9]+' || echo 0)

# --- 5. verify on the paused server -----------------------------------------
IS_RECOVERY=$(docker exec "$PITR_CONTAINER" psql -U harness -d harness -tAc "SELECT pg_is_in_recovery();" | tr -d '[:space:]')
ROWS=$(docker exec "$PITR_CONTAINER" psql -U harness -d harness -tAc \
    "SELECT string_agg(id::text, ',' ORDER BY id) FROM pitr_marker;" | tr -d '[:space:]')
log "recovered rows: $ROWS (expect 1,2), in_recovery=$IS_RECOVERY"

PASS=1
[ "$BACKUP_RECORDED" = "1" ] || PASS=0
[ "$IS_RECOVERY" = "t" ] || PASS=0
[ "$ROWS" = "1,2" ] || PASS=0
[ "$LIVE_COUNT" = "1" ] || PASS=0

docker exec "$PITR_CONTAINER" psql -U harness -c "SELECT pg_wal_replay_resume();" >/dev/null
sleep 3
PROMOTED=$(docker exec "$PITR_CONTAINER" psql -U harness -d harness -tAc "SELECT pg_is_in_recovery();" | tr -d '[:space:]')
[ "$PROMOTED" = "f" ] || PASS=0
log "promoted, in_recovery=$PROMOTED"

python3 - "$OUT_JSON" <<PY
import json
data = {
    "drill": "walg-s3-pitr",
    "executedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "storage": {
        "kind": "s3-compatible (MinIO)",
        "bucket": "$BUCKET",
        "prefix": "$WALG_S3_PREFIX",
        "endpoint": "$AWS_ENDPOINT",
        "versioning": True,
        "credentials": "drill-only, from compose defaults",
    },
    "project": "$PROJECT",
    "source": "drill instance walg-db in a private compose project, not the live primary",
    "backupRecorded": True if "$BACKUP_RECORDED" == "1" else False,
    "target": "$TARGET",
    "rowsAtTarget": "$ROWS",
    "rowsAfterDisaster": "$LIVE_COUNT",
    "promoted": True if "$PROMOTED" == "f" else False,
    "timingsSeconds": {
        "baseBackup": $BASE_BACKUP_S,
        "fetch": $FETCH_S,
        "replayToPause": $REPLAY_S,
        "restoreTotal": $RESTORE_S,
    },
    "result": "pass" if $PASS else "fail",
    "notes": [
        "id=2 was inserted AFTER the base backup, so its presence proves WAL replay.",
        "The delete happened after the target, so its absence proves the rewind.",
        "Bucket versioning is enabled for ransomware recovery.",
        "Cutover onto the live volume remains a manual, destructive decision.",
    ],
}
json.dump(data, open("$OUT_JSON", "w"), indent=2)
print(json.dumps(data, indent=2))
PY

[ "$PASS" = "1" ] || { log "DRILL FAILED"; exit 1; }
log "PASS — evidence $OUT_JSON"
