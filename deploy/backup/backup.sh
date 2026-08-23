#!/bin/sh
# backup.sh — manual/nightly Postgres snapshot for the harness deployment.
#
# Dumps the harness database out of the running compose db container into a
# gzip-compressed custom-format snapshot:
#
#     deploy/backup/backups/<UTC-timestamp>.dump
#
# then prunes old snapshots, keeping the newest BACKUP_KEEP (default 7).
#
# Usage:
#     ./deploy/backup/backup.sh
#
# Environment overrides (defaults fit deploy/docker-compose.yml):
#     CONTAINER          db container name                     (harness-db-1)
#     BACKUP_USE_COMPOSE set to 1 to drive `docker compose exec -T db`
#                        instead of `docker exec` (COMPOSE_FILE resolves
#                        relative to this script)              (0)
#     COMPOSE_FILE       compose file for BACKUP_USE_COMPOSE=1 (../docker-compose.yml)
#     BACKUP_DIR         output directory                      (<script>/backups)
#     BACKUP_KEEP        snapshots to retain                   (7; 0 = no pruning)
#     PGUSER             database user                         (harness)
#     PGDATABASE         database name                         (harness)
#
# Exit codes: 0 on success; nonzero on any failure. Safe for cron.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKUP_DIR=${BACKUP_DIR:-"$SCRIPT_DIR/backups"}
BACKUP_KEEP=${BACKUP_KEEP:-7}
CONTAINER=${CONTAINER:-harness-db-1}
BACKUP_USE_COMPOSE=${BACKUP_USE_COMPOSE:-0}
COMPOSE_FILE=${COMPOSE_FILE:-"$SCRIPT_DIR/../docker-compose.yml"}
PGUSER=${PGUSER:-harness}
PGDATABASE=${PGDATABASE:-harness}

log() { printf '[backup %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# Run a command inside the db container (stdout comes back to the host).
db_exec() {
    if [ "$BACKUP_USE_COMPOSE" = "1" ]; then
        docker compose -f "$COMPOSE_FILE" exec -T db "$@"
    else
        docker exec "$CONTAINER" "$@"
    fi
}

# Like db_exec, but also feeds the host's stdin into the container command.
db_exec_in() {
    if [ "$BACKUP_USE_COMPOSE" = "1" ]; then
        docker compose -f "$COMPOSE_FILE" exec -T db "$@"
    else
        docker exec -i "$CONTAINER" "$@"
    fi
}

command -v docker >/dev/null 2>&1 || die "docker CLI not found on PATH"

if [ "$BACKUP_USE_COMPOSE" != "1" ] && ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    die "container '$CONTAINER' not found/running; start the stack (docker compose -f deploy/docker-compose.yml up -d) or set BACKUP_USE_COMPOSE=1"
fi

case "$BACKUP_KEEP" in
    '' | *[!0-9]*) die "BACKUP_KEEP must be a non-negative integer (got '$BACKUP_KEEP')" ;;
esac

mkdir -p "$BACKUP_DIR"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TMP_OUT="$BACKUP_DIR/$STAMP.dump.tmp"
OUT="$BACKUP_DIR/$STAMP.dump"

log "starting pg_dump of database '$PGDATABASE' (custom format, gzip level 6)"

# Dump to stdout and redirect on the HOST: deploy/ is not mounted into the db
# container, so the container cannot write the file itself. -Z 6 pins gzip
# level 6 (custom format compresses by default; being explicit keeps pg_dump
# versions honest).
db_exec pg_dump --username="$PGUSER" --dbname="$PGDATABASE" --format=custom -Z 6 >"$TMP_OUT"

if [ ! -s "$TMP_OUT" ]; then
    rm -f "$TMP_OUT"
    die "pg_dump produced an empty file; aborting without recording a snapshot"
fi

# Integrity gate: a valid custom-format dump must be parseable by pg_restore.
# Catches truncated/corrupt snapshots immediately instead of at restore time.
db_exec_in pg_restore --list <"$TMP_OUT" >/dev/null || {
    rm -f "$TMP_OUT"
    die "snapshot failed pg_restore --list integrity check; discarded"
}

mv "$TMP_OUT" "$OUT"
log "snapshot written: $OUT ($(du -h "$OUT" | cut -f1))"

# Retention: UTC timestamps sort lexicographically, so reverse-sorted names =
# newest first. Keep the newest BACKUP_KEEP, delete the rest.
if [ "$BACKUP_KEEP" -gt 0 ]; then
    stale=$(ls -1 "$BACKUP_DIR"/*.dump 2>/dev/null | sort -r | awk -v keep="$BACKUP_KEEP" 'NR > keep')
    if [ -n "$stale" ]; then
        printf '%s\n' "$stale" | while IFS= read -r f; do
            rm -f -- "$f"
            log "retention: pruned $(basename -- "$f")"
        done
        log "retention: kept newest $BACKUP_KEEP snapshot(s)"
    else
        log "retention: within limit ($BACKUP_KEEP), nothing to prune"
    fi
else
    log "retention: disabled (BACKUP_KEEP=0), keeping all snapshots"
fi

log "done"
