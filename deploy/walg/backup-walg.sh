#!/bin/sh
# backup-walg.sh — WAL-G base backups + WAL archiving driver for the harness deployment.
#
# Mission 10 node B4 companion to the wave8 pg_dump scripts in ../backup/.
# This script ONLY drives the `wal-g` CLI; it never touches the database
# directly (WAL-G speaks the PostgreSQL protocol / reads the data directory
# itself).
#
# Upstream references (usage below follows these faithfully):
#   * https://github.com/wal-g/wal-g/blob/master/docs/PostgreSQL.md
#     (rendered copy: https://wal-g.readthedocs.io/PostgreSQL.html)
#     - "backup-push": local mode passes the PGDATA path
#         wal-g backup-push /path/to/pgdata
#       remote/streaming mode omits the path and uses the standard libpq
#       environment (PGHOST/PGPORT/PGUSER/...) to stream over the
#       BASE_BACKUP protocol (needs a connection with replication rights;
#       the dev compose superuser qualifies):
#           PGHOST=db wal-g backup-push
#     - "wal-push": takes the ABSOLUTE path of a finished WAL segment;
#       intended to be invoked from archive_command='wal-g wal-push %p'
#     - "backup-list": lists backups present in storage — the cheapest
#       credentialed smoke test (no DB connection needed)
#     - "delete retain N": keeps the newest N backups
#   * https://www.postgresql.org/docs/current/runtime-config-wal.html
#     (archive_command contract: exit 0 = success, else retry forever)
#
# Usage:
#     backup-walg.sh base        take a base backup (default)
#     backup-walg.sh wal <seg>   push one WAL segment (absolute path, i.e. %p)
#     backup-walg.sh list        list backups in storage (WALG_LIST_JSON=1 for --json)
#     backup-walg.sh retention   prune storage to newest WALG_RETENTION_KEEP
#     backup-walg.sh config      print resolved configuration (secrets masked);
#                                dry-run/config dump — runs NO storage traffic
#
# The mode comes from the first CLI argument OR from $WALG_MODE (the compose
# `walg` service invokes this script with no arguments and sets WALG_MODE);
# precedence: CLI arg > WALG_MODE > "base".
#
# Environment (all optional except a storage target):
#     WALG_S3_PREFIX          s3://bucket/path  — primary storage target
#     WALG_FILE_PREFIX        /some/dir         — local/NFS target (tests/drills)
#                             (one of the two above MUST be set)
#     AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION / AWS_ENDPOINT
#     WALG_COMPRESSION_METHOD lz4 (WAL-G default) | gzip | brotli | zstd | none
#     WALG_PGDATA             data dir for local base mode (/var/lib/postgresql/data —
#                             matches the compose `pgdata` volume layout)
#     WALG_STREAM_BACKUP      1 = stream base backup over TCP via PGHOST etc.
#                             (remote mode per upstream docs) instead of reading
#                             the data directory locally          (0)
#     PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE
#                             libpq env consumed by WAL-G directly (upstream
#                             "Configuration" section); defaults fit the compose
#                             db service: db/5432/harness/harness/harness
#     WALG_RETENTION_KEEP     if > 0, run `wal-g delete retain N` after each base
#                             backup                              (0 = off)
#     WALG_LIST_JSON          1 = `list` emits --json             (0)
#     WALG_MODE               mode used when no CLI argument is given (this is
#                             how the compose `walg` service selects its job;
#                             same values as the Usage modes above)
#     WALG_LOOP               "1" or "loop" = run the chosen mode forever,
#                             sleeping WALG_INTERVAL_SECONDS between runs;
#                             anything else = single shot      (oneshot)
#     WALG_INTERVAL_SECONDS   seconds between loop iterations  (86400)
#     WALG_EXTRA_ARGS         appended verbatim to the wal-g invocation
#                             (e.g. '--rating-composer', '--permanent')
#
# Exit codes: 0 success; nonzero on any failure (archive_command semantics:
# a nonzero exit makes PostgreSQL retry the segment later). Safe for cron.
#
# Honest scope note (Mission 10): validated here with `sh -n`/`bash -n`,
# shellcheck and `config` mode only. No S3 credentials exist in the build
# environment, so no round-trip against real object storage has been run —
# see docs/runbooks/backup-and-pitr.md "Validated vs not validated".

set -eu

# Mode precedence: CLI arg > WALG_MODE env > "base". The compose `walg`
# sidecar invokes this script with no arguments and sets WALG_MODE instead.
MODE=${1:-${WALG_MODE:-base}}

log() { printf '[walg-backup %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

usage() {
    sed -n '2,65p' "$0" | grep -E '^#' | sed 's/^# \{0,1\}//'
}

# FIRST gate, before any other check: every mode — including `config` — needs
# the binary, so a missing wal-g must fail honestly rather than let `config`
# print a configuration nothing could act on.
command -v wal-g >/dev/null 2>&1 ||
    die "wal-g binary not found on PATH (image ships it at /usr/local/bin/wal-g; on a host install see https://github.com/wal-g/wal-g/releases)"

run_walg() {
    # shellcheck disable=SC2086  # deliberate word-split of operator-provided extras
    wal-g $WALG_EXTRA_ARGS "$@"
}

# --- configuration ----------------------------------------------------------
# Exactly one storage target is required before anything touches the network.
if [ -z "${WALG_S3_PREFIX:-}" ] && [ -z "${WALG_FILE_PREFIX:-}" ]; then
    die "no storage target: set WALG_S3_PREFIX (e.g. s3://harness-backups/pg) or WALG_FILE_PREFIX (local drills)"
fi

export PGHOST=${PGHOST:-db}
export PGPORT=${PGPORT:-5432}
export PGUSER=${PGUSER:-harness}
export PGDATABASE=${PGDATABASE:-harness}
# PGPASSWORD intentionally NOT defaulted here — in the compose profile it comes
# from the environment block; on a host use ~/.pgpass or PGPASSFILE.
WALG_PGDATA=${WALG_PGDATA:-/var/lib/postgresql/data}
WALG_RETENTION_KEEP=${WALG_RETENTION_KEEP:-0}
WALG_EXTRA_ARGS=${WALG_EXTRA_ARGS:-}
case "$WALG_RETENTION_KEEP" in
    '' | *[!0-9]*) die "WALG_RETENTION_KEEP must be a non-negative integer (got '$WALG_RETENTION_KEEP')" ;;
esac

prune() {
    if [ "$WALG_RETENTION_KEEP" -gt 0 ]; then
        log "retention: deleting all but newest $WALG_RETENTION_KEEP backup(s)"
        run_walg delete retain "$WALG_RETENTION_KEEP"
    fi
}

dump_config() {
    log "resolved configuration (secret-looking values masked):"
    for v in \
        WALG_S3_PREFIX WALG_FILE_PREFIX AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY \
        AWS_DEFAULT_REGION AWS_ENDPOINT AWS_S3_FORCE_PATH_STYLE AWS_CA_BUNDLE \
        WALG_COMPRESSION_METHOD WALG_UPLOAD_CONCURRENCY WALG_DOWNLOAD_CONCURRENCY \
        WALG_DELTA_MAX_STEPS WALG_PREVENT_WAL_OVERWRITE WALG_DISK_RATE_LIMIT \
        PGHOST PGPORT PGUSER PGDATABASE WALG_PGDATA \
        WALG_STREAM_BACKUP WALG_RETENTION_KEEP WALG_LIST_JSON WALG_EXTRA_ARGS; do
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
    done
    printf '    archive_command (paste into db config once the db image\n'
    printf '    carries the wal-g binary — see runbook):\n'
    printf '        archive_command = '"'"'wal-g wal-push %%p'"'"'\n'
}

run_mode_once() {
    case "$MODE" in
        config)
            dump_config
            ;;

        base)
            if [ "${WALG_STREAM_BACKUP:-0}" = "1" ]; then
                # Remote mode per upstream docs: no data-directory argument; WAL-G
                # streams via the BASE_BACKUP protocol using PGHOST/PGPORT/PGUSER.
                [ -n "${PGHOST:-}" ] || die "WALG_STREAM_BACKUP=1 requires PGHOST"
                log "starting STREAMING base backup via PGHOST=$PGHOST (BASE_BACKUP protocol)"
                run_walg backup-push
            else
                # Local mode per upstream docs: pass the data directory. Sanity-check
                # that it really looks like PGDATA before handing it to WAL-G.
                [ -d "$WALG_PGDATA" ] || die "data directory not found: $WALG_PGDATA (mount the pgdata volume ro, or set WALG_PGDATA)"
                [ -f "$WALG_PGDATA/PG_VERSION" ] || die "$WALG_PGDATA does not look like a PostgreSQL data directory (no PG_VERSION)"
                log "starting LOCAL base backup of $WALG_PGDATA"
                run_walg backup-push "$WALG_PGDATA"
            fi
            log "base backup complete"
            prune
            ;;

        wal)
            SEG=${2:-}
            [ -n "$SEG" ] || die "usage: backup-walg.sh wal </absolute/path/to/segment>  (archive_command supplies %p)"
            case "$SEG" in
                /*) : ;;
                *) die "WAL segment path must be absolute (PostgreSQL passes an absolute %p): '$SEG'" ;;
            esac
            [ -f "$SEG" ] || die "segment not found: $SEG"
            # archive_command contract: nonzero exit ⇒ PostgreSQL retries later.
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
            [ "$WALG_RETENTION_KEEP" -gt 0 ] || die "refusing to prune: WALG_RETENTION_KEEP=0 (set it to a positive integer)"
            prune
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

# WALG_LOOP="1" or "loop" re-runs the chosen mode every WALG_INTERVAL_SECONDS
# (what the compose `walg` service's loop setting means); anything else —
# including unset — runs the mode once and exits (cron-friendly default).
case "${WALG_LOOP:-oneshot}" in
    1 | loop)
        WALG_INTERVAL_SECONDS=${WALG_INTERVAL_SECONDS:-86400}
        case "$WALG_INTERVAL_SECONDS" in
            '' | *[!0-9]*) die "WALG_INTERVAL_SECONDS must be a non-negative integer (got '$WALG_INTERVAL_SECONDS')" ;;
        esac
        log "loop mode: running '$MODE' every ${WALG_INTERVAL_SECONDS}s (Ctrl-C / container stop ends it)"
        while :; do
            run_mode_once
            sleep "$WALG_INTERVAL_SECONDS"
        done
        ;;

    *)
        run_mode_once
        ;;
esac

log "done"
