#!/bin/sh
# restore-walg.sh — point-in-time recovery (PITR) of the harness database
# from WAL-G storage into a fresh volume.
#
# Mission 10 node B4 companion to ../backup/restore.sh. Where restore.sh
# replays a logical pg_dump snapshot, this performs PHYSICAL recovery:
# `wal-g backup-fetch` a base backup, then let PostgreSQL replay archived WAL
# up to --target-time via restore_command + recovery.signal.
#
# Upstream references (wiring below follows these faithfully):
#   * https://github.com/wal-g/wal-g/blob/master/docs/PostgreSQL.md
#     - "backup-fetch": `wal-g backup-fetch <dest-dir> LATEST|<name>`
#     - "wal-fetch": intended for restore_command; exits 74 when a segment is
#       absent so PostgreSQL may keep searching, other errors must stop it —
#       the wrapper below maps exit 1 to 75 so recovery halts loudly instead
#       of silently skipping a corrupt/missing segment.
#   * https://www.postgresql.org/docs/current/runtime-config-wal.html
#   * https://www.postgresql.org/docs/current/continuous-archiving.html
#     (recovery.signal + restore_command + recovery_target_time semantics)
#
# Usage:
#     restore-walg.sh --target-time '2026-08-22 14:30:00+00' [options]
#
# Options:
#     --target-time TS   replay until this timestamp (default: latest consistent)
#     --volume NAME      named docker volume to fetch INTO (default harness_pgdata_pitr;
#                        NEVER the live harness_pgdata)
#     --fetch-only       stop after backup-fetch; skip container/recovery steps
#
# Environment (defaults fit deploy/docker-compose.yml):
#     WALG_S3_PREFIX / WALG_FILE_PREFIX + AWS_*   as backup-walg.sh (required)
#     PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE  libpq env for wal-g (defaults db/5432/harness)
#     PITR_IMAGE            image used for the scratch recovery container
#                           (default postgres:18-alpine — matches the compose db)
#     PITR_CONTAINER        scratch container name        (harness-pitr)
#     PITR_VOLUME           default target volume         (harness_pgdata_pitr)
#     PITR_NETWORK          compose network to attach     (auto-detected from the
#                           running stack; override if detached)
#
# DANGER: this script never writes the live `harness_pgdata` volume itself,
# but the procedure ends with a manual cutover that DOES. Read every printed
# step before executing it. Recovery stops at the FIRST consistent state at or
# after --target-time; timestamps are compared in the timezone given.

set -eu

TARGET_TIME=""
VOLUME_ARG=""
FETCH_ONLY=0

log() { printf '[walg-restore %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

usage() {
    sed -n '2,45p' "$0" | grep -E '^#' | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --target-time) [ $# -ge 2 ] || die "--target-time needs a value"; TARGET_TIME=$2; shift 2 ;;
        --volume)      [ $# -ge 2 ] || die "--volume needs a value"; VOLUME_ARG=$2; shift 2 ;;
        --fetch-only)  FETCH_ONLY=1; shift ;;
        -h | --help | help) usage; exit 0 ;;
        *) usage >&2; die "unknown argument: '$1'" ;;
    esac
done

command -v wal-g >/dev/null 2>&1 ||
    die "wal-g binary not found on PATH (host installs: https://github.com/wal-g/wal-g/releases)"
command -v docker >/dev/null 2>&1 || die "docker CLI not found on PATH"

if [ -z "${WALG_S3_PREFIX:-}" ] && [ -z "${WALG_FILE_PREFIX:-}" ]; then
    die "no storage target: set WALG_S3_PREFIX or WALG_FILE_PREFIX"
fi

if [ "$FETCH_ONLY" -eq 0 ] && [ -z "$TARGET_TIME" ]; then
    die "refusing to guess the recovery moment: pass --target-time '<UTC timestamp>' (or --fetch-only)"
fi

PITR_IMAGE=${PITR_IMAGE:-postgres:18-alpine}
PITR_CONTAINER=${PITR_CONTAINER:-harness-pitr}
PITR_VOLUME=${VOLUME_ARG:-${PITR_VOLUME:-harness_pgdata_pitr}}

export PGHOST=${PGHOST:-db}
export PGPORT=${PGPORT:-5432}
export PGUSER=${PGUSER:-harness}
export PGDATABASE=${PGDATABASE:-harness}

log "storage: ${WALG_S3_PREFIX:-}${WALG_FILE_PREFIX:-}"
log "inventorying base backups in storage (wal-g backup-list --detail)"
wal-g backup-list --detail

# --- 1. fresh target volume --------------------------------------------------
if docker volume inspect "$PITR_VOLUME" >/dev/null 2>&1; then
    die "volume '$PITR_VOLUME' already exists; remove it first (docker volume rm $PITR_VOLUME) or pick another with --volume"
fi
docker volume create "$PITR_VOLUME" >/dev/null
log "created empty volume $PITR_VOLUME"

# --- 2. backup-fetch into the volume ----------------------------------------
docker run --rm \
    -v "$PITR_VOLUME:/var/lib/postgresql/data" \
    -e WALG_S3_PREFIX="${WALG_S3_PREFIX:-}" \
    -e WALG_FILE_PREFIX="${WALG_FILE_PREFIX:-}" \
    -e AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}" \
    -e AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}" \
    -e AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-}" \
    -e AWS_ENDPOINT="${AWS_ENDPOINT:-}" \
    -v "${AWS_WEB_IDENTITY_TOKEN_FILE:-/dev/null}:/dev/null" \
    "${PITR_IMAGE}" \
    sh -c 'apk add --no-cache curl >/dev/null 2>&1 || true; \
           test -x /usr/local/bin/wal-g || { echo "this image lacks wal-g; build deploy/walg/Dockerfile.walg per the runbook and pass PITR_IMAGE=<it>" >&2; exit 127; }; \
           exec wal-g backup-fetch /var/lib/postgresql/data LATEST'
log "base backup fetched into $PITR_VOLUME"

if [ "$FETCH_ONLY" -eq 1 ]; then
    log "--fetch-only set: stopping here. Inspect with:"
    log "  docker run --rm -v $PITR_VOLUME:/d alpine ls /d"
    exit 0
fi

# --- 3. wire recovery into the fetched data directory ------------------------
# restore_command runs INSIDE the recovering container; it needs wal-g plus the
# storage env. walg-restore-command maps wal-g's exit codes onto the contract
# documented upstream (74 = absent ⇒ keep looking; anything else ⇒ stop).
docker run --rm -i \
    -v "$PITR_VOLUME:/var/lib/postgresql/data" \
    "${PITR_IMAGE}" \
    sh -s <<EOF
set -eu
mkdir -p /var/lib/postgresql/data/pg_wal
cat > /usr/local/bin/walg-restore-command <<'INNER'
#!/bin/sh
# %f = segment name, %p = destination path (see restore_command docs).
wal-g wal-fetch "\$1" "\$2"
rc=\$?
[ \$rc -eq 0 ] && exit 0
[ \$rc -eq 74 ] && exit \$rc
echo "wal-fetch failed rc=\$rc for \$1 — mapping to 75 so PostgreSQL STOPS instead of skipping" >&2
exit 75
INNER
chmod +x /usr/local/bin/walg-restore-command
# NOTE: the restore_command VALUE must stay single-quoted here — an unquoted
# path makes postgres fail config parsing outright ("syntax error ... near
# token \"/\"" -> FATAL), verified live against postgres:18-alpine.
printf 'restore_command = %s\n' "'/usr/local/bin/walg-restore-command %f %p'" >> /var/lib/postgresql/data/postgresql.conf
printf 'recovery_target_time = %s\n' "'$TARGET_TIME'" >> /var/lib/postgresql/data/postgresql.conf
printf 'recovery_target_action = pause\n' >> /var/lib/postgresql/data/postgresql.conf
touch /var/lib/postgresql/data/recovery.signal
chown -R 70:70 /var/lib/postgresql/data || true
echo "recovery wiring written: restore_command + recovery_target_time='$TARGET_TIME' + recovery.signal"
EOF

# --- 4. run the recovery container -------------------------------------------
NET_ARGS=""
STACK_NET=$(docker ps --format '{{.Names}}' | grep -E '^harness-db-[0-9]+$' | head -1 |
    xargs -r docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')
if [ -n "$STACK_NET" ]; then
    NET_ARGS="--network $STACK_NET -e PGHOST=db"
else
    NET_ARGS="-e PGHOST=host.docker.internal --add-host=host.docker.internal:host-gateway"
    log "stack not running: attaching to default bridge; archive lookups will use host networking fallback"
fi
# shellcheck disable=SC2086  # NET_ARGS is intentionally word-split
docker run -d --name "$PITR_CONTAINER" \
    $NET_ARGS \
    -e PGUSER -e PGPASSWORD -e PGDATABASE \
    -e WALG_S3_PREFIX="${WALG_S3_PREFIX:-}" \
    -e WALG_FILE_PREFIX="${WALG_FILE_PREFIX:-}" \
    -e AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}" \
    -e AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}" \
    -e AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-}" \
    -e AWS_ENDPOINT="${AWS_ENDPOINT:-}" \
    -v "$PITR_VOLUME:/var/lib/postgresql/data" \
    "${PITR_IMAGE}"
sleep 3
docker logs --tail 50 "$PITR_CONTAINER" >&2 || true
log "recovery container '$PITR_CONTAINER' started — follow with: docker logs -f $PITR_CONTAINER"

# --- 5. what ONLY a human should decide ---------------------------------------
cat <<'STEPS'

=======================================================================
 MANUAL VERIFICATION STEPS (operator — do these yourself, in order)
=======================================================================
 1. Watch recovery replay:
      docker logs -f harness-pitr
    PostgreSQL pauses at recovery_target_time (recovery_target_action=pause).
 2. Confirm the recovered moment from inside the paused server:
      docker exec harness-pitr psql -U harness -d harness \
        -c "SELECT now(), pg_last_wal_replay_lsn(), pg_is_in_recovery();"
 3. Sanity-check business data BEFORE promoting (counts vs expectations):
      users / teams / projects / agent_events — compare against the healthy
      source or your expectations file (see deploy/backup/restore.sh header).
 4. Promote ONLY when satisfied (ends recovery, opens writes):
      docker exec harness-pitr psql -U harness -c "SELECT pg_wal_replay_resume();"
    Wait for: LOG: redo done / database system is ready to accept connections.
 5. Cutover (DESTRUCTIVE to the live stack — schedule it):
      docker compose -f deploy/docker-compose.yml stop identity github models orchestrator sandbox meeting review frontend
      docker compose -f deploy/docker-compose.yml stop db
      # protect evidence first! snapshot the broken volume:
      docker run --rm -v harness_pgdata:/from -v "$PWD":/to alpine tar czf /to/pgdata-broken-$(date -u +%Y%m%dT%H%M%SZ).tgz -C /from .
      docker volume rm harness_pgdata
      docker volume create harness_pgdata
      docker run --rm -v harness_pgdata:/to -v harness_pgdata_pitr:/from alpine sh -c 'cp -a /from/. /to/'
      docker compose -f deploy/docker-compose.yml up -d
 6. Verify the stack: curl localhost:8001/health … 8008, UI login, row counts.
 7. Post-incident: take an immediate fresh base backup
      ./deploy/walg/backup-walg.sh base
    and note the recovered-vs-lost window in the incident report.
=======================================================================
STEPS

log "done"
