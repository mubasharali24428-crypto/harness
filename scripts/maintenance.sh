#!/bin/sh
# maintenance.sh — VACUUM (ANALYZE)/REINDEX job for the harness deployment.
#
# M15-W5-D2 (DB#12). Runs INSIDE a container that has docker-network access
# to Postgres and the psql client — in this deployment: the compose
# `db-maintenance` service (--profile maintenance, image postgres:18-alpine,
# which ships psql; the slim backend image deliberately does not). Also runs
# standalone on the host against anything libpq env points at, which is how
# it was proven locally (see docs/runbooks/maintenance.md).
#
# What it does per pass, per table (default: agent_events rate_buckets
# audit_log):
#   1. BEFORE snapshot: n_live_tup / n_dead_tup / dead ratio / n_mod_since_
#      analyze / last (auto)vacuum/(auto)analyze from pg_stat_user_tables
#      — these are THE bloat indicators (runbook §Bloat indicators).
#   2. VACUUM (ANALYZE) <table>          — reclaim dead tuples + refresh
#      planner statistics. One statement per psql call (utility statements
#      fail inside the implicit multi-statement transaction psql would open).
#   3. Optional: REINDEX TABLE CONCURRENTLY <table> when MAINTENANCE_REINDEX=1
#      (no exclusive lock; needs PG12+; a crashed leave-behind shows up as an
#      INVALID "_ccnew" index — see runbook §Troubleshooting).
#   4. AFTER snapshot + SUMMARY line (pass/fail per table).
#
# Usage:
#   maintenance.sh [once|loop|status]
#   # command defaults from $MAINTENANCE_MODE, else "loop".
#
# Exit codes — honest and cron-safe:
#   0  success (all present tables maintained; missing-but-configured tables
#      only WARN so a fresh DB pre-migration doesn't page anyone unless the
#      configured set is ENTIRELY absent — that is exit 1)
#   1  bad storage of truth: psql unreachable, a maintenance statement failed,
#      zero of the configured tables exist
#   2  usage error (unknown command, bad knob values)
#   3  lock held by another LIVE run (`once` refuses to stack;
#      `loop` waits quietly instead — see below)
#
# Environment (compose sets sane defaults; all optional here):
#   MAINTENANCE_MODE              once|loop|status                       loop
#   MAINTENANCE_INTERVAL_SECONDS  loop cadence                        604800 (weekly)
#   MAINTENANCE_TABLES            whitespace-separated table list     agent_events
#                                                                     rate_buckets audit_log
#   MAINTENANCE_REINDEX           1 = add REINDEX TABLE CONCURRENTLY       0
#   MAINTENANCE_LOG_DIR           append run logs here (volume-mounted);
#                                 skipped gracefully if unwritable        unset
#   MAINTENANCE_LOCK_DIR          mkdir-based concurrency guard           unset
#                                 (atomic mkdir; stale after
#                                 MAINTENANCE_LOCK_STALE_SECONDS)  21600 (6h)
#   MAINTENANCE_STAMP_DIR         last-success stamps (loop dedup/catch-up) unset
#   PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE   libpq env for psql
#
# Scheduling semantics (mirrors the M13 lesson that retired WALG_LOOP):
#   - `loop` runs one pass IMMEDIATELY at startup, then sleeps
#     MAINTENANCE_INTERVAL_SECONDS between passes. Downtime therefore
#     collapses missed cycles into a single catch-up run instead of silently
#     skipping them. A restart within the same interval does NOT re-vacuum:
#     the last-success stamp in MAINTENANCE_STAMP_DIR marks us done until due.
#   - `once` always runs exactly one pass (host-cron friendly) and its exit
#     code IS the pass result — failures must page someone, never exit 0.
#   - `status` touches nothing; prints stamp, lock, log tail, live bloat.

set -eu

CMD=${1:-${MAINTENANCE_MODE:-loop}}

MODE_ONCE=once
MODE_LOOP=loop
MODE_STATUS=status

log()  { printf '[db-maintenance %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
warn() { log "WARN: $*"; }
# Global fatal (usage errors, lock refusal): prints THEN exits the shell.
die()  { log "ERROR: $*"; exit "${2:-1}"; }
# In-pass fatal: must NOT exit(1) — run_pass runs with its output buffered by
# run_pass_logged, and exiting the shell would discard the buffer along with
# this very message (observed live). Return the failure instead.
fail_run() { log "ERROR: $*"; return 1; }

usage() {
    sed -n '2,57p' "$0" | grep -E '^#' | sed 's/^# \{0,1\}//'
}

# --- knob validation ----------------------------------------------------------

is_positive_int() {
    printf '%s' "$1" | grep -Eq '^[1-9][0-9]*$'
}

INTERVAL=${MAINTENANCE_INTERVAL_SECONDS:-604800}
LOCK_STALE_SECONDS=${MAINTENANCE_LOCK_STALE_SECONDS:-21600}
REINDEX=${MAINTENANCE_REINDEX:-0}
TABLES=${MAINTENANCE_TABLES:-agent_events rate_buckets audit_log}

case "$CMD" in
    "$MODE_ONCE"|"$MODE_LOOP"|"$MODE_STATUS") ;;
    -h|--help|help) usage; exit 0 ;;
    *) usage; die "unknown command '$CMD' (want once|loop|status)" 2 ;;
esac

is_positive_int "$INTERVAL" \
    || die "MAINTENANCE_INTERVAL_SECONDS='$INTERVAL' is not a positive integer" 2
is_positive_int "$LOCK_STALE_SECONDS" \
    || die "MAINTENANCE_LOCK_STALE_SECONDS='$LOCK_STALE_SECONDS' is not a positive integer" 2
case "$REINDEX" in
    0|false|no|"") REINDEX=0 ;;
    1|true|yes)    REINDEX=1 ;;
    *) die "MAINTENANCE_REINDEX='$REINDEX' is not a boolean-ish value" 2 ;;
esac

# --- persistent side channels -------------------------------------------------

LOG_FILE=""
if [ -n "${MAINTENANCE_LOG_DIR:-}" ]; then
    if mkdir -p "$MAINTENANCE_LOG_DIR" 2>/dev/null && touch "$MAINTENANCE_LOG_DIR/maintenance.log" 2>/dev/null; then
        LOG_FILE="$MAINTENANCE_LOG_DIR/maintenance.log"
    else
        warn "MAINTENANCE_LOG_DIR=$MAINTENANCE_LOG_DIR not writable — continuing without persistent log"
    fi
fi

append_log() {
    # $@ = lines to mirror into the persistent log (best effort).
    [ -n "$LOG_FILE" ] || return 0
    printf '%s\n' "$@" >>"$LOG_FILE" 2>/dev/null || true
}

stamp_file_for_run() {
    [ -n "${MAINTENANCE_STAMP_DIR:-}" ] && mkdir -p "$MAINTENANCE_STAMP_DIR" 2>/dev/null \
        || return 1
    printf '%s/stamp_%s' "$MAINTENANCE_STAMP_DIR" "$(printf '%s' "$PGDATABASE" | tr ':/' '__')"
}

write_stamp() {
    _sf=$(stamp_file_for_run) || { warn "stamp dir unwritable — catch-up dedup disabled"; return 0; }
    printf '%s\n' "$(date -u +%s)" >"$_sf"
}

stamp_age_seconds() {
    _sf=$(stamp_file_for_run) || return 255
    [ -f "$_sf" ] || return 255
    _stored=$(cat "$_sf" 2>/dev/null) || return 255
    case "$_stored" in ''|*[!0-9]*) return 255 ;; esac
    echo $(( $(date -u +%s) - _stored ))
}

# --- lock guard (atomic mkdir; flock is not portable to busybox ash) ----------

lock_dir=${MAINTENANCE_LOCK_DIR:-}
LOCK_ACQUIRED=0

lock_owner_line() { printf '%s %s\n' "$$" "$(date -u +%s)"; }

lock_acquire() {
    [ -n "$lock_dir" ] || return 0                      # no lock configured: allowed
    mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true
    if mkdir "$lock_dir" 2>/dev/null; then
        lock_owner_line >"$lock_dir/owner"
        LOCK_ACQUIRED=1
        return 0
    fi
    # Held by someone. Break it ONLY if provably stale (owner records start
    # epoch; reading OUR OWN file keeps this portable across busybox/macOS/GNU).
    _owner_pid=""
    _owner_epoch=""
    if [ -f "$lock_dir/owner" ]; then
        _line=$(cat "$lock_dir/owner" 2>/dev/null || true)
        _owner_pid=${_line%% *}
        _owner_epoch=${_line##* }
    fi
    _now=$(date -u +%s)
    case "$_owner_epoch" in
        ''|*[!0-9]*)
            warn "lock dir $lock_dir has no readable owner — treating as STALE and breaking it"
            ;;
        *)
            _age=$((_now - _owner_epoch))
            [ "$_age" -le "$LOCK_STALE_SECONDS" ] && return 3
            warn "lock is ${_age}s old (> stale threshold ${LOCK_STALE_SECONDS}s) — breaking it"
            ;;
    esac
    rm -rf "$lock_dir" 2>/dev/null || true
    if mkdir "$lock_dir" 2>/dev/null; then
        lock_owner_line >"$lock_dir/owner"
        LOCK_ACQUIRED=1
        warn "stale lock broken and re-acquired (concurrent breaker race possible but harmless)"
        return 0
    fi
    return 3
}

lock_release() {
    [ "$LOCK_ACQUIRED" -eq 1 ] || return 0
    rm -rf "$lock_dir" 2>/dev/null || true
    LOCK_ACQUIRED=0
}
trap lock_release EXIT INT TERM

# --- SQL helpers ---------------------------------------------------------------

psql_ok() {
    # Cheap reachability probe with a short timeout so `die` names the cause.
    psql -c 'SELECT 1;' >/dev/null 2>&1
}

# Validate one identifier; safe-by-construction before ANY interpolation into
# SQL (defense in depth: the table list comes from operator env, not users,
# but interpolated strings deserve guards regardless).
valid_ident() {
    printf '%s' "$1" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$'
}

bloat_snapshot() {
    # The bloat indicators (runbook §Bloat indicators): dead tuples, dead
    # ratio, un-analyzed modifications, last (auto)vacuum/(auto)analyze.
    _list=""
    for t in "$@"; do
        # Single quotes = SQL string literal. Safe: valid_ident() has already
        # rejected anything that is not [A-Za-z0-9_] (no quote can survive).
        _list="$_list,'$t'"
    done
    _list=${_list#,}
    psql -v ON_ERROR_STOP=1 -P pager=off -c "
        SELECT relname                                   AS table_name,
               n_live_tup                                AS live,
               n_dead_tup                                AS dead,
               ROUND(n_dead_tup::numeric /
                     GREATEST(n_live_tup + n_dead_tup, 1), 4) AS dead_ratio,
               n_mod_since_analyze                       AS mod_since_anlz,
               COALESCE(last_analyze::text,
                        last_autoanalyze::text, '-')     AS last_analyzed,
               COALESCE(last_vacuum::text,
                        last_autovacuum::text, '-')      AS last_vacuumed
        FROM pg_stat_user_tables
        WHERE relname IN ($_list)
        ORDER BY n_dead_tup DESC;"
}

maintain_table() {
    # VACUUM (ANALYZE) one table; optionally REINDEX CONCURRENTLY.
    # Each utility statement rides in its OWN psql call — combining them in
    # one -c opens an implicit transaction and VACUUM/REINDEX refuse that.
    _rc=0
    log "VACUUM (ANALYZE) $1 ..."
    if ! psql -v ON_ERROR_STOP=1 -c "VACUUM (ANALYZE) \"$1\";"; then
        log "FAIL: VACUUM (ANALYZE) $1"
        return 1
    fi
    if [ "$REINDEX" -eq 1 ]; then
        log "REINDEX TABLE CONCURRENTLY $1 ..."
        if ! psql -v ON_ERROR_STOP=1 -c "REINDEX TABLE CONCURRENTLY \"$1\";"; then
            log "FAIL: REINDEX TABLE CONCURRENTLY $1 (check for leftover INVALID _ccnew indexes)"
            return 1
        fi
    fi
    return 0
}

# --- one full pass -------------------------------------------------------------

run_pass() {
    _pass_started=$(date -u +%s)
    append_log "" "==== maintenance run $(date -u +%Y-%m-%dT%H:%M:%SZ) host=${PGHOST:-<unix-socket>} db=${PGDATABASE:-<unset>} reindex=$REINDEX ===="
    log "starting pass: host=${PGHOST:-<unix-socket>} db=${PGDATABASE:-<unset>} tables=[$TABLES] reindex=$REINDEX"

    if ! psql_ok; then
        fail_run "cannot reach postgres (PGHOST=${PGHOST:-<unset>} PGPORT=${PGPORT:-5432} db=${PGDATABASE:-<unset>}; check stack health) — nothing attempted"
        return 1
    fi

    # Resolve the configured list against reality: existing → maintain,
    # absent → WARN (fresh DB pre-migration), invalid identifier → fatal.
    _present=""
    _absent=""
    for t in $TABLES; do
        if ! valid_ident "$t"; then
            fail_run "table name '$t' failed identifier validation — refusing to interpolate it"
            return 1
        fi
        if [ "$(psql -tA -c "SELECT to_regclass('\"$t\"') IS NOT NULL;")" = "t" ]; then
            _present="$_present $t"
        else
            _absent="$_absent $t"
        fi
    done
    [ -n "$_present" ] || { fail_run "NONE of the configured tables exist [${TABLES}] — schema not migrated? config typo?"; return 1; }
    for t in $_absent; do warn "table '$t' not found in database — skipping (does migration need to run?)"; done

    log "BEFORE snapshot (pg_stat_user_tables):"
    _before=$(bloat_snapshot $_present) || { fail_run "bloat snapshot query failed"; return 1; }
    printf '%s\n' "$_before"
    append_log "BEFORE:" "$_before"

    _failed=""
    _ok_count=0
    for t in $_present; do
        if maintain_table "$t"; then
            _ok_count=$((_ok_count + 1))
        else
            _failed="$_failed $t"
        fi
    done

    log "AFTER snapshot (pg_stat_user_tables):"
    _after=$(bloat_snapshot $_present) || warn "post-pass bloat snapshot failed (maintenance itself already ran)"
    printf '%s\n' "$_after"
    append_log "AFTER:" "$_after"

    _elapsed=$(( $(date -u +%s) - _pass_started ))
    if [ -z "$_failed" ]; then
        log "SUMMARY: PASS — $_ok_count table(s) maintained in ${_elapsed}s (skipped absent:${_absent:- none})"
        write_stamp
        append_log "SUMMARY: PASS — $_ok_count table(s) in ${_elapsed}s"
        return 0
    fi
    log "SUMMARY: FAIL — failed:$_failed (ok:$_ok_count, elapsed ${_elapsed}s)"
    append_log "SUMMARY: FAIL — failed:$_failed"
    return 1
}

# --- buffered execution (keeps exit codes without depending on PIPESTATUS) ----

run_pass_logged() {
    # Capture one pass into a temp buffer, then fan it out to tty + persistent
    # log. POSIX sh has no PIPESTATUS; buffering preserves the real exit code.
    _buf=$(mktemp "${TMPDIR:-/tmp}/harness-maint.XXXXXX") || die "mktemp failed" 1
    _rc=0
    run_pass >>"$_buf" 2>&1 || _rc=$?
    cat "$_buf"
    append_log "$(cat "$_buf")"
    rm -f "$_buf"
    return "$_rc"
}

# --- commands ------------------------------------------------------------------

cmd_status() {
    echo "== harness db-maintenance status =="
    echo "mode=$CMD host=${PGHOST:-<unix-socket>} port=${PGPORT:-5432} user=${PGUSER:-<unset>} db=${PGDATABASE:-<unset>}"
    echo "tables=[$TABLES] reindex=$REINDEX interval=${INTERVAL}s"
    if [ -n "${MAINTENANCE_STAMP_DIR:-}" ] && _sf=$(stamp_file_for_run 2>/dev/null) && [ -f "$_sf" ]; then
        _age=$(stamp_age_seconds)
        echo "last success: epoch $(cat "$_sf"), ${_age}s ago"
    else
        echo "last success: never (no stamp)"
    fi
    if [ -n "$lock_dir" ]; then
        if [ -f "$lock_dir/owner" ]; then
            echo "lock: HELD by [$(cat "$lock_dir/owner")] (pid epoch)"
        else
            echo "lock: free"
        fi
    fi
    if [ -n "$LOG_FILE" ] && [ -f "$LOG_FILE" ]; then
        echo "-- last log lines ($LOG_FILE) --"
        tail -n 15 "$LOG_FILE"
    fi
    if psql_ok; then
        echo "-- live bloat snapshot --"
        for t in $TABLES; do valid_ident "$t" || continue; done
        bloat_snapshot $TABLES 2>/dev/null || echo "(snapshot unavailable)"
    else
        echo "(postgres unreachable — no live snapshot)"
    fi
    return 0
}

cmd_once() {
    lock_acquire || die "another maintenance run holds the lock (pid/epoch in $lock_dir/owner); refusing to stack — use 'status'" 3
    run_pass_logged
}

cmd_loop() {
    log "loop mode: cadence ${INTERVAL}s; one immediate pass, then sleep (catch-up collapses downtime)"
    while :; do
        # Dedup across restarts: a fresh container within the same interval
        # must not re-vacuum what a predecessor just finished.
        _age=$(stamp_age_seconds)
        if [ "$_age" != "255" ] && [ "$_age" -lt "$INTERVAL" ]; then
            log "last success was ${_age}s ago (< interval ${INTERVAL}s) — sleeping until due"
        else
            lock_acquire || {
                warn "lock held elsewhere; retrying in 60s (loop mode waits, never stacks)"
                sleep 60
                continue
            }
            run_pass_logged || warn "pass failed — will retry next cycle (container stays up; see log)"
            lock_release
        fi
        sleep "$INTERVAL"
    done
}

case "$CMD" in
    "$MODE_ONCE")   cmd_once ;;
    "$MODE_LOOP")   cmd_loop ;;
    "$MODE_STATUS") cmd_status ;;
esac
