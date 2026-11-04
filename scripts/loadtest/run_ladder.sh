#!/usr/bin/env bash
# W17 — repeatable dry-engine load ladder with a duplicate/lost terminal-transition audit.
#
#   scripts/loadtest/run_ladder.sh [vus ...]        default rungs: 4 8 16 32 64 100
#
# Pins a git revision (LADDER_REV, default HEAD): `git archive` into a scratch directory, so
# uncommitted work in this checkout is NOT in the measured build. Builds under its own image
# tag and compose project (harness-w17ladder-<rev7>), runs each rung on the isolated loadtest
# stack (ports 18001/18004, dry echo engine, ZERO provider spend), waits for the queue to drain,
# audits the event log (audit_terminals.sql), then removes ONLY its own project and volumes.
#
# Env: LADDER_REV, LADDER_HOLD (seconds per rung, 60), LADDER_OUT (evidence dir),
#      LADDER_KEEP=1 (leave the stack up), plus every PROBE_* knob of run_probe.sh.
# Closed-loop users (one team each): this is not an open-loop arrival test and says nothing
# about real-model capacity.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
REV_FULL="$(git -C "$SRC" rev-parse "${LADDER_REV:-HEAD}")"
REV="${REV_FULL:0:7}"
HOLD="${LADDER_HOLD:-60}"
OUT="${LADDER_OUT:-$SRC/docs/briefs/evidence/w17/ladder-$REV}"
RUNGS=("$@"); [ ${#RUNGS[@]} -gt 0 ] || RUNGS=(4 8 16 32 64 100)
export PROBE_PROJECT="harness-w17ladder-$REV"
export HARNESS_RUNS_VOLUME="${PROBE_PROJECT}_runs"   # compose pins harness_runs; `down -v` must not reach the live one
export HARNESS_IMAGE_TAG="w17-$REV"          # never collide with another stack's image tag
export PROBE_MASTER_KEY="${PROBE_MASTER_KEY:-dev-master-key-change-me-in-prod!}"

for port in 18001 18004; do
  if lsof -iTCP:"$port" -sTCP:LISTEN -nP >/dev/null 2>&1; then
    echo "[ladder] port $port is in use (another load test?); refusing to share it" >&2; exit 1
  fi
done
if [ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$PROBE_PROJECT")" ]; then
  echo "[ladder] project $PROBE_PROJECT already has containers; remove it first" >&2; exit 1
fi

SNAP="$(mktemp -d "${TMPDIR:-/tmp}/w17-ladder-$REV.XXXXXX")"
mkdir -p "$OUT"
git -C "$SRC" archive "$REV_FULL" | tar -x -C "$SNAP"
ln -s "$SRC/scripts/loadtest/.venv" "$SNAP/scripts/loadtest/.venv"   # locust lives in the checkout's venv
# The revision pins the PRODUCT code; the driver scripts come from this checkout (they may be
# newer than the pinned commit). Both are recorded in config.txt.
for f in run_probe.sh run_ladder.sh audit_terminals.sql summarize_ladder.py; do
  cp "$SRC/scripts/loadtest/$f" "$SNAP/scripts/loadtest/$f"
done

compose() {
  docker compose -f "$SNAP/deploy/docker-compose.yml" -p "$PROBE_PROJECT" --profile loadtest "$@"
}
psql_db() { compose exec -T db-loadtest psql -U harness -d harness -tAq "$@"; }
cleanup() {
  if [ "${LADDER_KEEP:-0}" = "1" ]; then echo "[ladder] LADDER_KEEP=1: project $PROBE_PROJECT left up"; return; fi
  compose down -v >/dev/null 2>&1 || true
  rm -rf "$SNAP"
}
trap cleanup EXIT

{
  echo "revision: $REV_FULL"
  echo "image: harness-backend:v$HARNESS_IMAGE_TAG   project: $PROBE_PROJECT"
  echo "host: $(sw_vers -productName 2>/dev/null || uname -s) $(sw_vers -productVersion 2>/dev/null || uname -r), docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
  echo "knobs: engine_max_concurrency=${PROBE_ENGINE_MAX_CONCURRENCY:-8} run_concurrency=${PROBE_RUN_CONCURRENCY:-4} poll=${PROBE_WORKER_POLL_SECONDS:-0.5} hold=${HOLD}s spawn_rate=${PROBE_SPAWN_RATE:-2}/s"
  echo "rungs: ${RUNGS[*]}"
  echo "started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} | tee "$OUT/config.txt"

# Build ONCE; rungs then run with --no-build so the image cannot change mid-ladder.
compose build orchestrator-loadtest identity-loadtest migrate-loadtest >"$OUT/build.log" 2>&1
export PROBE_NO_BUILD=1
IMAGE_ID="$(docker image inspect "harness-backend:v$HARNESS_IMAGE_TAG" --format '{{.Id}}')"
echo "image id: $IMAGE_ID" | tee -a "$OUT/config.txt"

for vus in "${RUNGS[@]}"; do
  label="ladder_${REV}_${vus}"
  echo "[ladder] ---- rung $vus users ----"
  # Samples the durable queue and container CPU/memory every 5 s while the rung runs.
  ( while sleep 5; do
      t="$(date -u +%H:%M:%S)"
      q="$(psql_db -c "select coalesce(string_agg(state||'='||n, ' '), 'empty') from (select state, count(*) n from run_claims group by state) s" 2>/dev/null || echo '?')"
      st="$(docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' \
            $(compose ps -q db-loadtest orchestrator-loadtest identity-loadtest 2>/dev/null) 2>/dev/null | tr '\n' ';')"
      echo "$t | claims: $q | $st"
    done >>"$OUT/samples_${vus}.log" ) & sampler=$!
  PROBE_SPAWN_RATE="${PROBE_SPAWN_RATE:-$(( vus > 20 ? 10 : 4 ))}" \
    "$SNAP/scripts/loadtest/run_probe.sh" run "$vus" "$HOLD" "$label" || echo "[ladder] locust exited non-zero for rung $vus"
  kill "$sampler" 2>/dev/null || true; wait "$sampler" 2>/dev/null || true
  # Service logs and proof the containers still run the image built above (not recreated).
  compose logs --no-color orchestrator-loadtest identity-loadtest >"$OUT/services_${vus}.log" 2>&1 || true
  for svc in orchestrator-loadtest identity-loadtest; do
    cid="$(compose ps -q "$svc" | head -1)"
    echo "$svc image=$(docker inspect "$cid" --format '{{.Image}}') started=$(docker inspect "$cid" --format '{{.State.StartedAt}}') restarts=$(docker inspect "$cid" --format '{{.RestartCount}}') oom=$(docker inspect "$cid" --format '{{.State.OOMKilled}}')" \
      >>"$OUT/containers_${vus}.txt"
  done
  # Drain: wait for the durable queue to empty (max 300 s), recording how long it took.
  drain_start=$(date +%s); drained=no
  for _ in $(seq 1 150); do
    n="$(psql_db -c 'select count(*) from run_claims' | tr -d '[:space:]')"
    [ "$n" = "0" ] && { drained=yes; break; }
    sleep 2
  done
  echo "{\"vus\": $vus, \"drained\": \"$drained\", \"drainSeconds\": $(( $(date +%s) - drain_start ))}" >"$OUT/drain_${vus}.json"
  psql_db -f - <"$SNAP/scripts/loadtest/audit_terminals.sql" >"$OUT/audit_${vus}.json"
  cp "$SNAP/scripts/loadtest/results/${label}.console.log" "$OUT/console_${vus}.log" 2>/dev/null || true
  cp "$SNAP/scripts/loadtest/results/${label}_stats.csv" "$OUT/stats_${vus}.csv" 2>/dev/null || true
  echo "[ladder] rung $vus audit: $(cat "$OUT/audit_${vus}.json")"
done
python3 "$SNAP/scripts/loadtest/summarize_ladder.py" "$OUT" "${RUNGS[@]}" | tee "$OUT/SUMMARY.md"
echo "finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$OUT/config.txt"
