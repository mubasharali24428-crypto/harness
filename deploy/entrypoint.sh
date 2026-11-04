#!/bin/sh
# Split-container deployment: one image, selected by HARNESS_SERVICE.
# Empty/unset runs the legacy all-in-one stack; migrate is a one-shot job.
set -e
cd "$(dirname "$0")/runners"

# A compose `command:` belongs to the service that set it. Before this, the
# argument list was never read, so `sandbox-egress` (command: python -m
# harness_sandbox.egress_proxy) silently ignored its own command, fell through
# to the all-in-one branch and tried to boot seven uvicorns — plus the migrate
# job — inside a 128MB, 0.25-CPU sidecar. It crash-looped on the master-key
# gate instead of ever proxying anything. Checked BEFORE HARNESS_SERVICE so a
# command-carrying sidecar needs no HARNESS_SERVICE at all.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

if [ -n "${HARNESS_SERVICE:-}" ]; then
  case "$HARNESS_SERVICE" in
    migrate|identity|github|models|orchestrator|sandbox|meeting|review|webhook-delivery|siem-push) ;;
    *)
      echo "unknown HARNESS_SERVICE: $HARNESS_SERVICE" >&2
      echo "expected: migrate identity github models orchestrator sandbox meeting review webhook-delivery siem-push" >&2
      exit 1
      ;;
  esac

  if [ "$HARNESS_SERVICE" = migrate ]; then
# M18-C6 (OPS-24/OPS-16): deploy-time SCHEMA-DRIFT GATE. Before this image's
# code touches the ledger, applied migrations must be a subset of the
# migrations shipped in THIS image. applied > declared = rolled-forward
# database meeting an older image (bad rollback, or two image generations
# sharing one DB): REFUSE with an operator-facing message naming the versions
# and which image tag matches them. Pending migrations (declared > applied)
# are normal and pass straight through.
python - <<'PYGATE'
import asyncio
import sys

sys.path.insert(0, "/opt/harness/deploy/runners")

from migrate import check_drift_with_retry as check_drift
from services import get_factory


async def main():
    factory = get_factory()
    engine = factory.kw["bind"]
    try:
        await check_drift(engine)
        print("drift gate: OK (applied migrations match this image's chain)")
    finally:
        await engine.dispose()


try:
    asyncio.run(main())
except Exception as exc:
    print(f"DRIFT GATE REFUSED BOOT: {exc}", file=sys.stderr)
    sys.exit(37)
PYGATE
    if [ $? -eq 37 ]; then
      echo "migrate: schema drift — refusing to apply anything or start the app tier." >&2
      echo "Run the decision tree in docs/runbooks/rollback.md §3." >&2
      exit 1
    fi

python - <<'PYBOOT'
import asyncio
import sys

sys.path.insert(0, "/opt/harness/deploy/runners")

from migrate import migrate
from services import get_factory


async def main():
    factory = get_factory()
    engine = factory.kw["bind"]
    try:
        for name in await migrate(engine):
            print(f"applied {name}")
        await seed_projects()
    finally:
        await engine.dispose()


async def seed_projects():
    """Every team gets a starter project if it has none (fresh-install UX)."""
    from sqlalchemy import text

    factory = get_factory()
    async with factory() as db:
        await db.execute(
            text(
                """
                INSERT INTO projects (id, team_id, name)
                SELECT gen_random_uuid()::text, t.id, 'demo-repo'
                FROM teams t
                WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.team_id = t.id)
                """
            )
        )
        await db.commit()
        eng = factory.kw["bind"]
        await eng.dispose()


asyncio.run(main())
PYBOOT
    exit 0
  fi

  # M18-C6: app-tier pre-flight — the SAME drift gate as the migrate branch,
  # but read-only (no migrations are applied here). If the DB is ahead of this
  # image's chain (bad rollback / two image generations on one DB), every
  # backend refuses to boot instead of serving against a schema it does not
  # understand. Exit code 37 = drift; anything else is a normal boot failure.
  python - <<'PYGATE' || { [ $? -eq 37 ] && exit 1 || exit $?; }
import asyncio
import sys

sys.path.insert(0, "/opt/harness/deploy/runners")

from migrate import check_drift_with_retry as check_drift
from services import get_factory


async def main():
    factory = get_factory()
    engine = factory.kw["bind"]
    try:
        await check_drift(engine)
        print("drift gate: OK")
    finally:
        await engine.dispose()


try:
    asyncio.run(main())
except Exception as exc:
    print(f"DRIFT GATE REFUSED BOOT: {exc}", file=sys.stderr)
    sys.exit(37)
PYGATE

  if [ "$HARNESS_SERVICE" = webhook-delivery ]; then
    exec python -m harness_identity.webhook_worker
  fi

  # W16 slice 4: the SIEM push loop. Separate service so a stalled customer
  # collector cannot slow outbound webhooks, and so `pendingRows` growth has an
  # obvious process to look at.
  if [ "$HARNESS_SERVICE" = siem-push ]; then
    exec python -m harness_identity.siem_worker
  fi

  case "$HARNESS_SERVICE" in
    sandbox|orchestrator)
      mkdir -p /tmp/harness-dsh
      cp -r /opt/harness/demo_repo /tmp/harness-dsh/workspace 2>/dev/null || true
      ;;
  esac

  case "$HARNESS_SERVICE" in
    identity) port=8001 ;;
    github) port=8002 ;;
    models) port=8003 ;;
    orchestrator) port=8004 ;;
    sandbox) port=8005 ;;
    meeting) port=8006 ;;
    review) port=8008 ;;
  esac
  exec uvicorn "services:${HARNESS_SERVICE}_app" --factory --host 0.0.0.0 --port "$port"
fi

python - <<'PYBOOT'
import asyncio
import sys

sys.path.insert(0, "/opt/harness/deploy/runners")

from migrate import migrate
from services import get_factory


async def main():
    factory = get_factory()
    engine = factory.kw["bind"]
    try:
        for name in await migrate(engine):
            print(f"applied {name}")
        await seed_projects()
    finally:
        await engine.dispose()


async def seed_projects():
    """Every team gets a starter project if it has none (fresh-install UX)."""
    from sqlalchemy import text

    factory = get_factory()
    async with factory() as db:
        await db.execute(
            text(
                """
                INSERT INTO projects (id, team_id, name)
                SELECT gen_random_uuid()::text, t.id, 'demo-repo'
                FROM teams t
                WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.team_id = t.id)
                """
            )
        )
        await db.commit()
        eng = factory.kw["bind"]
        await eng.dispose()


asyncio.run(main())
PYBOOT

mkdir -p /tmp/harness-dsh
cp -r /opt/harness/demo_repo /tmp/harness-dsh/workspace 2>/dev/null || true

for svc in identity:8001 github:8002 models:8003 orchestrator:8004 sandbox:8005 meeting:8006 review:8008; do
  name="${svc%%:*}"
  port="${svc##*:}"
  uvicorn "services:${name}_app" --factory --host 0.0.0.0 --port "$port" &
done

# dash-compatible supervisor: if any service dies, stop the stack
while true; do
  sleep 5
  for pid in $(jobs -p); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "service $pid died; stopping stack" >&2
      exit 1
    fi
  done
done
