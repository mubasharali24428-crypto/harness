#!/bin/sh
# restore-walg.sh — point-in-time recovery (PITR) of the harness database
# from WAL-G storage into a fresh volume.
#
# Mission 10 node B4 origin; corrected in M13-W3-H2 after the first REAL
# execution (2026-08-24, file:// drill). What actually broke in the M10
# version, and how it is fixed here:
#   * default PITR_IMAGE was stock postgres:18-alpine — has NO wal-g binary;
#   * postgres:18+ official ENTRYPOINT refuses data at .../data (it mandates
#     the /var/lib/postgresql major-version layout, PR #1259);
#   * a helper written into a THROWAWAY container never exists in the
#     recovery container (containers are rebuilt from the image each run).
# Fixes: PITR_IMAGE defaults to the wal-g-capable harness-db-walg image,
# everything happens under /pgdata, the server runs via `--entrypoint postgres`
# (init logic bypassed — the fetched cluster is already complete), and
# restore_command is SELF-CONTAINED (`wal-g wal-fetch "%f" "%p"` inline).
#
# What it does: `wal-g backup-fetch` a base backup, wire
# restore_command + recovery_target_time + recovery.signal, start a scratch
# Postgres that pauses at the target for human verification, and print the
# manual promote/cutover checklist.
#
# Upstream references:
#   * https://github.com/wal-g/wal-g/blob/master/docs/PostgreSQL.md
#     ("wal-fetch": exit 74 when a segment is absent ⇒ PostgreSQL may keep
#     searching; other failures must STOP recovery)
#   * https://www.postgresql.org/docs/current/continuous-archiving.html
#
# Usage:
#     restore-walg.sh --target-time '2026-08-24 04:00:22+00' [options]
#
# Options:
#     --target-time TS   replay until this timestamp (REQUIRED unless
#                        --fetch-only; stops at FIRST commit AT/after TS)
#     --fetch-only       stop after backup-fetch (no recovery wiring/start)
#
# Environment:
#     WALG_S3_PREFIX / WALG_FILE_PREFIX + AWS_*    storage target (required)
#     PITR_IMAGE            image WITH wal-g       (harness-db-walg:v3.0.9;
#                           build: docker build -f deploy/walg/Dockerfile.walg ...)
#     PITR_CONTAINER        scratch container name (harness-pitr)
#     PITR_VOLUME           scratch volume name    (harness_pgdata_pitr)
#     HBA_DIR               host dir mounted ro at /etc/harness IF the fetched
#                           cluster sets hba_file=/etc/harness/pg_hba.conf
#                           (compose `walg-db` does) (deploy/walg)
#
# DANGER: never touches the live `harness_pgdata` volume itself, but the
# printed cutover checklist DOES (destructively). Recovery pauses at the
# target — nothing is writable until YOU promote.

set -eu

TARGET_TIME=""
FETCH_ONLY=0

log() { printf '[walg-restore %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

usage() {
    sed -n '2,44p' "$0" | grep -E '^#' | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --target-time) [ $# -ge 2 ] || die "--target-time needs a value"; TARGET_TIME=$2; shift 2 ;;
        --fetch-only)  FETCH_ONLY=1; shift ;;
        -h | --help | help) usage; exit 0 ;;
        *) usage >&2; die "unknown argument: '$1'" ;;
    esac
done

command -v docker >/dev/null 2>&1 || die "docker CLI not found on PATH"

if [ -z "${WALG_S3_PREFIX:-}" ] && [ -z "${WALG_FILE_PREFIX:-}" ]; then
    die "no storage target: set WALG_S3_PREFIX or WALG_FILE_PREFIX"
fi

if [ "$FETCH_ONLY" -eq 0 ] && [ -z "$TARGET_TIME" ]; then
    die "refusing to guess the recovery moment: pass --target-time '<UTC timestamp>' (or --fetch-only)"
fi

PITR_IMAGE=${PITR_IMAGE:-harness-db-walg:${WALG_IMAGE_TAG:-v3.0.9}}
PITR_CONTAINER=${PITR_CONTAINER:-harness-pitr}
PITR_VOLUME=${PITR_VOLUME:-harness_pgdata_pitr}
HBA_DIR=${HBA_DIR:-"$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)"}

log "storage: ${WALG_S3_PREFIX:-}${WALG_FILE_PREFIX:-}"
log "inventorying base backups in storage (wal-g backup-list --detail)"

# --- 0. inventory -------------------------------------------------------------
docker run --rm \
    -e WALG_S3_PREFIX="${WALG_S3_PREFIX:-}" \
    -e WALG_FILE_PREFIX="${WALG_FILE_PREFIX:-}" \
    -e AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}" \
    -e AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}" \
    -e AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-}" \
    -e AWS_ENDPOINT="${AWS_ENDPOINT:-}" \
    -v "${WALG_FILE_PREFIX:-/nonexistent}:${WALG_FILE_PREFIX:-/nonexistent}" \
    "$PITR_IMAGE" wal-g backup-list --detail

# --- 1. fresh scratch volume ---------------------------------------------------
if docker volume inspect "$PITR_VOLUME" >/dev/null 2>&1; then
    die "volume '$PITR_VOLUME' already exists; remove it first (docker volume rm $PITR_VOLUME) or pick another"
fi
docker volume create "$PITR_VOLUME" >/dev/null
log "created empty volume $PITR_VOLUME"

# --- 2. backup-fetch into the volume -------------------------------------------
T0=$(date +%s)
docker run --rm \
    -v "$PITR_VOLUME:/pgdata" \
    -v "${WALG_FILE_PREFIX:-/nonexistent}:${WALG_FILE_PREFIX:-/nonexistent}" \
    -e WALG_S3_PREFIX="${WALG_S3_PREFIX:-}" \
    -e WALG_FILE_PREFIX="${WALG_FILE_PREFIX:-}" \
    -e AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}" \
    -e AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}" \
    -e AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-}" \
    -e AWS_ENDPOINT="${AWS_ENDPOINT:-}" \
    "$PITR_IMAGE" \
    sh -c 'wal-g backup-fetch /pgdata LATEST'
log "base backup fetched in $(($(date +%s) - T0))s  ← RTO EVIDENCE SLOT #1 (fetch)"
if [ "$FETCH_ONLY" -eq 1 ]; then
    log "--fetch-only set: stopping here. Inspect with:"
    log "  docker run --rm -v $PITR_VOLUME:/d alpine ls /d"
    exit 0
fi

# --- 3. wire recovery into the fetched cluster ----------------------------------
# restore_command is self-contained (no helper binaries): wal-g resolves %f/%p
# directly. Dedupe any previously-appended lines so re-runs stay idempotent.
docker run --rm -i \
    -v "$PITR_VOLUME:/pgdata" \
    -v "${WALG_FILE_PREFIX:-/nonexistent}:${WALG_FILE_PREFIX:-/nonexistent}" \
    -e WALG_S3_PREFIX="${WALG_S3_PREFIX:-}" \
    -e WALG_FILE_PREFIX="${WALG_FILE_PREFIX:-}" \
    -e AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}" \
    -e AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}" \
    -e AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-}" \
    -e AWS_ENDPOINT="${AWS_ENDPOINT:-}" \
    -e TARGET_TIME="$TARGET_TIME" \
    -e HBA_MOUNT_NEEDED="$(printf '%s' "${HBA_DIR:-}" | wc -c)" \
    "$PITR_IMAGE" \
    sh -s <<'EOF'
set -eu
mkdir -p /pgdata/pg_wal
sed -i '/^restore_command /d;/^recovery_target_time /d;/^recovery_target_action /d' /pgdata/postgresql.conf
{
    echo "restore_command = 'wal-g wal-fetch \"%f\" \"%p\"'"
    echo "recovery_target_time = '$TARGET_TIME'"
    echo "recovery_target_action = 'pause'"
} >> /pgdata/postgresql.conf
touch /pgdata/recovery.signal
chown -R 999:999 /pgdata
chmod 700 /pgdata
echo "wired:" >&2
grep -E '^(restore_command|recovery_target)' /pgdata/postgresql.conf >&2
EOF
log "recovery wiring written: target_time=$TARGET_TIME action=pause"

# --- 4. run the recovering server (pauses at target) ----------------------------
# --entrypoint postgres BYPASSES the 18+ init entrypoint (it would refuse the
# mount layout); uid 999 = debian-flavored postgres user. The deploy/walg dir
# rides along read-only in case the fetched config points hba_file there
# (compose `walg-db` clusters do).
docker rm -f "$PITR_CONTAINER" >/dev/null 2>&1 || true
T1=$(date +%s)
docker run -d --name "$PITR_CONTAINER" \
    -v "$PITR_VOLUME:/pgdata" \
    -v "${WALG_FILE_PREFIX:-/nonexistent}:${WALG_FILE_PREFIX:-/nonexistent}" \
    -v "$HBA_DIR:/etc/harness:ro" \
    -e WALG_S3_PREFIX="${WALG_S3_PREFIX:-}" \
    -e WALG_FILE_PREFIX="${WALG_FILE_PREFIX:-}" \
    -e AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}" \
    -e AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}" \
    -e AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-}" \
    -e AWS_ENDPOINT="${AWS_ENDPOINT:-}" \
    --user 999:999 \
    --entrypoint postgres \
    "$PITR_IMAGE" -D /pgdata >/dev/null

READY=no
i=0
while [ $i -lt 120 ]; do
    i=$((i + 1))
    STATE=$(docker inspect "$PITR_CONTAINER" --format '{{.State.Running}}' 2>/dev/null || echo missing)
    if [ "$STATE" != "true" ]; then
        echo >&2 "--- $PITR_CONTAINER exited during startup; last logs:"
        docker logs --tail 30 "$PITR_CONTAINER" >&2 || true
        die "recovery container failed to start (see logs above)"
    fi
    if docker exec "$PITR_CONTAINER" pg_isready -U harness -q 2>/dev/null; then READY=yes; break; fi
    sleep 1
done
[ "$READY" = "yes" ] || die "server did not become ready within 120s (check: docker logs $PITR_CONTAINER)"
REPLAY_S=$(($(date +%s) - T1))
log "server ready (paused at target) in ${REPLAY_S}s  ← RTO EVIDENCE SLOT #2 (replay-to-pause)"
docker logs "$PITR_CONTAINER" 2>&1 | grep -E "starting point-in-time|recovery stopping|consistent recovery state|restored log file" | tail -8 >&2 || true

# --- 5. what ONLY a human should decide ------------------------------------------
cat <<STEPS

=======================================================================
 PAUSED AT TARGET — VERIFY BEFORE PROMOTING (operator, in order)
=======================================================================
 1. Confirm the recovered moment:
      docker exec $PITR_CONTAINER psql -U harness -d harness \\
        -c "SELECT now(), pg_last_wal_replay_lsn(), pg_is_in_recovery();"
 2. Sanity-check business data vs expectations (counts of users / teams /
    projects / agent_events; your incident marker tables).
 3. Promote ONLY when satisfied (ends recovery, opens writes):
      docker exec $PITR_CONTAINER psql -U harness -c "SELECT pg_wal_replay_resume();"
    Wait for logs: redo done / selected new timeline ID / database system
    is ready to accept connections.
 4. Cutover (DESTRUCTIVE to the live stack — schedule it):
      cd <repo> && docker compose -f deploy/docker-compose.yml stop \\
        identity github models orchestrator sandbox meeting review frontend db
      docker run --rm -v harness_pgdata:/from -v "\$PWD":/to alpine \\
        tar czf /to/pgdata-broken-\$(date -u +%Y%m%dT%H%M%SZ).tgz -C /from .
      docker volume rm harness_pgdata && docker volume create harness_pgdata
      docker run --rm -v harness_pgdata:/to -v $PITR_VOLUME:/from alpine \\
        sh -c 'cp -a /from/. /to/'
      docker compose -f deploy/docker-compose.yml up -d
 5. Verify the stack (curl localhost:8001/health … 8008, UI login, counts),
    then take an immediate fresh base backup and record RPO/RTO in
    docs/runbooks/backup-and-pitr.md (drill evidence table).
 6. Tear down this drill:
      docker rm -f $PITR_CONTAINER && docker volume rm $PITR_VOLUME
=======================================================================
STEPS

log "done — $PITR_CONTAINER is UP and PAUSED at $TARGET_TIME"
