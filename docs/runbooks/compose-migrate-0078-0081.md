# Runbook — apply migrations 0078–0081 on the deployed compose Postgres

For operators. Sprints 1–3 landed migrations `0078_assignment_plans`, `0079_run_adapter_leases`,
`0080_case_memory`, `0081_scheduler_proposals` in git. A scratch-DB `migrate_selftest` PASS proves the
chain applies, but it **drops its scratch database** — it is not the same as migrating the deployed
compose DB on `:5499`. Use this runbook for that step.

## 0. Preconditions (read before running)

- Docker daemon up, compose stack reachable; `nc -z 127.0.0.1 5499` succeeds.
- A backend image built from a commit that **contains** `section1/migrations/0081_scheduler_proposals.sql`.
  An older cached image refuses boot with `DRIFT GATE REFUSED BOOT ... this image declares only 70`
  (or similar). That is an image-age problem — rebuild, **do not roll back the schema**.
- `HARNESS_MASTER_KEY` set to the same 64-hex key the app tier uses. `migrate` refuses an empty key.
  Do not commit it. Rotating it invalidates previously encrypted secrets (see `.env.example`).

## 1. Bring up / rebuild the stack

```sh
cd /path/to/harness
export HARNESS_MASTER_KEY="$(openssl rand -hex 32)"   # or reuse the deployed key; never commit it
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps --format 'table {{.Service}}\t{{.Status}}\t{{.Image}}'
```

Do **not** run `docker compose down -v` (that deletes the database volume).

## 2. Apply the forward-only migration chain

```sh
cd /path/to/harness
docker compose --env-file .env -f deploy/docker-compose.yml run --rm migrate
```

Expect exit 0 and `applied 0078_...` … `applied 0081_...`. If the ledger is already at 0081 the
one-shot is a no-op. A second run must print no newly applied files.

## 3. Verify on the compose DB (not a scratch DB)

```sh
docker exec harness-db-1 psql -U harness -d harness -tc \
  'select max(version) from schema_migrations;'
# expect: 0081

docker exec harness-db-1 psql -U harness -d harness -c \
  "select tablename from pg_tables where schemaname='public' and tablename in
   ('assignment_plan_versions','case_memory','scheduler_proposals') order by 1;"
# expect: all three rows

HARNESS_TEST_PG=1 make pg-probe-migrate   # optional: full 0001→0081 scratch re-apply + idempotency
```

Health edges after migration:

```sh
for p in 8001 8004; do printf "%s " "$p"; curl -fsS -o /dev/null -w '%{http_code}\n' "http://localhost:$p/health"; done
# expect two 200s
```

## 4. Rollback posture

Migrations are **forward-only**. Never delete or edit `schema_migrations` rows and never edit an
already-landed migration file. A drift refusal means the image is older than the schema: rebuild a
newer image (§1). A genuinely bad migration requires the documented backup/restore procedure in
`docs/runbooks/rollback.md` and `docs/runbooks/backup-and-pitr.md` — take a backup before running
`migrate` on a database that matters.

## 5. Evidence

- `docs/briefs/evidence/sprint3/task-11-migrations.md` — scratch-DB PASS on PG 18.4 (0001→0081).
- `docs/briefs/evidence/sprint4/task-21-compose-migrate-smoke.md` — compose DB result at this SHA.
- `docs/briefs/evidence/sprint4/task-23-pg-probe.md` — compose `pg-probe` trio result.
