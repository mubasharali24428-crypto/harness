# M17 Phase A — S-GAMMA Systems/Ops Findings

> Seat: S-GAMMA (ELITE-FORGE, TEAM-1). Lens: compose topology, PG config,
> backup/PITR, CI blind spots, observability, deploy story.
> Baseline: `3c794b2` (Wave 6 CLOSED). Live probes executed 2026-08-25 ~03:00 UTC
> against the running stack (`docker exec harness-db-1/harness-walg-db-1 psql …`,
> `docker inspect`, `docker logs`, `crontab -l`) — every "live" claim below is a
> measured probe, not an inference. No fixes; no git writes.

**TL;DR — the 3am story:** the live primary has **no WAL archiving and no
backup schedule**, so the advertised RPO is fiction for the DB that actually
holds data; the live DB is also **14 migrations behind disk** while its
containers predate HEAD by ~40h (drift triad: code/schema/images); nothing
alerts on any of this because there are zero metrics/alerting/log-aggregation
wires; and CI has never once executed the real migration path it gates on.

---

## 1. Compose topology

### [CRITICAL] OPS-01 — PITR exists only on a sidecar DB; the live `db` archives nothing
- Evidence: `deploy/docker-compose.yml:53-94` — `db` runs stock `postgres:18-alpine`
  with no `archive_mode`; `deploy/docker-compose.yml:313-373` — archiving lives on
  opt-in `walg-db` (`--profile walg`), separate volume `pgdata_walg`.
  Runbook admits the design intent: `docs/runbooks/backup-and-pitr.md:113-119`
  ("separate archiving instance instead of flipping `db` itself").
- Live probes: `db`: `archive_mode = off`, `archive_command = (disabled)`,
  `pg_stat_archiver.last_archived_wal = NULL`. `walg-db`:
  `archived_count=42 failed_count=0`, last archive **326s before the probe** —
  healthy, but protecting only its own throwaway drill dataset (its only table:
  `pitr_marker`). The nightly base-backup cron targets `WALG_PGHOST=walg-db`
  by default (`docker-compose.yml:398`) — i.e. the documented daily backup
  backs up the *wrong cluster* unless an operator remembers to override.
- 3am translation: host/volume loss on `pgdata` ⇒ total loss of everything
  since the last manual logical dump. The repo's own honest-status table
  (`backup-and-pitr.md:350-370`) marks S3 round-trip and live-volume cutover
  UNEXECUTED, but doesn't flag that the default topology protects nothing real.

### [HIGH] OPS-02 — No scheduling exists on this host; all cadence claims rest on a crontab that isn't there
- Evidence: runbooks prescribe host cron — `backup-and-pitr.md:47-57`
  (`17 2 * * *` pg_dump, `03 3 * * *` walg), `maintenance.md:60-68`
  (weekly vacuum). Probe: `crontab -l` → **"no crontab for user"**. The
  M10 in-container loop was deliberately retired ("no catch-up, hides
  failures", `docker-compose.yml:376-377`) with cron named as the replacement.
- Consequence: as deployed, neither backup layer nor maintenance has ever run
  on a schedule here; the only dumps on disk are three manual ones from
  Aug 22 (`deploy/backup/backups/`, newest `20260823T041531Z.dump`). The
  RPO column at `backup-and-pitr.md:35-38` describes a machine state that
  does not exist. Also: launchd/cron mail is the *only* failure channel
  (OPS-08 compounds this).

### [HIGH] OPS-03 — Zero resource limits anywhere; one noisy service starves the box
- Evidence: `grep resources|mem_limit|cpus|pids_limit deploy/docker-compose.yml`
  → no hits; `docker inspect harness-db-1` → `mem=0 pids=<no value> cpu=0`.
- 3am translation: a runaway sandbox/orchestrator workload OOMs the Postgres
  container (same kernel), which is precisely the incident class
  `restart: unless-stopped` + healthchecks cannot save you from (healthy
  process, dead host memory). Sandbox section executes agent code by design;
  unbounded next to the database.

### [MEDIUM] OPS-04 — Unbounded json-file log drivers on every long-running container
- Evidence: `docker inspect harness-db-1 / harness-identity-1` →
  `LogConfig {"Type":"json-file","Config":{}}` — no `max-size`/`max-file`;
  compose sets none. Backends emit per-request access lines
  (`RequestContextMiddleware`, `services.py:207`).
- Consequence: slow-motion disk exhaustion on the Docker VM — the same disk
  holding `pgdata`. Classic weeks-later 3am outage that no wave ever tested.

### [MEDIUM] OPS-05 — `frontend` is the only long-running service with no healthcheck
- Evidence: `docker-compose.yml:234-250` (frontend block: no `healthcheck`);
  `docker inspect harness-frontend-1 .Config.Healthcheck` → `null`;
  `grep -c healthcheck docker-compose.yml` → 10 (all other services covered).
- Impact: `proxy`/humans can see a wedged Next.js as healthy; restart policy
  can't act on a hung-but-alive server. Minor today (dev TLS is opt-in),
  but it's the one gap in an otherwise complete healthcheck contract
  (`ops.md §3`).

### [MEDIUM] OPS-06 — Healthchecks are liveness-only; a DB-down backend still reports healthy
- Evidence: `/health` returns `{"ok": true}` without touching the DB
  (`services.py:97-99`). Only identity ships a deep variant —
  `section1/src/harness_identity/app.py:146-158`, `/health?deep=1` runs
  `SELECT 1` → verified live: `{"ok":true,"db":"ok"}` — but the compose
  healthcheck (`docker-compose.yml:118`) hits plain `/health`.
- 3am translation: db dies ⇒ seven backends keep answering `/health` 200 with
  a wall of 500s; orchestrator keeps accepting runs into a queue whose worker
  will fail them. No readiness signal exists for load balancers or humans to
  distinguish "app up" from "app functional".

### [MEDIUM] OPS-07 — Pool-math ledger omits the models service's second engine
- Evidence: `docs/pool-math.md:16,23-26` counts exactly 7 × (8+4) = 84 ≤ 120.
  But `deploy/runners/services.py:146-157` gives **models** an additional raw
  `asyncpg.create_pool(min_size=0, max_size=4)` cost-ledger pool (built from
  the same DSN after stripping `+asyncpg`).
- Worst case is really 84 + 4 = 88 ≤ 120, so no exhaustion today — but the
  contract doc/test claim exhaustiveness ("zero bypasses"), and headroom math
  silently shrinks for every future sidecar copied from this pattern. The
  grep-guard in `test_deploy_pool_math.py` only catches `create_async_engine`,
  not `asyncpg.create_pool`.

## 2. PG configuration (live-measured)

| Setting | Live value | Note |
|---|---|---|
| `max_connections` | 120 | matches `docker-compose.yml:82`; pool math OK |
| `shared_buffers` | 160 MB (`163848kB`) | image default (~25% of a small VM); never tuned |
| `effective_cache_size` | 5 GB | **larger than shared_buffers×31; aspirational** — planner trusts a cache the host may not have |
| `work_mem` / `maintenance_work_mem` | 4 MB / 64 MB | defaults; 0026's keyset queries fine now, sorts degrade at scale |
| `wal_level` | replica | fine |
| `autovacuum_*` | all defaults (naptime 60s, scale 0.2, 3 workers) | no per-table tuning despite maintenance.md documenting churn tables |
| `idle_in_transaction_session_timeout` / `statement_timeout` | 0 / 0 | a leaked idle-in-txn session holds vacuum horizon forever; nothing bounds it |
| `tcp_keepalives_idle` | 0 | half-open connections linger after network hiccups |
| `log_min_duration_statement` / `log_lock_waits` / `log_connections` | off / off / off | slow-query forensics impossible post-incident |

- [HIGH] OPS-08 — **Logging/observability settings are all image defaults.**
  Nothing in compose, docs, or migrations tunes any of the above. Combined with
  OPS-02/OPS-08 alerting absence: a stalled autovacuum, a bloating table, or a
  lock pileup is invisible until users report it.
- [MEDIUM] OPS-09 — `checkpoint_timeout=300s` + `max_wal_size=1GB` are defaults;
  fine for dev write volume, but the PITR story's RPO math assumes segments get
  archived promptly — on the live db they never leave the data dir at all (OPS-01).

## 3. Backup/PITR gaps

### [CRITICAL] OPS-10 — Logical backup covers the database only; roles/grants are unrestorable
- Evidence: `deploy/backup/backup.sh:81` — single `pg_dump --format=custom`;
  `grep -rn pg_dumpall|--globals-only` over `deploy/ section1/migrations/` →
  **zero hits**. Everything runs as superuser role `harness` (live probe:
  `harness super=true repl=true`, sole login role besides PG system roles), so
  today's blast radius is small — but the moment anyone follows AGENTS.md's
  least-privilege direction, restores silently drop those distinctions.
  Restore path (`restore.sh` → `pg_restore --exit-on-error`) recreates objects
  under whatever role executes it.

### [HIGH] OPS-11 — Master key has no escrow; backups restore into permanent secret loss
- Evidence: `backup-and-pitr.md:24-29` — snapshots hold app-layer-encrypted
  secrets decryptable only with **the producing environment's
  `HARNESS_MASTER_KEY`**; restoring elsewhere without the same key yields
  undecryptable `api_keys.key_encrypted` / GitHub tokens. No doc, runbook, or
  script addresses key persistence/escrow across host migration (ops.md §1
  sources keys from macOS launchd env — host-local by construction).
- 3am translation: successful DR exercise, then discover every stored API key
  is garbage. The runbook states the hazard but ships no procedure.

### [HIGH] OPS-12 — PITR restore drills never prove the restore *serves traffic* (auth, roles, extensions)
- Evidence: drill procedure `backup-and-pitr.md:179-240` verifies only
  `pitr_marker` rows + `pg_is_in_recovery()`; evidence log (:259-266) shows all
  four PASSes ran against the walg-db **drill cluster** (fresh init'd instance,
  no `schema_migrations`, only `pitr_marker` — probed live). Never exercised:
  restoring the REAL `harness` cluster (14 applied migrations, real roles/data)
  and booting backends against it. Also UNEXECUTED per the honest table
  (:366-370): real S3, cutover onto live `harness_pgdata`,
  `restore-walg.sh --fetch-only`, amd64 build.
- Related blind spot: `verify_base` (`runners/backup.sh:158-188`) proves a
  full landed in storage, but nothing ever validates a base backup is
  *fetchable/restorable* until an incident needs it to be.

### [MEDIUM] OPS-13 — Retention defaults keep effectively nothing
- Evidence: `WALG_RETENTION_KEEP=0` default (`docker-compose.yml:411`,
  `backup.sh:106`) means **unbounded growth** if someone enables archiving and
  forgets pruning; meanwhile the logical layer keeps 7 nightly dumps
  (`backup.sh:31`) ≈ 7-day window against a 24h RPO claim — one bad week of
  unnoticed cron failure (see OPS-02: no cron, no mail) erases the window.
- Also unencrypted-at-rest both layers (`backup-and-pitr.md:24`), acceptable
  for dev, flagged honestly, but combined with same-filesystem storage
  (`rsync` offsite is documentation, not deployment — :68-74) the anti-ransomware
  posture is nil.

### [MEDIUM] OPS-14 — Large objects: currently zero (probe: `pg_largeobject_metadata` count = 0), and `pg_dump` custom format would carry them anyway — but no test or check guards the invariant that no LO usage sneaks in via future sections. Low blast radius today; noting for completeness since the audit lens asked.

## 4. CI blind spots

### [CRITICAL] OPS-15 — The migration runner CI gates on has never been executed by CI
- Evidence: PR job matrix (`.github/workflows/ci.yml:48-76`) runs pytest on
  SQLite fixtures — `section1/tests/conftest.py:21-27` builds engines from
  `"sqlite+aiosqlite://"` + `Base.metadata.create_all`. The real-PG path runs
  only in `nightly-pg-probe` (`ci.yml:96-134`) — and even THAT uses
  `create_all` (`integration/test_checkpoint1.py:51,157`), not
  `deploy/runners/migrate.py`. Grep confirms: no test anywhere imports or
  invokes the migrate runner against Postgres.
- Consequences never exercised: dollar-quote splitting (`migrate.py:30-61` —
  hand-rolled parser, used by `0003_pg_notify.sql:5-19` and five DO-blocks in
  `0023_fk_deletion_story.sql:36-58`), statement-level execution order, the
  legacy-marking branch (`MARK_LEGACY_SQL`), and re-run idempotency. A
  migration that passes review but breaks the splitter lands green and fails
  at the next `docker compose up` — i.e., at 3am or during a demo.
- Bonus drift guard gap: nothing asserts `schema_migrations` contents match
  `migrations/*.sql` on any environment (see OPS-16 for why that matters).

### [HIGH] OPS-16 — Live-stack schema/image drift is structural and undetected
- Live probes: `db.schema_migrations` = **12 applied, max 0012**, while disk
  ships through **0026** (25 files; numbering skips none — 0014 was renumbered
  0017 per `0017_pr_id.sql:6-8`). Running images built **Aug 23 02:45 PDT** vs
  HEAD committed **Aug 24 19:34 PDT** (~41h stale). Section 8 already depends
  on a table the live DB lacks: `team_settings` (migration 0024) is read by
  `section8/src/harness_review/team_thresholds.py:42` — probed live:
  `ERROR: relation "team_settings" does not exist`. It degrades to defaults by
  design (service.py:359 "Missing config → defaults") so nobody notices, but
  any 0024+ feature (e.g. team reviewer thresholds, WS ticket fingerprinting
  0025) is silently absent from the running product. Next `up -d --build`
  will replay 13+ migrations mid-life with zero rehearsal.
- Root cause: no CI gate compares declared vs applied versions, and the
  nightly PG probe creates schema via ORM instead of the runner (OPS-15), so
  "migrations actually apply cleanly in sequence" is asserted nowhere.

### [HIGH] OPS-17 — Single PG version (18) everywhere; no minor-version pin, no matrix
- Evidence: `postgres:18-alpine` (`docker-compose.yml:54`), `postgres:18`
  (`Dockerfile.walg`), `postgres:18-alpine` CI service (`ci.yml:103`) — all
  floating tags; `pg_prove`-style cross-version checks absent. A silent 18.x
  bump changes planner/VACUUM behavior under the stack with no canary. The
  glibc-vs-musl split (alpine primary, debian walg — `backup-and-pitr.md:131-136`)
  also means the restore image is a *different libc/build* than the primary;
  only ever proven on Apple Silicon arm64 (:370).

### [MEDIUM] OPS-18 — No concurrent-deploy race coverage: two `migrate`s can interleave
- Evidence: `migrate.py:64-91` — version check + apply inside one transaction
  per file batch, but nothing prevents two migrate jobs (e.g. operator runs
  `up` twice across profile boundaries, or events-retention's
  `depends_on: migrate: service_completed_successfully` races a manual run)
  from double-applying: row insert happens in the same txn, so exact-duplicate
  is prevented, but concurrent DDL on different statements can deadlock or
  partially order differently. There's no advisory-lock guard
  (`pg_advisory_lock`), no test simulating two runners. Six waves added
  services depending on migrate completion; none tested migrate-vs-migrate.

### [MEDIUM] OPS-19 — pip-audit is `continue-on-error: true` (`ci.yml:156-161`)
- Self-described WARN-posture "this wave" — waves have a way of becoming
  permanent. Supply-chain findings can't block anything. (gitleaks does block.)

### [MEDIUM] OPS-20 — E2E never runs in CI
- `docs/ci-gates.md:34`: `make e2e` "(CI job joins via ATLAS when the suite
  stabilizes)" — Playwright against the composed stack is local-only, so
  frontend↔backend integration regressions ship green.

## 5. Observability

### [HIGH] OPS-21 — Metrics exist on ONE service of nine; no scrape, no storage, no alerting
- Evidence: `services.py:183-207` — JSON logging + `RequestContextMiddleware`
  metrics wired **only in `app_orchestrator`**. identity/github/models/
  sandbox/meeting/review have neither `configure_logging` nor the middleware
  (grep-verified). No Prometheus/Grafana/Alertmanager anywhere in compose
  (grep over deploy/: zero hits). `/metrics` output goes nowhere — nothing
  scrapes it.
- Net: queue depth, event-bus lag, rate-limit trips, auth failures, DB pool
  saturation — all unobservable. The only "monitoring" is `curl /health` by hand
  (ops.md §2.6). At 3am there is no signal that anything is wrong until a user
  says so; nothing pages anyone (charter law "a failed nightly wrapper must
  page someone", `backup.sh:30` — has no implementation path).

### [HIGH] OPS-22 — Log aggregation story is absent
- Evidence: 11 containers × json-file drivers (OPS-04), no Loki/vector/fluentd
  (grep zero hits), no shipped query procedure beyond `docker logs | grep`.
  Orchestrator's structured JSON logs can't be correlated against the other six
  services' default uvicorn text logs; request IDs (`X-Request-ID`) die at the
  emitting container. Cross-service incident triage is manual archaeology.

### [MEDIUM] OPS-23 — Postgres self-monitoring unread: `pg_stat_statements` not installed (probed: only plpgsql extension), autovacuum stats only visible by hand (maintenance.md §"Bloat indicators" is a human procedure, not a job), `failed_count` on archiver checked solely during manual drills. The maintenance wrapper logs bloat snapshots weekly-if-cronned (it isn't — OPS-02) into a volume nobody reads.

## 6. Deploy story

### [CRITICAL] OPS-24 — No zero-downtime path and no rollback procedure exist at all
- Evidence: grep `rollback|roll back` over `docs/runbooks/ deploy/ ONBOARDING.md`
  → **zero hits**; grep `zero-downtime|rolling|blue-green` over docs/+deploy →
  only unrelated clientela research notes. Deploy model = `docker compose up -d
  --build`: recreate-in-place, each backend single-instance (compose scale-out
  impossible: fixed host ports 8001-8008, `docker-compose.yml:115-232`),
  hard cutover with connection breakage; `migrate` runs before backends
  (`x-backend-depends`, :46-50) so new code + new schema go live together
  against old containers during the swap window.
- Rollback reality-check: images are retagged in place (`harness-identity`
  etc., no version tags — probed `docker images`), previous builds become
  dangling; rollback requires knowing the old image ID before `up --build`
  destroys it. Schema rollbacks don't exist (forward-only numbered migrations,
  no down paths in `section1/migrations/`), so a bad 0027 is unrecoverable
  except by PITR — which points at the wrong cluster by default (OPS-01) and
  has never restored the real schema (OPS-12).
- 3am translation: bad deploy ⇒ forward-fix only, while every backend
  restart-loops against a half-migrated schema. The charter asks what breaks
  at 3am: this is the longest outage generator in the repo.

### [HIGH] OPS-25 — Migrate/backends race window is real, not theoretical
- Evidence: `restart: unless-stopped` on all backends + `depends_on:
  migrate: service_completed_successfully` gates *initial start*, but a
  recreate cycle (`up -d --build`) restarts backends while `migrate` may be
  re-running (one-shot `restart: "no"` — a failed migrate leaves OLD backends
  running against NEW expectations only on the next manual up; conversely a
  succeeded migrate flips schema while pre-recreate old-code containers still
  serve requests). No lock, no drain, no readiness ordering beyond
  service_healthy. Combined with OPS-18 (no advisory lock) the window can
  corrupt sequencing on busy systems.

### [MEDIUM] OPS-26 — Entrypoint supervisor converts any child death into full-container exit (`entrypoint.sh:139-147`) — good for restart-policy honesty (documented ops.md §3) — but in the LEGACY all-in-one mode one crashed section kills all eight; split mode is fine. Legacy profile remains one flag away from production use.

---

## Severity roll-up

| Severity | Count | Items |
|---|---|---|
| CRITICAL | 4 | OPS-01 (PITR not on live db), OPS-15 (migrate runner never CI-tested), OPS-16 (live schema/image drift, 0024+ features silently absent), OPS-24 (no rollback/no ZD path) |
| HIGH | 9 | OPS-02, OPS-03, OPS-07, OPS-08, OPS-11, OPS-12, OPS-17, OPS-21, OPS-22, OPS-25 *(10 listed — OPS-07 counted MEDIUM by impact, see note)* |
| MEDIUM | 9 | OPS-04, OPS-05, OPS-06, OPS-07, OPS-09, OPS-13, OPS-14, OPS-18, OPS-19, OPS-20, OPS-23, OPS-26 |

*(OPS-07 appears once; final tally: 4 CRITICAL / 9 HIGH / 11 MEDIUM across 26 items.)*

## Cross-seat pointers

- OPS-15/OPS-16 overlap P-BETA's test-masking lens (SQLite-fixture suites
  asserting production behavior).
- OPS-11 (master key escrow) overlaps MAC2's secrets surface.
- OPS-03/OPS-09 resource/tuning absence feeds PBC1's 10x scale plan directly:
  current headroom math assumes dev-sized state (pool-math.md itself warns
  "a deployment that raises overflow past its headroom owns its own outage").

— end S-GAMMA Phase A findings —
