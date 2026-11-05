#!/usr/bin/env bash
# Run one command with the live stack's DSH_MODEL switched, then restore it.
#
#   scripts/eval/with_model.sh anthropic/claude-haiku-5.5 python3 scripts/eval/run_eval.py --task w11-grounding --include-live
#
# Why: the deployment has one DSH_MODEL, but vision tasks need an image-capable model while the rest
# run on a cheaper text model (docs/decisions/ none; owner direction 2026-11-05: Haiku for vision,
# DeepSeek for the rest). Shell env beats --env-file in compose, so the root .env is never edited.
# Only orchestrator and worker-daemon read DSH_MODEL; both are recreated on the running images.
set -euo pipefail

MODEL="${1:?usage: with_model.sh <model> <command...>}"
shift
[ "$#" -gt 0 ] || { echo "with_model.sh: no command given" >&2; exit 2; }

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
# Each service keeps its OWN image tag: the backend services and the worker-daemon are rebuilt
# separately, so one shared tag would name an image that does not exist for the other (it aborted
# the switch half-applied and left the orchestrator stopped on 2026-11-05).
tag_of() { local image; image="$(docker inspect "harness-$1-1" --format '{{.Config.Image}}')"; echo "${image##*:v}"; }
TAG_ORCHESTRATOR="$(tag_of orchestrator)"
TAG_WORKER_DAEMON="$(tag_of worker-daemon)"
PREVIOUS="$(docker exec harness-orchestrator-1 printenv DSH_MODEL)"

apply() {
  (cd "$REPO/deploy" &&
    DSH_MODEL="$1" HARNESS_IMAGE_TAG="$TAG_ORCHESTRATOR" docker compose --env-file ../.env \
      -f docker-compose.yml up -d --no-build --no-deps --wait orchestrator >/dev/null &&
    DSH_MODEL="$1" HARNESS_IMAGE_TAG="$TAG_WORKER_DAEMON" docker compose --env-file ../.env \
      -f docker-compose.yml up -d --no-build --no-deps --wait worker-daemon >/dev/null)
}

restore() { echo "[with_model] restoring DSH_MODEL=$PREVIOUS" >&2; apply "$PREVIOUS"; }
trap restore EXIT

if [ "$MODEL" != "$PREVIOUS" ]; then
  echo "[with_model] DSH_MODEL $PREVIOUS -> $MODEL" >&2
  apply "$MODEL"
fi
DSH_MODEL="$MODEL" "$@"
