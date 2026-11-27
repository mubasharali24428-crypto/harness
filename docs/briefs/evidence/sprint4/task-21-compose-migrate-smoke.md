# Task 21 — Compose up, migrate durable DB, stack smoke

- Date (UTC): 2026-11-24
- Status: **PASS** (closes S3 Task 12 BLOCKED, G-ENV-1 / G-ENV-2 / G-ENV-4)
- SHA at verification: `3b71212e` (started at pre-flight `d93ee869`; tasks 22/26/27 were committed
  while the image rebuilt, none of them change compose config or migration files).

## Docker recovery (Opus rule §1)

Pre-flight `docker info` failed (`/Users/user/.docker/run/docker.sock` missing). Mandated recovery:
`open -a Docker` → rc=0; `docker info` succeeded after **2 polls × 5 s** (Server 29.5.3, Docker Desktop).

## Master key

`.env` has three `HARNESS_MASTER_KEY=` lines: an empty placeholder (25), a valid 64-hex key (259)
and a 25-char placeholder (260) that wins in dotenv. A valid 64-hex key was **exported into the
shell** for every compose command (shell overrides `--env-file` for interpolation, verified via
`docker compose config`). No `.env` edit, no secret in any commit.

## First attempt failed honestly (stale image)

`docker compose ... up -d` brought up `db` but `migrate` exited 37:
`DRIFT GATE REFUSED BOOT: SCHEMA DRIFT: database has 73 applied migrations but this image declares only 70`.
The cached `harness-backend` image predates 0075. Correct fix: **rebuild the image from this checkout**,
not roll back the schema. `docker compose ... up -d --build` rebuilt `harness-backend:vdev-local`
and brought the full stack up.

## Verification (documented compose stack, no `down -v`)

```
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps
  db / sandbox-egress / identity / orchestrator / frontend / review / models /
  github / meeting / sandbox / siem-push / webhook-delivery / events-retention  — all Up
docker inspect harness-migrate-1 --format '{{.State.ExitCode}} {{.State.Status}}'   → 0 exited
docker logs harness-migrate-1 | tail -6
  drift gate: OK (applied migrations match this image's chain)
  applied 0076_project_guest_grants.sql
  applied 0077_approval_rendering.sql
  applied 0078_assignment_plans.sql
  applied 0079_run_adapter_leases.sql
  applied 0080_case_memory.sql
  applied 0081_scheduler_proposals.sql
docker exec harness-db-1 psql -U harness -d harness -tc 'select max(version) from schema_migrations;'
  → 0081
... tablename in (assignment_plan_versions, case_memory, scheduler_proposals)
  → all three present
curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/health?deep=0   → 200
curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/ready            → 200
curl -s -o /dev/null -w '%{http_code}' http://localhost:8004/health?deep=0   → 200
curl -s -o /dev/null -w '%{http_code}' http://localhost:8004/ready            → 200
curl -s -o /dev/null -w '%{http_code}' http://localhost:3010/                 → 200
```

Frontend is published on **:3010** by the local `.env` (`HARNESS_FRONTEND_PORT`), not the default
:3000 — relevant to Tasks 24/25/28.

`make dev-otp` correctly reported "no HARNESS-OTP codes in the logs yet — request a login code on
the UI first"; it is not an acceptance requirement here and no code was fabricated.

## Acceptance

PASS — migrate applied 0078–0081 on the **compose** DB at `:5499` with `max(version)=0081` and two
real 200 health responses from identity and orchestrator. This replaces the Sprint 3 Task 12
BLOCKED verdict. The two section4 `docker run` sandbox tests also pass now (see Task 30 gate table).
