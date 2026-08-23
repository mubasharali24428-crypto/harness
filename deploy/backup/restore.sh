#!/bin/sh
# restore.sh — restore a backup.sh snapshot into a FRESH database.
#
# Drops and recreates the target database, streams the custom-format dump
# back in with pg_restore (--exit-on-error), then sanity-checks row counts
# of key tables — optionally against an expectations file.
#
# Usage:
#     ./deploy/backup/restore.sh <path/to/snapshot.dump> [expectations.txt]
#
# DANGER: the target database is DROPPED. The default target is the live
# 'harness' database; a confirmation prompt guards it unless RESTORE_FORCE=1.
# To rehearse without touching production data, override RESTORE_TARGET_DB.
#
# Environment overrides (defaults fit deploy/docker-compose.yml):
#     RESTORE_TARGET_DB  database to restore INTO        (harness)
#     RESTORE_FORCE      1 = skip the drop-recreate prompt
#     EXPECTATION_FILE   expectations file if arg 2 not given
#     CONTAINER / BACKUP_USE_COMPOSE / COMPOSE_FILE / PGUSER   as backup.sh
#
# Expectations format: one "<table> <count>" per line, '#' starts a comment.
# Generate one from a healthy db before the incident:
#
#     for t in users teams projects agent_events; do
#         printf '%s %s\n' "$t" \
#             "$(docker exec harness-db-1 psql -U harness -d harness -tAc "SELECT count(*) FROM $t")"
#     done > deploy/backup/expectations.txt

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONTAINER=${CONTAINER:-harness-db-1}
BACKUP_USE_COMPOSE=${BACKUP_USE_COMPOSE:-0}
COMPOSE_FILE=${COMPOSE_FILE:-"$SCRIPT_DIR/../docker-compose.yml"}
PGUSER=${PGUSER:-harness}
TARGET_DB=${RESTORE_TARGET_DB:-harness}

log() { printf '[restore %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
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

DUMP=${1:-}
[ -n "$DUMP" ] || die "usage: restore.sh <snapshot.dump> [expectations.txt]"
[ -f "$DUMP" ] || die "snapshot not found: $DUMP"
[ -s "$DUMP" ] || die "snapshot is empty: $DUMP"
# Absolute path so stdin redirection works from any cwd.
DUMP=$(CDPATH= cd -- "$(dirname -- "$DUMP")" && pwd)/$(basename -- "$DUMP")

EXPECT=${2:-${EXPECTATION_FILE:-}}
[ -z "$EXPECT" ] || [ -f "$EXPECT" ] || die "expectations file not found: $EXPECT"

case "$TARGET_DB" in
    '' | *[!A-Za-z0-9_]*) die "refusing unsafe database name: '$TARGET_DB'" ;;
esac

if [ "${RESTORE_FORCE:-0}" != "1" ]; then
    printf '[restore] WARNING: this will DROP DATABASE "%s" and replace its\n' "$TARGET_DB"
    printf '[restore] contents with:\n'
    printf '[restore]     %s\n' "$DUMP"
    printf '[restore] Data currently in "%s" will be permanently destroyed.\n' "$TARGET_DB"
    printf '[restore] Type YES (all capitals) to continue, anything else aborts: '
    IFS= read -r answer
    [ "$answer" = "YES" ] || die "aborted by operator"
else
    log "RESTORE_FORCE=1 — skipping drop-recreate confirmation"
fi

if [ "$BACKUP_USE_COMPOSE" != "1" ] && ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    die "container '$CONTAINER' not found/running; start the stack or set BACKUP_USE_COMPOSE=1"
fi

log "terminating connections to '$TARGET_DB'"
db_exec psql --username="$PGUSER" --dbname=postgres --quiet \
    --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$TARGET_DB' AND pid <> pg_backend_pid();" \
    >/dev/null

log "dropping + recreating empty database '$TARGET_DB'"
db_exec psql --username="$PGUSER" --dbname=postgres --quiet --set=ON_ERROR_STOP=1 \
    --command="DROP DATABASE IF EXISTS \"$TARGET_DB\" WITH (FORCE);" \
    --command="CREATE DATABASE \"$TARGET_DB\";"
log "database '$TARGET_DB' recreated"

log "streaming snapshot into '$TARGET_DB' (pg_restore --exit-on-error)"
db_exec_in pg_restore \
    --username="$PGUSER" \
    --dbname="$TARGET_DB" \
    --no-owner \
    --role="$PGUSER" \
    --exit-on-error \
    <"$DUMP"
log "restore completed without errors"

# --- row-count sanity check on key tables ---------------------------------
KEY_TABLES="users teams projects agent_events"

count_in_target() {
    db_exec psql --username="$PGUSER" --dbname="$TARGET_DB" \
        --tuples-only --no-align --command="SELECT count(*) FROM public.$1" |
        tr -d '[:space:]'
}

for t in $KEY_TABLES; do
    # Table names come from the hardcoded list above — safe to eval-assign.
    eval "count_$t=\$(count_in_target \"\$t\")"
    eval "log \"restored table '\$t': \$count_$t rows\""
done

if [ -n "$EXPECT" ]; then
    log "comparing restored counts against expectations: $EXPECT"
    mismatches=0
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in '' | '#'*) continue ;; esac
        # shellcheck disable=SC2086  # deliberate word-split of "<table> <count>"
        set -- $line
        [ $# -eq 2 ] || die "malformed expectation line (want '<table> <count>'): '$line'"
        et=$1
        ec=$2
        case "$et" in *[!A-Za-z0-9_]*) die "unsafe table name in expectations: '$et'" ;; esac
        case "$ec" in *[!0-9]*) die "non-numeric count in expectations: '$ec'" ;; esac
        actual=$(eval "echo \${count_$et:-}")
        if [ -z "$actual" ]; then
            log "FAIL: expectations reference unknown table '$et'"
            mismatches=$((mismatches + 1))
        elif [ "$actual" != "$ec" ]; then
            log "FAIL: table '$et' expected $ec rows, restored db has $actual"
            mismatches=$((mismatches + 1))
        else
            log "ok: table '$et' = $ec rows"
        fi
    done <"$EXPECT"
    if [ "$mismatches" -gt 0 ]; then
        log "SANITY CHECK FAILED: $mismatches mismatch(es) against expectations"
        exit 1
    fi
    log "sanity check passed: all expectations matched"
else
    log "no expectations file provided — counts printed above for eyeballing only"
fi

log "done"
