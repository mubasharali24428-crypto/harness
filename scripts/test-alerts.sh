#!/bin/sh
# Run the promtool unit tests for deploy/observability/alerts.yml inside the running prometheus
# container (promtool is not on the host). Needs: docker compose ... --profile observability up -d prometheus
set -e
cd "$(dirname "$0")/../deploy/observability"
C=$(docker ps --filter name=prometheus --format '{{.Names}}' | head -1)
[ -n "$C" ] || { echo "no running prometheus container" >&2; exit 1; }
D=/tmp/alert-tests.$$
docker exec "$C" mkdir -p "$D"
docker cp alerts.yml "$C:$D/alerts.yml"
docker cp alerts_test.yml "$C:$D/alerts_test.yml"
docker exec "$C" promtool check rules "$D/alerts.yml"
docker exec "$C" promtool test rules "$D/alerts_test.yml"
