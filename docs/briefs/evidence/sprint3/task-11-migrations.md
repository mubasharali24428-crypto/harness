# Task 11 — Apply migrations 0078–0081 on real Postgres + selftest

- Date (UTC): 2026-11-24
- SHA at test: `03a03a40f7433a3fb4ee5058278eeeea24758e97` (HEAD after Sprint 3 pre-flight)
- Status: **PASS**

## Environment

- Docker daemon is **down** (`docker info` cannot reach `/Users/user/.docker/run/docker.sock`; port 5499 closed), so compose `:5499` was unavailable.
- Used the Makefile's documented caller-supplied Postgres path instead: `HARNESS_TEST_PG_EXTERNAL=1` (see `Makefile:338` `pg-up`), with `PG_URL` pointed at the locally running Homebrew PostgreSQL.
- PG version: **PostgreSQL 18.4 (Homebrew)** on aarch64-apple-darwin25.4.0, at `127.0.0.1:5432`. Connected as OS superuser `user` (rolsuper/rolcreatedb = t).
- Only a per-run scratch DB (`harness_migrate_probe_<hex>`) was created and dropped; no live data touched. Post-run check: no `harness_migrate_probe_%` databases remain.

## Migrations on disk (confirmed present)

`section1/migrations/0078_assignment_plans.sql`, `0079_run_adapter_leases.sql`,
`0080_case_memory.sql`, `0081_scheduler_proposals.sql` — all four exist and are
included in the 79-file chain applied below.

## Command

```sh
cd "/Users/user/untitled folder 2/harness"
HARNESS_TEST_PG_EXTERNAL=1 \
PG_URL="postgresql+asyncpg://user@127.0.0.1:5432/postgres" \
make pg-probe-migrate
```

(`make pg-probe-migrate` → `deploy/runners/migrate_selftest.py`, the same runner CI uses.)

## Output (trimmed)

```
pg-up: using the caller-supplied Postgres (HARNESS_TEST_PG_EXTERNAL=1)
HARNESS_TEST_PG=1 HARNESS_DATABASE_URL="postgresql+asyncpg://user@127.0.0.1:5432/postgres" ... ./section1/.venv/bin/python deploy/runners/migrate_selftest.py
failed to connect to the docker API at unix:///Users/user/.docker/run/docker.sock ...
migrate_selftest PASS: 79 migrations (0001→0081) applied clean on real PG; re-run idempotent.
=== rc=0 ===
```

The `docker API` line is harmless: it comes from the Makefile expanding the unused
`HARNESS_TEST_PG_CONTAINER` variable in `PG_ENV` (`docker compose ps -q db`);
`migrate_selftest.py` does not read that variable. Exit code was 0.

## Acceptance

- migrate selftest **PASS** at recorded SHA `03a03a40` — 79 migrations 0001→0081
  applied clean on real PG, second pass a no-op (idempotent). Verified Docker-compose
  route unavailable; documented external-PG route used and passed.
