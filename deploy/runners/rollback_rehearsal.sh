#!/bin/bash
# Part 59 (session 12): rehearse docs/runbooks/rollback.md section 4 (image + schema rollback)
# end to end on a THROWAWAY stack, never the live one.
#
#   deploy/runners/rollback_rehearsal.sh [N-1 git ref, default origin/master]
#
# N   = this checkout, N-1 = the given ref (built from a temporary git worktree).
# Uses deploy/docker-compose.rehearsal.yml (project c6rehearsal: no host ports, own volume, own
# network) with services db + migrate + identity only. Steps, each checked:
#   1. deploy N-1, seed a marker row, take a logical backup (deploy/backup/backup.sh)
#   2. upgrade to N (new migrations apply), write a post-upgrade marker
#   3. image-only rollback to N-1 must be REFUSED by the drift gate (schema ahead of the image)
#   4. image + schema rollback: restore the backup (deploy/backup/restore.sh), start N-1:
#      migrate passes, identity is ready, the pre-upgrade marker is back, the post-upgrade one gone
#   5. roll forward to N again: migrate passes, identity is ready
# Exit 0 only when every check holds. Tears the stack, worktree and scratch down on exit.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REF="${1:-origin/master}"
SCRATCH="$(mktemp -d)"
N1_TREE="$SCRATCH/n1-tree"
export HARNESS_ALLOW_DEV_MASTER_KEY=1
export HARNESS_MASTER_KEY="rehearsal-only-key-$(date +%s)-not-a-secret"
export HARNESS_REQUIRE_OWNERSHIP=false
export WALG_FILE_PREFIX="$SCRATCH/walg"   # never the live WAL archive
export HARNESS_RUNS_VOLUME="c6rehearsal_runs"   # compose pins harness_runs; `down -v` must not reach the live one
mkdir -p "$WALG_FILE_PREFIX"

compose() {
  docker compose -f "$ROOT/deploy/docker-compose.yml" -f "$ROOT/deploy/docker-compose.rehearsal.yml" \
    -p c6rehearsal "$@"
}
sql() { docker exec c6rehearsal-db-1 psql -U harness -d harness -Atc "$1"; }
ready() {
  for _ in $(seq 1 60); do
    if docker exec c6rehearsal-identity-1 python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/ready', timeout=3)" \
      >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}
migrate_exit() { docker inspect c6rehearsal-migrate-1 --format '{{.State.ExitCode}}'; }
check() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; exit 1; fi; }
cleanup() {
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  git -C "$ROOT" worktree remove --force "$N1_TREE" >/dev/null 2>&1 || true
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

echo "N-1 = $REF ($(git -C "$ROOT" rev-parse --short "$REF")), N = $(git -C "$ROOT" rev-parse --short HEAD)"
git -C "$ROOT" worktree add --detach "$N1_TREE" "$REF" >/dev/null
docker build -q -f "$N1_TREE/deploy/backend.Dockerfile" -t harness-backend:vrb-n1 "$N1_TREE" >/dev/null
docker build -q -f "$ROOT/deploy/backend.Dockerfile" -t harness-backend:vrb-n "$ROOT" >/dev/null
N1_HEAD=$(ls "$N1_TREE/section1/migrations" | sort | tail -1 | cut -d_ -f1)
N_HEAD=$(ls "$ROOT/section1/migrations" | sort | tail -1 | cut -d_ -f1)
echo "migration heads: N-1 $N1_HEAD, N $N_HEAD"

echo "--- 1. deploy N-1, seed, back up"
HARNESS_IMAGE_TAG=rb-n1 compose up -d --no-build db migrate identity >/dev/null 2>&1 || true
check "N-1 migrate exits 0" '[ "$(migrate_exit)" = 0 ]'
check "N-1 identity ready" ready
check "ledger at N-1 head" '[ "$(sql "SELECT max(version) FROM schema_migrations" | cut -d_ -f1)" = "$N1_HEAD" ]'
sql "INSERT INTO teams (id, name) VALUES ('rb-pre', 'before the upgrade')" >/dev/null
CONTAINER=c6rehearsal-db-1 BACKUP_DIR="$SCRATCH/backups" sh "$ROOT/deploy/backup/backup.sh" >/dev/null
DUMP=$(ls "$SCRATCH/backups"/*.dump | tail -1)
check "backup written" '[ -s "$DUMP" ]'

echo "--- 2. upgrade to N"
HARNESS_IMAGE_TAG=rb-n compose up -d --no-build --force-recreate migrate identity >/dev/null 2>&1 || true
check "N migrate exits 0" '[ "$(migrate_exit)" = 0 ]'
check "N identity ready" ready
check "ledger at N head" '[ "$(sql "SELECT max(version) FROM schema_migrations" | cut -d_ -f1)" = "$N_HEAD" ]'
sql "INSERT INTO teams (id, name) VALUES ('rb-post', 'after the upgrade')" >/dev/null

echo "--- 3. image-only rollback must be refused"
if [ "$N1_HEAD" != "$N_HEAD" ]; then
  set +e
  GATE=$(HARNESS_IMAGE_TAG=rb-n1 compose run --rm --no-deps migrate 2>&1)
  RC=$?
  set -e
  check "N-1 migrate refuses the newer schema (exit $RC)" '[ "$RC" != 0 ]'
  check "refusal names the drift gate" 'printf "%s" "$GATE" | grep -qi "drift"'
else
  echo "SKIP  no new migrations between N-1 and N: image-only rollback is allowed (rollback.md section 2)"
fi

echo "--- 4. image + schema rollback: restore, then N-1"
compose stop identity >/dev/null 2>&1 || true
CONTAINER=c6rehearsal-db-1 RESTORE_FORCE=1 sh "$ROOT/deploy/backup/restore.sh" "$DUMP" >/dev/null
HARNESS_IMAGE_TAG=rb-n1 compose up -d --no-build --force-recreate migrate identity >/dev/null 2>&1 || true
check "N-1 migrate exits 0 on the restored schema" '[ "$(migrate_exit)" = 0 ]'
check "N-1 identity ready after rollback" ready
check "ledger back at N-1 head" '[ "$(sql "SELECT max(version) FROM schema_migrations" | cut -d_ -f1)" = "$N1_HEAD" ]'
check "pre-upgrade row restored" '[ "$(sql "SELECT count(*) FROM teams WHERE id = '"'"'rb-pre'"'"'")" = 1 ]'
check "post-upgrade row gone (the documented data-loss window)" \
  '[ "$(sql "SELECT count(*) FROM teams WHERE id = '"'"'rb-post'"'"'")" = 0 ]'

echo "--- 5. roll forward to N again"
HARNESS_IMAGE_TAG=rb-n compose up -d --no-build --force-recreate migrate identity >/dev/null 2>&1 || true
check "N migrate exits 0 again" '[ "$(migrate_exit)" = 0 ]'
check "N identity ready again" ready
check "ledger at N head again" '[ "$(sql "SELECT max(version) FROM schema_migrations" | cut -d_ -f1)" = "$N_HEAD" ]'
echo "REHEARSAL PASSED"
