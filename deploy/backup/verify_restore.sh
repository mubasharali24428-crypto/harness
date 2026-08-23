#!/bin/sh
# verify_restore.sh — one-shot end-to-end proof that the newest snapshot is
# actually restorable.
#
# Inside the RUNNING db container it:
#   1. picks the newest deploy/backup/backups/*.dump,
#   2. creates a scratch database (harness_verify_restore),
#   3. restores the snapshot into it with pg_restore --exit-on-error,
#   4. compares row counts of key tables between the scratch db and the LIVE
#      database, printing PASS/FAIL per table,
#   5. drops the scratch database (also on failure, via EXIT trap).
#
# Production tables are never written; the only side effects are transient
# scratch-db objects, which are always cleaned up.
#
# Usage:
#     ./deploy/backup/verify_restore.sh
#
# Environment overrides:
#     VERIFY_SCRATCH_DB  scratch database name   (harness_verify_restore)
#     VERIFY_TABLES      tables to compare       ("users teams projects agent_events")
#     LIVE_DB            live database name      (harness)
#     CONTAINER / BACKUP_USE_COMPOSE / COMPOSE_FILE / PGUSER     as backup.sh
#
# NOTE on races: if services write to the live db between the pg_dump inside
# the snapshot and this comparison, counts can legitimately drift. Run it on
# a quiet stack, or treat small agent_events drift as "re-run when idle".

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKUP_DIR=${BACKUP_DIR:-"$SCRIPT_DIR/backups"}
CONTAINER=${CONTAINER:-harness-db-1}
BACKUP_USE_COMPOSE=${BACKUP_USE_COMPOSE:-0}
COMPOSE_FILE=${COMPOSE_FILE:-"$SCRIPT_DIR/../docker-compose.yml"}
PGUSER=${PGUSER:-harness}
LIVE_DB=${LIVE_DB:-harness}
SCRATCH_DB=${VERIFY_SCRATCH_DB:-harness_verify_restore}
TABLES=${VERIFY_TABLES:-"users teams projects agent_events"}

log() { printf '[verify %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

db_exec() {
    if [ "$BACKUP_USE_COMPOSE" = "1" ]; then
        docker compose -f "$COMPOSE_FILE" exec -T db "$@"
    else
        docker exec "$CONTAINER" "$@"
    fi
}

db_exec_in() {
    if [ "$BACKUP_USE_COMPOSE" = "1" ]; then
        docker compose -f "$COMPOSE_FILE" exec -T db "$@"
    else
        docker exec -i "$CONTAINER" "$@"
    fi
}

command -v docker >/dev/null 2>&1 || die "docker CLI not found on PATH"

for name in "$SCRATCH_DB" "$LIVE_DB"; do
    case "$name" in '' | *[!A-Za-z0-9_]*) die "refusing unsafe database name: '$name'" ;; esac
done

if [ "$BACKUP_USE_COMPOSE" != "1" ] && ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    die "container '$CONTAINER' not found/running; start the stack or set BACKUP_USE_COMPOSE=1"
fi

latest=$(ls -1 "$BACKUP_DIR"/*.dump 2>/dev/null | sort | tail -n 1 || true)
[ -n "$latest" ] || die "no *.dump snapshots in $BACKUP_DIR — run backup.sh first"
log "verifying newest snapshot: $(basename -- "$latest")"

failures=0

cleanup() {
    status=$?
    trap - EXIT
    # Best-effort teardown; never mask the real exit status.
    db_exec psql --username="$PGUSER" --dbname=postgres --quiet \
        --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$SCRATCH_DB' AND pid <> pg_backend_pid();" \
        --command="DROP DATABASE IF EXISTS \"$SCRATCH_DB\" WITH (FORCE);" \
        >/dev/null 2>&1 || true
    log "scratch database '$SCRATCH_DB' dropped"
    exit $status
}
trap cleanup EXIT

log "creating fresh scratch database '$SCRATCH_DB'"
db_exec psql --username="$PGUSER" --dbname=postgres --quiet --set=ON_ERROR_STOP=1 \
    --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$SCRATCH_DB' AND pid <> pg_backend_pid();" \
    --command="DROP DATABASE IF EXISTS \"$SCRATCH_DB\" WITH (FORCE);" \
    --command="CREATE DATABASE \"$SCRATCH_DB\";" \
    >/dev/null

log "restoring snapshot into scratch db (--exit-on-error)"
db_exec_in pg_restore \
    --username="$PGUSER" \
    --dbname="$SCRATCH_DB" \
    --no-owner \
    --role="$PGUSER" \
    --exit-on-error \
    <"$latest"
log "restore into scratch db completed without errors"

live_count() {
    db_exec psql --username="$PGUSER" --dbname="$1" \
        --tuples-only --no-align --command="SELECT count(*) FROM public.$2" |
        tr -d '[:space:]'
}

printf '[verify %s] %-22s %-10s %-10s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" TABLE LIVE SCRATCH RESULT
for t in $TABLES; do
    live=$(live_count "$LIVE_DB" "$t")
    scratch=$(live_count "$SCRATCH_DB" "$t")
    if [ "$live" = "$scratch" ]; then
        result=PASS
    else
        result=FAIL
        failures=$((failures + 1))
    fi
    printf '[verify %s] %-22s %-10s %-10s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$t" "$live" "$scratch" "$result"
done

if [ "$failures" -eq 0 ]; then
    log "RESULT: PASS — newest snapshot restores cleanly with identical row counts"
else
    log "RESULT: FAIL — $failures table(s) differ from live (idle-stack drift is possible; investigate if unexpected)"
    exit 1
fi
