# Deploy & Ops Brutal Audit — `untitled folder 2/harness`

Read-only audit of `deploy/`, CI, and the assembly runners. All findings verified against current file contents. Build on the five known failures rather than repeating them verbatim where deeper root causes exist.

---

## A. Configuration & secrets

### A1. Engine model/key config has *silent-fallback* semantics, not just "lost on recreate" — CRITICAL
- **Where:** `deploy/docker-compose.yml:14-16` (`DEEPSEek_API_KEY: ${DEEPSEEK_API_KEY:-ollama}`, `DEEPSEEK_BASE_URL: ...:-http://host.docker.internal:11434/v1`, `DSH_MODEL: ...:-qwen3.5-9b-128k:latest`); `section4/src/harness_orchestrator/engine.py:43-52`
- **Beyond the known issue:** `engine.live_mode` is `bool(api_key and model)` — the placeholder `"ollama"` counts as a key, so the engine reports **live** even with zero real config. On failure the ladder walks `catalog.local_fallback_candidates()` (catalog.py:80-95), i.e. silently downgrades to whatever free local models are priced 0.0 — violating AGENTS.md's own "No forced model downgrade" rule.
- **Incident:** prod host reboots, compose project recreated without the operator's shell-exported env → every agent run executes against a stale host Ollama (or empty responses), cost ledger bills nothing, no alarm fires because `/health` stays green.
- **Fix:** delete all three defaults (`${DEEPSEEK_API_KEY:?err}` style required vars); add a `HARNESS_REQUIRE_LIVE_ENGINE=1` fail-fast mirroring the master-key gate in `services.py:20-27`; log the resolved `engine._engine_name()` at startup and expose it on `/health`.

### A2. Compose neuters the master-key fail-fast — HIGH
- **Where:** `docker-compose.yml:10-11` — defaults `HARNESS_MASTER_KEY` to the public constant **and** `HARNESS_ALLOW_DEV_MASTER_KEY:-1`.
- **Impact:** `services.py:18-27`'s F9 fail-fast can never trigger under stock compose; GitHub tokens/OTP secrets encrypted at rest under a public constant (`KeyVault(MASTER_KEY)`, services.py:344). Restores onto another host with the same dev key decrypt everything.
- **Fix:** `${HARNESS_MASTER_KEY:?set a real key}` + drop the allow-flag default; keep dev convenience in a `compose.override.yml`.

### A3. Hardcoded DB superuser password everywhere — HIGH
- **Where:** `docker-compose.yml:9,43-45,279`; `services.py:14-16,143-146`; `ci.yml:87-89,110`.
- **Impact:** `harness/harness` superuser; db published on `127.0.0.1:5499` (compose:53-54) means any local process on the host is postgres superuser. Restore/PITR tooling inherits it.
- **Fix:** `${POSTGRES_PASSWORD:?}`, least-privilege role for services (no SUPERUSER), keep 5499 bound to loopback only in dev profile.

### A4. No env contract / no `.env.example` — MEDIUM
- **Where:** 30+ knobs scattered across compose anchors, `services.py` (HARNESS_RUN_CONCURRENCY :193, HARNESS_WORKER_POLL_SECONDS :200, HARNESS_NOTIFY_POLL_SECONDS :237, HARNESS_LOG_FORMAT :186), `engine.py` (DSH_FALLBACK_MODELS :53), `SandboxConfig` (`SANDBOX_*` prefix, config.py). Nothing documents which are required for prod. ONBOARDING.md covers behavior, not env.
- **Fix:** checked-in `.env.example` + one env table in `docs/`; a `config` dump command like `backup-walg.sh config` for the stack.

### A5. GitHub OAuth redirect URI pins localhost:8002 — MEDIUM
- **Where:** `docker-compose.yml:25` (`GITHUB_REDIRECT_URI:-http://localhost:8002/api/v1/github/oauth/callback`), default repeated in `services.py:74`.
- **Impact:** under any domain/TLS deployment the browser is sent to `localhost:8002` — OAuth breaks even once Caddy fronts the stack; also leaks topology.
- **Fix:** derive from `FRONTEND_BASE` + public route (`https://<domain>/api/v1/github/...`) and route the callback through the proxy.

## B. Frontend ↔ networking

### B1. `NEXT_PUBLIC_*` runtime env is a no-op; browser URLs are baked to localhost — CRITICAL
- **Where:** `docker-compose.yml:204-209` sets `NEXT_PUBLIC_*` as *runtime* env; `deploy/frontend.Dockerfile:3-8` builds with **no** build args; `section7/src/lib/api.ts:6-15,396` falls back to hardcoded `http://localhost:8001/8004/8006/8008`, `ws://localhost:8001`; `NEXT_PUBLIC_GITHUB_URL` (:396) and `NEXT_PUBLIC_ONBOARDING_BASE_URL` (`onboarding.ts:4`, port 8010 — **no such service exists**) aren't even set in compose.
- **Root cause sharpening of the known finding:** Next.js inlines `NEXT_PUBLIC_` at **build time**, so the compose `environment:` block does literally nothing; the shipped bundle always points browsers at host-loopback ports. Behind any reverse proxy/domain this breaks — and even in the default stack it forces CORS to matter at all.
- **Fix:** either (a) `ARG NEXT_PUBLIC_*` in frontend.Dockerfile + `args:` in compose, or better (b) collapse to one relative base (`/api/v1`) with the proxy doing path→service routing, deleting cross-origin entirely (kills most of the CORS surface, including the bug just patched in `services.py:32-45`). Remove the phantom 8010 default.

### B2. Proxy exists but is opt-in; raw ports are the default topology — HIGH
- **Where:** `docker-compose.yml:220-246` (`profiles: [proxy]`), ports `8001-8008`, `3000`, `5499` published unconditionally; `Caddyfile:12` is `harness.local` + internal CA (dev-only), no prod domain/ACME story.
- **Incident:** operator exposes the host, ports 8001-8008 are internet-reachable bypassing TLS; bearer tokens traverse plaintext.
- **Fix:** `docker-compose.prod.yml` where caddy is default, backends switch from `ports:` to `expose:`, Caddyfile takes domain from env with ACME; keep dev compose as-is.

## C. Data durability

### C1. All agent state lives in container-local `/tmp` — CRITICAL
- **Where:** `docker-compose.yml:17-18` (`DSH_WORKSPACE: /tmp/harness-dsh/workspace`, `DSH_SESSION_ROOT: /tmp/...`), `backend.Dockerfile:26-27`, `section5/src/harness_sandbox/config.py:10` (`storage_root=/tmp/harness_sandboxes`), re-copied demo repo at `entrypoint.sh:66-69,129-130`.
- **Sharpening of the known issue:** it's not only work products — DSH *session roots* (resume/continuity) and sandbox task trees (diffs produced for review) die on every `docker compose up` after a rebuild, every crash-loop, every image bump. `restart: unless-stopped` does not help across recreation. Also `cp -r demo_repo workspace || true` silently mutates/nests when the dir already exists.
- **Fix:** named volumes `dsh_workspace`, `dsh_sessions`, `sandbox_storage`; make the demo-repo seeding first-run-only; define retention/pruning for run artifacts.

### C2. Backup scheduling exists only as prose; walg sidecar defaults to a no-op — HIGH
- **Where:** cron lives only in `docs/runbooks/backup-and-pitr.md:36-60`; `docker-compose.yml:284` `WALG_MODE:-config` (prints config, backs up nothing); loop mode admits "no catch-up" (compose:256-258); **WAL archiving is not configured at all** — the db image lacks the wal-g binary (`Dockerfile.walg` header says a future wave must swap the db image), so PITR is currently impossible regardless of sidecar.
- **Evidence of manual-only operation:** three hand-run dumps sit in `deploy/backup/backups/` (2026-08-23).
- **Incident:** disk dies Tuesday → last snapshot whenever someone last remembered; stated RPO 24h is aspirational.
- **Fix:** ship a scheduled unit (host systemd timer installer, or crond in the walg sidecar with `WALG_MODE=base` default + required-prefix fail-fast); land the wal-g db image swap so `archive_command` actually archives.

### C3. Default walg backup mode reads a **live** pgdata without coordinated WAL — HIGH
- **Where:** `docker-compose.yml:288-293` mounts `pgdata:ro` into the sidecar; `backup-walg.sh:163-169` local-mode `backup-push "$WALG_PGDATA"` is the default path; streaming (`WALG_STREAM_BACKUP=1`) is opt-in; no `archive_command` exists (see C2).
- **Incident:** a "successful" base backup taken from a running cluster without archived WAL may be unrestorable exactly when you need it — the drill would catch it, but nothing runs drills on schedule either (runbook says "weekly", nothing enforces).
- **Fix:** default `WALG_STREAM_BACKUP=1` over libpq (the script already supports it), or block local mode until the db image carries wal-g; schedule `verify_restore.sh`.

### C4. Migration runner: solid core, sharp edges — MEDIUM
- **Where:** `runners/migrate.py`. Good: single transaction per run (:66), idempotent by version table, legacy bootstrap detection (:18-27). Weaknesses: (a) `split_statements(:30-61)` toggles on bare `$$` only — `$fn$`-tagged dollar quotes and `$$` inside string literals corrupt splitting; (b) statement-level execution inside one txn means a failure aborts all, fine, but there's no advisory lock — two operators running `migrate` concurrently (e.g. manual + scripted) race between SELECT and INSERT; (c) one-shot must be manually invoked on schema changes when operators use `restart` instead of `up -d` (known issue #5).
- **Fix:** `pg_advisory_xact_lock` at migration start; proper dollar-quote parser or `psql -f`-style single execute; wire `docker compose run --rm migrate` into a `make deploy` target so it can't be forgotten.

## D. Runtime, updates, graceful shutdown

### D1. No graceful drain of in-flight runs — HIGH
- **Where:** `services.py:206-212` — worker `stop()` cancels the poll task mid-flight; `worker.py:14-31` recovers orphans **only at next startup**; uvicorn default TERM kills in-flight HTTP/SSE/WS handlers; compose sets no `stop_grace_period`.
- **Incident:** every redeploy yanks terminal streams mid-run; `run_claims` rows sit `running` with dead heartbeats until the replacement boots; WS clients see silent drops instead of a resume pointer.
- **Fix:** on SIGTERM mark owned claims `orphaned`/release, finish or checkpoint active runs, add `stop_grace_period: 60s`; make heartbeat-age (column already exists, `0006_run_claims.sql:10`) the orphan criterion rather than startup-only recovery.

### D2. Scale-out is claimed by the schema, prevented by the compose — MEDIUM
- **Where:** `run_claims.worker_id` + state machine (`0006_run_claims.sql`) imply N workers; but every service pins a host port (`docker-compose.yml:81,99,117,...`) so `--scale orchestrator=2` fails on 8004; the orchestrator runs exactly one in-process worker (services.py:197-201).
- **Fix:** drop host port publishes (B2 fixes this anyway), then scale orchestrator; confirm claim atomicity under concurrency before advertising it.

### D3. No resource limits anywhere — MEDIUM
- **Where:** no `mem_limit`/`cpus`/`pids_limit` in compose; `SandboxConfig.max_memory_mb/max_cpus` (config.py:8-9) applies to subprocesses only, and `use_docker: False` default means "ephemeral containers" in ONBOARDING.md:3-7 are actually **processes inside the production backend container** (local_runner.py), with confinement that degrades to `off` with only a warning (local_runner.py:47-56).
- **Incident:** a runaway agent build OOMs the single host taking postgres down with it; a "sandboxed" command can read env of the backend process tree (DEEPSEEK key, master key material) if sanitize misses one var.
- **Fix:** per-service compose limits (sandbox lowest); turn `use_docker` on in deployed profile so the doc's threat model is true; fail closed (refuse to run) when requested confinement is unavailable.

### D4. Unbounded container logs — MEDIUM
- **Where:** no `logging:` blocks in compose; orchestrator emits JSON + request access logs (services.py:186,204) to the docker json-file driver with default no cap.
- **Incident:** chatty agent-event traffic fills the disk → postgres in the same host dies writing WAL.
- **Fix:** anchored `x-logging: &default {logging: {driver: json-file, options: {max-size: "50m", max-file: "3"}}}` applied to all services.

### E. Images & supply chain

### E1. Mutable base tags, no registry, no immutable artifacts, no rollback — HIGH
- **Where:** `python:3.13-slim` (backend.Dockerfile:1), `node:22-slim` ×2 (frontend.Dockerfile:1,10), `postgres:18-alpine`, `caddy:2-alpine`, bare `alpine` in restore scripts; pip installs unpinned section packages (backend.Dockerfile:19-21 — only the SDK is pinned per AGENTS.md); images are built locally per deploy, never tagged/pushed.
- **Incident:** an upstream `18-alpine` repush changes pgdata compat mid-incident during PITR; "rollback" is impossible because the previous image was overwritten/dangling.
- **Fix:** pin digests; CI builds and pushes `ghcr://…:git-sha`; compose references digests; retention keeps N prior images for rollback.

### E2. Security scanning is half-WARN and artifact-blind — MEDIUM
- **Where:** `ci.yml:140-144` pip-audit `continue-on-error: true` indefinitely ("this wave" with no exit criterion); gitleaks blocking (:145-148, good); **no SBOM**, **no image scanner** (trivy/grype), no Dependabot config (`.github/` contains only workflows/ci.yml).
- **Fix:** date-box the WARN posture; add trivy on built images + syft SBOM artifact; enable dependabot for npm + pip.

### E3. CI never builds the images — MEDIUM
- **Where:** `ci.yml:16-27` validates compose syntax only. A Dockerfile COPY typo (e.g. forgetting `section7` deps drift) reaches the first real deploy untested.
- **Fix:** `docker build` both images per PR (cache from registry), plus a boot-smoke (`HARNESS_SERVICE=identity` + curl /health).

### E4. Frontend image ships dev tree as root — LOW/MEDIUM
- **Where:** `frontend.Dockerfile:12` copies the **entire** build stage (node_modules incl. devDeps, tests, sources) into runtime; no USER, no HEALTHCHECK, `npm run start` (next start, not standalone).
- **Fix:** `output: standalone` in next.config.mjs, copy `.next/standalone` only, run as node, add HEALTHCHECK on :3000.

### E5. `.dockerignore` leaks junk into build context — LOW
- **Where:** `.dockerignore:1` `**/.venv` does **not** match `.venv_ci` (38 MB uploaded every build); `deploy/.seed_session.json` (**live admin bearer token**, verified gitignored but plaintext on disk) and `deploy/backup/backups/*.dump` ride in every context upload.
- **Fix:** add `.venv*`, `deploy/backup/backups`, `deploy/.seed_session.json`, `.git`; rotate that seeded token and move it out of the repo dir.

## F. Misc honesty gaps

- **F1 — `HARNESS_REQUIRE_OWNERSHIP:-false`** default in the prod-facing anchor (compose:13): ownership proofs off unless an operator discovers the knob. Flip default in prod file.
- **F2 — rate limiter fails open** by design (services.py:115): during a DB brownout, login brute-force protection silently vanishes; log loudly when open.
- **F3 — legacy all-in-one entrypoint path** (entrypoint.sh:132-147) traps no signals; children die hard on `docker stop`. Split mode is fine (`exec` :81). Either delete legacy mode or add a trap+wait supervisor.

**Positives worth keeping:** healthchecks with `start_period` on every backend; `service_completed_successfully` gating on migrate; backup integrity gate (`pg_restore --list`, backup.sh:90) and expectation-file restores; PITR script's exit-code mapping and never-touch-live-volume discipline; checksum-verified wal-g binary.

---
**Summary:** ~20 findings (target was ≥13): 3 critical (A1 engine fallback semantics, B1 build-time-baked localhost URLs, C1 /tmp state loss), 8 high, rest medium/low. No files created or modified — read-only analysis. Recurring theme: the system's real topology is "one mutable host, localhost everything," while its schemas, docs, and runbooks describe a production system that hasn't been assembled yet; the highest-leverage fixes are a `docker-compose.prod.yml` (proxy-default, no host ports, required env, resource/logging limits) plus named volumes for DSH state and a genuinely scheduled backup path.