#!/bin/sh
# Split-container deployment: one image, selected by HARNESS_SERVICE.
# Empty/unset runs the legacy all-in-one stack; migrate is a one-shot job.
set -e
cd "$(dirname "$0")/runners"

if [ -n "${HARNESS_SERVICE:-}" ]; then
  case "$HARNESS_SERVICE" in
    migrate|identity|github|models|orchestrator|sandbox|meeting|review) ;;
    *)
      echo "unknown HARNESS_SERVICE: $HARNESS_SERVICE" >&2
      echo "expected: migrate identity github models orchestrator sandbox meeting review" >&2
      exit 1
      ;;
  esac

  if [ "$HARNESS_SERVICE" = migrate ]; then
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
