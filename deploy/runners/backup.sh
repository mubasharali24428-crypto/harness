#!/bin/sh
# backup.sh — WAL-G base-backup + WAL-push wrapper for the harness deployment.
#
# H-018 / M13-W3-H2 (replaces Mission 10's deploy/walg/backup-walg.sh as the
# compose `walg` entrypoint; that M10 script stays on disk for host-side use
# but is no longer what the profile runs). Runs INSIDE a container that has
# both docker-network access to the database and the wal-g binary — in this
# deployment: the compose `walg` service (image harness-db-walg).
#
# What it does:
#   base      streaming base backup of the live cluster via the BASE_BACKUP
#             replication protocol (PGHOST/PGUSER env), then optional
#             retention prune, then an inventory line proving storage is real
#   wal <seg> push one WAL segment by absolute path — exactly what PostgreSQL
#             archive_command supplies (%p)
#   list      inventory backups already in storage
#   retention prune storage to WALG_RETENTION_KEEP newest fulls
#   config    secrets-masked configuration dump (dry-run; no storage traffic)
#
# Usage:
#   backup.sh [mode] [wal-segment-path]
#   # mode defaults from $BACKUP_MODE (compose sets it; legacy $WALG_MODE is
#   # still honored), else "base".
#
# Exit codes — honest and cron-safe:
#   0  success
#   1  bad usage / missing binary / missing storage target / wal-g failure /
#      post-conditions not met
#   (archive_command contract: nonzero exit ⇒ PostgreSQL retries the segment
#   later; a failed nightly wrapper must page someone, never exit 0.)
#
# Environment (all optional except a storage target):
#   BACKUP_MODE            base|wal|list|retention|config when no CLI arg
#                          ($WALG_MODE legacy alias)                    base
#   WALG_S3_PREFIX         s3://bucket/path — primary storage target
#   WALG_FILE_PREFIX       /dir — local/file target (drills)
#                          one of the two above MUST be set
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION / AWS_ENDPOINT
#   PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE   libpq env consumed by wal-g;
#                          compose sets PGHOST=${WALG_PGHOST:-db} — the LIVE
#                          primary since M17-B2 (set WALG_PGHOST=walg-db for a
#                          drill). Host-side runs without env still fall back
#                          to walg-db below (loud connection error if absent).
#   WALG_COMPRESSION_METHOD lz4|gzip|brotli|zstd|none                  lz4
#   WALG_RETENTION_KEEP    if > 0, prune after each base backup           0
#   WALG_LIST_JSON         1 = `list` emits --json                        0
#   WALG_EXTRA_ARGS        appended verbatim to the wal-g invocation     (empty)
#   WALG_LOG_DIR           append run logs here (volume-mounted); skipped
#                          gracefully if unwritable                      unset
#   BACKUP_MIN_WAL_SECONDS warn (not fail) if no WAL archived for this many
#                          seconds during `base`                         600
#   BACKUP_STRICT=1        escalate the staleness warning to exit 1      off
#
# Honest status (M13-W3-H2): executed end-to-end against file:// storage
# inside Docker (base + wal + list + retention + restore round-trip); see the
# "Validated vs UNEXECUTED" table in docs/runbooks/backup-and-pitr.md.

set -eu

MODE=${1:-${BACKUP_MODE:-${WALG_MODE:-base}}}
SEG=${2:-}

log() { printf '[walg-backup %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

usage() {
    sed -n '2,58p' "$0" | grep -E '^#' | sed 's/^# \{0,1\}//'
}

# --- run-log side channel ----------------------------------------------------
# Everything below also appends to $WALG_LOG_DIR/backup.log when that dir is
# writable (compose mounts the `walg_logs` volume there). One-shot containers
# are removed by `--rm`; without this their evidence would vanish with them.
LOG_FILE=""
if [ -n "${WALG_LOG_DIR:-}" ]; then
    if mkdir -p "$WALG_LOG_DIR" 2>/dev/null && touch "$WALG_LOG_DIR/backup.log" 2>/dev/null; then
        LOG_FILE="$WALG_LOG_DIR/backup.log"
        {
            printf '\n==== run %s mode=%s host=%s ====\n' \
                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" "${PGHOST:-<unset>}"
        } >>"$LOG_FILE"
    else
        log "WARN: WALG_LOG_DIR=$WALG_LOG_DIR not writable — continuing without persistent log"
    fi
fi

# Log to stderr AND the persistent file (if any). stderr stays the primary
# stream so `docker compose run` output remains complete.
tlog() {
    log "$@"
    [ -n "$LOG_FILE" ] && printf '[walg-backup %s] %s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG_FILE" || true
}

# --- first gates: binary + storage target ------------------------------------
command -v wal-g >/dev/null 2>&1 ||
    die "wal-g binary not found on PATH (compose walg services use image harness-db-walg; see deploy/walg/Dockerfile.walg)"

if [ -z "${WALG_S3_PREFIX:-}" ] && [ -z "${WALG_FILE_PREFIX:-}" ]; then
    die "no storage target: set WALG_S3_PREFIX (e.g. s3://harness-backups/pg) or WALG_FILE_PREFIX (local drills)"
fi

export PGHOST="${PGHOST:-walg-db}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-harness}"
export PGDATABASE="${PGDATABASE:-harness}"
# PGPASSWORD intentionally NOT defaulted here — in the compose profile it
# comes from the env block; on a host use ~/.pgpass or PGPASSFILE.
WALG_RETENTION_KEEP=${WALG_RETENTION_KEEP:-0}
WALG_EXTRA_ARGS=${WALG_EXTRA_ARGS:-}
BACKUP_MIN_WAL_SECONDS=${BACKUP_MIN_WAL_SECONDS:-600}

case "$WALG_RETENTION_KEEP" in
    '' | *[!0-9]*) die "WALG_RETENTION_KEEP must be a non-negative integer (got '$WALG_RETENTION_KEEP')" ;;
esac
case "$BACKUP_MIN_WAL_SECONDS" in
    '' | *[!0-9]*) die "BACKUP_MIN_WAL_SECONDS must be a non-negative integer (got '$BACKUP_MIN_WAL_SECONDS')" ;;
esac

run_walg() {
    # shellcheck disable=SC2086  # deliberate word-split of operator-provided extras
    wal-g $WALG_EXTRA_ARGS "$@"
}

# Run retention holds (migration 0056) mean held data may sit in any backup, so expiry waits for
# the release. Fails closed: holds that cannot be read count as holds. 0 = prune forbidden.
holds_forbid_pruning() {
    HOLDS=$(psql -Atqc "select to_regclass('run_retention_holds') is not null" 2>/dev/null) ||
        { tlog "WARN: cannot read run retention holds (psql failed); not pruning"; return 0; }
    [ "$HOLDS" = "t" ] || return 1 # no hold table (pre-0056 schema): no holds exist
    HOLDS=$(psql -Atqc "select count(*) from run_retention_holds" 2>/dev/null) ||
        { tlog "WARN: cannot count run retention holds; not pruning"; return 0; }
    case "$HOLDS" in '' | *[!0-9]*) tlog "WARN: unreadable hold count '$HOLDS'; not pruning"; return 0 ;; esac
    if [ "$HOLDS" -gt 0 ]; then tlog "retention: $HOLDS run retention hold(s) active"; return 0; fi
    return 1
}

PRUNE_SKIPPED=0
prune() {
    if [ "$WALG_RETENTION_KEEP" -gt 0 ] && holds_forbid_pruning; then
        PRUNE_SKIPPED=1
        tlog "retention: SKIPPED, no backup is expired while run retention holds are active"
    elif [ "$WALG_RETENTION_KEEP" -gt 0 ]; then
        tlog "retention: deleting all but newest $WALG_RETENTION_KEEP backup(s)"
        # --confirm is REQUIRED by wal-g >=3.0.x or `delete` stays a DRY RUN
        # (observed live: "Dry run: objects would be deleted … Run with
        # --confirm"). Setting WALG_RETENTION_KEEP>0 IS the confirmation.
        run_walg delete retain "$WALG_RETENTION_KEEP" --confirm
    fi
}

dump_config() {
    tlog "resolved configuration (secret-looking values masked):"
    for v in \
        WALG_S3_PREFIX WALG_FILE_PREFIX AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY \
        AWS_DEFAULT_REGION AWS_ENDPOINT AWS_S3_FORCE_PATH_STYLE AWS_CA_BUNDLE \
        WALG_COMPRESSION_METHOD WALG_UPLOAD_CONCURRENCY WALG_DOWNLOAD_CONCURRENCY \
        PGHOST PGPORT PGUSER PGDATABASE \
        WALG_RETENTION_KEEP WALG_LIST_JSON WALG_EXTRA_ARGS WALG_LOG_DIR; do
        # shellcheck disable=SC1083  # deliberate indirection: eval resolves $v's NAME
        eval val=\"\${$v:-}\"
        case $v in
            *KEY* | *SECRET* | *PASSWORD* | *TOKEN*)
                [ -n "$val" ] && val='<set,masked>'
                printf '    %s=%s\n' "$v" "$val"
                ;;
            *)
                printf '    %s=%s\n' "$v" "$val"
                ;;
        esac
        [ -n "$LOG_FILE" ] && printf '    %s=%s\n' "$v" "$val" >>"$LOG_FILE" || true
    done
}

# Post-base-backup honesty checks: prove the archive is actually receiving
# WAL and that a full backup really landed. A silent no-op must NOT look like
# success.
verify_base() {
    START_TS=$1
    if command -v psql >/dev/null 2>&1; then
        if ARCH=$(psql -Atqc "select last_archived_wal, extract(epoch from now()-last_archived_time)::int from pg_stat_archiver;" 2>/dev/null) &&
            [ -n "$ARCH" ] && [ "$ARCH" != "|" ]; then
            LAST_WAL=$(printf '%s' "$ARCH" | cut -d'|' -f1)
            AGE=$(printf '%s' "$ARCH" | cut -d'|' -f2)
            tlog "archiver check: last_archived_wal=$LAST_WAL age=${AGE}s"
            case "$AGE" in '' | *[!0-9]*) AGE=999999999 ;; esac
            if [ "$AGE" -gt "$BACKUP_MIN_WAL_SECONDS" ]; then
                tlog "WARN: pg_stat_archiver shows no WAL archived for ${AGE}s (> ${BACKUP_MIN_WAL_SECONDS}s) — archiving may be broken; PITR coverage stops at older segments"
                [ "${BACKUP_STRICT:-0}" = "1" ] && die "strict mode: stale archiver"
            fi
        else
            tlog "WARN: could not read pg_stat_archiver via psql (continuing; check archiving manually)"
        fi
    else
        tlog "WARN: psql unavailable — skipping post-backup archiver sanity check"
    fi

    # Post-check matches wal-g v3.x `backup-list --detail` reality (verified
    # live): a header line beginning `backup_name` plus one row per backup
    # (`base_<...>`); older versions printed a `name` column instead.
    if run_walg backup-list --detail 2>/dev/null | grep -Eq '^(backup_name|name)[[:space:]]|^base_'; then
        ELAPSED=$(( $(date -u +%s) - START_TS ))
        tlog "post-check: backup inventory lists at least one full backup (base backup verified in storage)"
        tlog "timed RTO evidence slot — base backup wall time: ${ELAPSED}s (record this in docs/runbooks/backup-and-pitr.md drill table)"
        # Part 28: record the verified backup; the orchestrator exposes the newest as
        # harness_backup_last_success_timestamp_seconds (HarnessBackupStale). A failed record is loud
        # but does not fail a backup that did land: the stale alert then fires, which is the point.
        if command -v psql >/dev/null 2>&1 &&
            psql -Atqc "INSERT INTO backup_runs (kind) VALUES ('base')" >/dev/null 2>&1; then
            tlog "recorded the base backup in backup_runs"
        else
            tlog "WARN: could not record the base backup in backup_runs; HarnessBackupStale will fire"
        fi
    else
        die "post-check FAILED: storage inventory empty after backup-push — treat the backup as NOT taken"
    fi
}

run_mode_once() {
    case "$MODE" in
        config)
            dump_config
            ;;

        base)
            # Streaming (remote) mode per upstream PostgreSQL.md: no data-dir
            # argument; wal-g streams via BASE_BACKUP using libpq env. Needs a
            # role with replication rights (the compose superuser qualifies).
            tlog "starting STREAMING base backup via PGHOST=$PGHOST (BASE_BACKUP protocol)"
            START_TS=$(date -u +%s)
            run_walg backup-push
            tlog "base backup complete"
            prune
            verify_base "$START_TS"
            ;;

        wal)
            [ -n "$SEG" ] || die "usage: backup.sh wal </absolute/path/to/segment>  (archive_command supplies %p)"
            case "$SEG" in
                /*) : ;;
                *) die "WAL segment path must be absolute (PostgreSQL passes an absolute %p): '$SEG'" ;;
            esac
            [ -f "$SEG" ] || die "segment not found: $SEG"
            # archive_command contract: nonzero ⇒ PostgreSQL retries later.
            run_walg wal-push "$SEG"
            ;;

        list)
            if [ "${WALG_LIST_JSON:-0}" = "1" ]; then
                run_walg backup-list --json
            else
                run_walg backup-list --detail
            fi
            ;;

        retention)
            [ "$WALG_RETENTION_KEEP" -gt 0 ] ||
                die "refusing to prune: WALG_RETENTION_KEEP=0 (set it to a positive integer)"
            prune
            [ "$PRUNE_SKIPPED" = 0 ] || die "refusing to prune: run retention holds are active or unreadable"
            ;;

        -h | --help | help)
            usage
            ;;

        *)
            usage >&2
            die "unknown mode: '$MODE'"
            ;;
    esac
}

run_mode_once
tlog "done (mode=$MODE)"
