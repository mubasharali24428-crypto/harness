#!/usr/bin/env bash
# M18-C4 — throughput truth probe runner.
#
# Boots an ISOLATED load-test stack (own PG volume, ports 18001/18004), waits
# for health, then runs the locust journey profile at the requested VU level.
# The live compose stack keeps running untouched on 8001-8008.
#
# Usage:
#   scripts/loadtest/run_probe.sh smoke              # 1 journey, sanity
#   scripts/loadtest/run_probe.sh run <vus> <seconds> [label]
#   scripts/loadtest/run_probe.sh down               # tear down stack + volume
#
# Env:
#   PROBE_ENGINE_MAX_CONCURRENCY  default 8   (section4 dedicated executor)
#   PROBE_RUN_CONCURRENCY         default 4   (per-team queue cap)
#   PROBE_WORKER_POLL_SECONDS     default 0.5
#   PROBE_PROJECT                 default harness-loadtest (compose -p)
#
# M19-B1 (hit live): compose interpolates HARNESS_MASTER_KEY as
# ${PROBE_MASTER_KEY:-} = EMPTY STRING, and pydantic-settings applies that
# empty value OVER deploy/runners/services.py's module-level dev-key fallback,
# so KeyVault boots with b"" and identity crash-loops ("master_key must be
# non-empty"). Default here to the PUBLIC dev constant — legal because the
# isolated stack sets HARNESS_ALLOW_DEV_MASTER_KEY=1 (services.py F9 gate).
set -uo pipefail
export PROBE_MASTER_KEY="${PROBE_MASTER_KEY:-dev-master-key-change-me-in-prod!}"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Function, not a string var: the repo path contains spaces ("untitled folder 2")
# and word-splitting a command string mangles it.
compose() {
  docker compose -f "$REPO_ROOT/deploy/docker-compose.yml" \
    -p "${PROBE_PROJECT:-harness-loadtest}" --profile loadtest "$@"
}
IDENTITY=http://localhost:18001
ORCH=http://localhost:18004
RESULTS_DIR="$REPO_ROOT/scripts/loadtest/results"
mkdir -p "$RESULTS_DIR"

wait_healthy() {
  local url="$1" name="$2" tries="${3:-60}"
  for _ in $(seq 1 "$tries"); do
    if curl -sf --max-time 3 "$url" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo "[probe] FATAL: $name never became healthy at $url" >&2
  return 1
}

stack_up() {
  echo "[probe] booting isolated load-test stack (db+migrate+identity+orchestrator, dry engine)"
  compose up -d --build db-loadtest migrate-loadtest identity-loadtest orchestrator-loadtest
  wait_healthy "$IDENTITY/health" identity 90 || return 1
  wait_healthy "$ORCH/health" orchestrator 90 || return 1
  echo "[probe] stack healthy: identity=$IDENTITY orchestrator=$ORCH"
}

case "${1:-}" in
  smoke)
    stack_up
    exec python3 "$REPO_ROOT/scripts/loadtest/smoke_probe.py" \
      --identity "$IDENTITY" --orchestrator "$ORCH" --vu 1
    ;;
  run)
    VUS="${2:?usage: run_probe.sh run <vus> <seconds> [label]}"
    SECS="${3:?usage: run_probe.sh run <vus> <seconds> [label]}"
    LABEL="${4:-probe_${VUS}vu}"
    stack_up
    echo "[probe] locust: vus=$VUS seconds=$SECS label=$LABEL"
    cd "$REPO_ROOT/scripts/loadtest"
    # M19-B1 (hit live): without these exports the flow client falls back to
    # its localhost:8001 defaults and the sweep hits the LIVE compose stack
    # instead of the isolated one (its ownership gate + login limiter then
    # own the run: 158k RATE_LIMITED / OWNERSHIP_REQUIRED, zero samples).
    HARNESS_IDENTITY_URL="$IDENTITY" HARNESS_ORCHESTRATOR_URL="$ORCH" \
      ./.venv/bin/locust -f locustfile.py --headless \
      -u "$VUS" -r "${PROBE_SPAWN_RATE:-2}" --run-time "${SECS}s" \
      --csv "results/$LABEL" --only-summary --stop-timeout 180 \
      2>&1 | tee "results/$LABEL.console.log"
    ;;
  down)
    compose down -v
    echo "[probe] isolated stack + volume removed"
    ;;
  *)
    echo "usage: $0 {smoke|run <vus> <seconds> [label]|down}" >&2
    exit 2
    ;;
esac
