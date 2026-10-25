#!/usr/bin/env bash
# OIDC SSO drill — real IdP (Keycloak) against the running dev stack.
#
#   ./scripts/oidc_drill.sh              # run, write evidence, stop the IdP
#   KEEP_IDP=1 ./scripts/oidc_drill.sh   # leave Keycloak running afterwards
#
# Needs: the compose stack up (identity healthy on 8001) and Docker able to
# pull quay.io/keycloak/keycloak:26.1. The drill talks to the identity service
# through docker exec, so the IdP hostname (sso-idp) only has to resolve on
# the compose network.
set -euo pipefail

cd "$(dirname "$0")/.."

# Match how the dev stack is started on this host (root .env carries the
# master key and dev flags). Without it compose would fall back to deploy/.env
# and the stack would refuse to boot.
ENV_ARGS=()
[ -f .env ] && ENV_ARGS=(--env-file .env)
COMPOSE=(docker compose "${ENV_ARGS[@]}" -f deploy/docker-compose.yml)
IDENTITY_CONTAINER=${IDENTITY_CONTAINER:-harness-identity-1}
OUT=${OIDC_DRILL_OUT:-docs/briefs/evidence/pilot-prep/oidc-keycloak-drill.json}
ISSUER=${DRILL_ISSUER:-http://sso-idp:8080/realms/harness}

cleanup() {
    if [ "${KEEP_IDP:-0}" != "1" ]; then
        "${COMPOSE[@]}" --profile sso-drill stop sso-idp >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

echo "[oidc-drill] starting Keycloak (profile sso-drill)" >&2
"${COMPOSE[@]}" --profile sso-drill up -d --wait --wait-timeout 180 sso-idp >/dev/null

echo "[oidc-drill] waiting for ${ISSUER}" >&2
ready=0
for _ in $(seq 1 90); do
    if docker exec "$IDENTITY_CONTAINER" python - "$ISSUER" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1] + "/.well-known/openid-configuration", timeout=5)
PY
    then
        ready=1
        break
    fi
    sleep 2
done
if [ "$ready" != "1" ]; then
    echo "[oidc-drill] IdP did not become ready; last logs:" >&2
    "${COMPOSE[@]}" --profile sso-drill logs --tail 40 sso-idp >&2 || true
    exit 1
fi

mkdir -p "$(dirname "$OUT")"
echo "[oidc-drill] driving the flow" >&2
run_drill() {
    docker exec -i "$IDENTITY_CONTAINER" python - < scripts/oidc_drill.py
}
# One retry: Keycloak's very first boot can still be settling right after the
# health probe passes, and a refused socket is not a flow failure.
if ! run_drill | tee "$OUT"; then
    echo "[oidc-drill] first attempt failed; waiting 10s and retrying once" >&2
    sleep 10
    run_drill | tee "$OUT"
fi

python3 - "$OUT" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
if data.get("result") != "pass":
    raise SystemExit("[oidc-drill] FAILED: " + json.dumps(data.get("checks"), indent=2))
print(f"[oidc-drill] PASS — {len(data['checks'])} checks, evidence in {sys.argv[1]}")
PY
