#!/bin/sh
# nightly.sh — the scheduled backup run (installed by install-launchd.sh; see the runbook).
#
#   1. logical dump                (backup.sh)
#   2. WAL-G base backup of the LIVE db   (compose walg one-shot, --no-deps so no second cluster
#                                    can archive into the live prefix)
#   3. artifact store snapshot, right after the base, then verified against the database
#   4. mirror the WAL-G archive out of /tmp (rsync, no --delete) to $MIRROR_DIR
#
# Usage: nightly.sh            run now (manual / kickstart-proof)
#        nightly.sh --if-due   run only if the newest base backup is older than DUE_AFTER_HOURS
#                              (23). The launchd agent uses this, hourly plus 03:17, so a Mac that
#                              was asleep or had Docker down at 03:17 catches up as soon as it can.
# Retention: WALG_RETENTION_KEEP (default 7) newest base backups are kept, older ones and the WAL
# before them are deleted by the walg wrapper, which itself refuses while run-retention holds exist.
# Every step runs even if an earlier one fails; exit is nonzero if ANY failed, and the run is
# summarised on the last line as `nightly-backup RESULT=ok|FAILED steps=...` for log scraping.
# Not covered here: nothing leaves this machine unless OFFSITE_* is configured (see step 5).
set -u
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
MIRROR_DIR=${MIRROR_DIR:-"$HOME/harness-backups/walg-live"}
# Optional off-machine copy. Put `OFFSITE_RSYNC_TARGET=user@host:/path/` in this file (an ssh key
# without a passphrase for that host must already work: BatchMode). Backups hold user data, so
# choose a destination you control or encrypt it first.
[ ! -f "$HOME/harness-backups/offsite.env" ] || . "$HOME/harness-backups/offsite.env"
PREFIX=$(sed -n 's/^WALG_FILE_PREFIX=//p' deploy/.env .env 2>/dev/null | tail -1)
[ -n "$PREFIX" ] || PREFIX=/tmp/harness-walg-live
export WALG_FILE_PREFIX=$PREFIX
export WALG_RETENTION_KEEP=${WALG_RETENTION_KEEP:-7}
DUE_AFTER_HOURS=${DUE_AFTER_HOURS:-23}
DB_CONTAINER=${DB_CONTAINER:-harness-db-1}
FAILED=""
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
step() { name=$1; shift; echo "[nightly $(ts)] >>> $name"; if "$@"; then echo "[nightly $(ts)] ok  $name"; else echo "[nightly $(ts)] FAIL $name" >&2; FAILED="$FAILED $name"; fi; }

if [ "${1:-}" = "--if-due" ]; then
    # Unknown age (Docker down, no table, no psql) counts as due: the steps then fail loudly and the
    # next hourly tick retries, instead of silently skipping a night.
    AGE=$(docker exec "$DB_CONTAINER" psql -U harness -d harness -Atc \
        "select coalesce(floor(extract(epoch from now() - max(finished_at)))::bigint, 999999999) from backup_runs where kind = 'base'" 2>/dev/null) || AGE=999999999
    case "$AGE" in '' | *[!0-9]*) AGE=999999999 ;; esac
    if [ "$AGE" -lt $((DUE_AFTER_HOURS * 3600)) ]; then
        echo "[nightly $(ts)] not due: newest base backup is ${AGE}s old (< ${DUE_AFTER_HOURS}h)"; exit 0
    fi
fi

step logical-dump ./deploy/backup/backup.sh
step walg-base docker compose -p harness -f deploy/docker-compose.yml --env-file .env --env-file deploy/.env \
    --profile walg run --rm --no-deps walg
step store-snapshot sh -c 'OUT=$(./deploy/backup/store-backup.sh | tail -1) && ./deploy/backup/store_verify.py "$OUT"'
# The mirror follows the prune (--delete) but only when the source still holds a base backup, so a
# wiped or empty /tmp archive can never be propagated over the copy.
step archive-mirror sh -c 'ls "$2"/basebackups_005/*_backup_stop_sentinel.json >/dev/null 2>&1 || { echo "source archive has no base backup; refusing to mirror" >&2; exit 1; }
    mkdir -p "$1" && rsync -a --delete "$2/" "$1/"' _ "$MIRROR_DIR" "$PREFIX"

if [ -n "${OFFSITE_RSYNC_TARGET:-}" ]; then
    step offsite rsync -a --delete -e "ssh -o BatchMode=yes -o ConnectTimeout=20" \
        "$HOME/harness-backups/walg-live" "$HOME/harness-backups/store" "$OFFSITE_RSYNC_TARGET"
    OFFSITE=configured
else
    OFFSITE=NONE
    echo "[nightly $(ts)] WARN offsite: no OFFSITE_RSYNC_TARGET configured; every backup copy is on this machine" >&2
fi

if [ -z "$FAILED" ]; then echo "nightly-backup RESULT=ok offsite=$OFFSITE $(ts)"; exit 0; fi
echo "nightly-backup RESULT=FAILED steps=$FAILED offsite=$OFFSITE $(ts)"; exit 1
