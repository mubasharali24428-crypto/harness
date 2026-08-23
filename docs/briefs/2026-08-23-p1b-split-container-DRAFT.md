# TASK: Split-container deployment shape — same image, SERVICE=<name> selection (Phase 1)

You are working in the AI Engineering Harness monorepo at the current directory
(`/Users/user/untitled folder 2/harness`). Read `AGENTS.md` first. Scope: ONLY
`deploy/entrypoint.sh`, `deploy/runners/services.py`, and
`deploy/docker-compose.yml`. Do NOT touch any `sectionN/` code, `contracts/`,
CI files, or docs. Do NOT commit.

## Current state (verified today)

- `deploy/entrypoint.sh` (#!/bin/sh, MUST stay dash-compatible): runs a Python
  block that applies migrations (`migrate.py`) + seeds starter projects, copies
  demo workspace, then starts SEVEN uvicorn instances
  (`identity:8001 github:8002 models:8003 orchestrator:8004 sandbox:8005
  meeting:8006 review:8008`) plus a dash watchdog loop that exits if any dies.
- `deploy/docker-compose.yml`: one `backend` service builds
  `deploy/backend.Dockerfile`, publishes all seven ports, healthcheck hits
  :8001/health. `db` on 5499 host-side. `frontend` (Next.js) depends on backend
  healthy. Environment vars live in the backend service block.
- `deploy/runners/services.py` exposes factory functions `identity_app`,
  `github_app`, `models_app`, `orchestrator_app`, `sandbox_app`, `meeting_app`,
  `review_app` and a SERVICES dict; uvicorn uses `services:<name>_app --factory`.
- CI gate: `docker compose -f deploy/docker-compose.yml config -q` must keep
  passing (static-gates job).

## Goal

The platform must scale past ONE process (per EXPANSION_PLAN.md Phase 1):
same image, one service per container, selected by env var. All-in-one mode
stays available and remains the DEFAULT (dev convenience, zero behavior change
for existing users).

## What to do

### 1. entrypoint.sh: SERVICE selection + migrate-only mode

- New behavior, BEFORE the current flow:
  - If env `HARNESS_SERVICE` is set and non-empty:
    - Validate it against the known list; unknown value => print error, exit 1.
    - If it equals `migrate`: run the existing Python migrate+seed block, then
      exit 0 (one-shot migration container).
    - Otherwise: SKIP migrations/seed (the migrate container handles schema),
      skip the demo-workspace copy UNLESS service is `sandbox` or
      `orchestrator` (they need /tmp/harness-dsh), then exec (not background)
      exactly ONE uvicorn for that service on its port:
      `exec uvicorn "services:${name}_app" --factory --host 0.0.0.0 --port "$port"`.
      No watchdog loop needed — the container IS the process.
  - If `HARNESS_SERVICE` is empty/unset: behave EXACTLY as today (migrate+seed,
    all seven uvicorns, watchdog). Existing `docker compose up` dev flow and the
    backend.Dockerfile CMD must not change semantics.
- Keep it POSIX sh. No arrays, no `[[ ]]`, no local -a. `sh -n deploy/entrypoint.sh`
  must pass.

### 2. docker-compose.yml: one container per service

- Replace the single `backend` service with eight services built from the SAME
  build context/Dockerfile: `migrate` (profile-less one-shot,
  `HARNESS_SERVICE=migrate`, `restart: "no"`), and `identity`, `github`,
  `models`, `orchestrator`, `sandbox`, `meeting`, `review` — each with its
  single published port identical to today's host mapping (8001…8006, 8008;
  still bound without 127.0.0.1 prefix exactly as now).
- Every long-running service gets `HARNESS_SERVICE=<name>`, shares today's
  backend environment block verbatim (factor it out with YAML anchors `x-backend-env: &backend-env`
  to avoid duplication), gets a per-service healthcheck hitting ITS OWN
  `/health` on ITS OWN port (same python urllib one-liner pattern), and:
  ```yaml
  depends_on:
    migrate:
      condition: service_completed_successfully
    db:
      condition: service_healthy
  ```
- `frontend` keeps port 3000 and depends on `identity` healthy instead of
  `backend`. All other settings/restart policies preserved.
- The `migrate` service needs the db env vars too (it talks to Postgres).
- `docker compose -f deploy/docker-compose.yml config -q` MUST pass — this is
  the CI gate. Also sanity-check that anchors resolve (no duplicate-key errors).

### 3. services.py: no functional change expected

Read it first. If nothing must change (uvicorn already resolves `<name>_app`
factories), leave it untouched and say so. If something small is genuinely
required (e.g. an app name mapping), make the minimal edit.

## Tests / verification you must run

1. `sh -n deploy/entrypoint.sh`
2. `docker compose -f deploy/docker-compose.yml config -q`
3. `docker compose -f deploy/docker-compose.yml config` and confirm in the
   output: eight backend-side services exist, each has HARNESS_SERVICE set
   correctly, healthchecks reference the right ports, migrate is a one-shot.
4. Do NOT try to `docker compose up --build` (too heavy for your sandbox);
   report that live boot verification will be done by the reviewer.

## Deliverable

No commit. Report: files changed, outputs of the three checks above, any
deviation from this brief and why.
