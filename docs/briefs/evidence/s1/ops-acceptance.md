# Ops acceptance (part 28) - evidence, 2026-10-18

Scratch PG18.4 on 127.0.0.1:55432 (temp data dir under the session scratchpad), deleted at the end.
Python: `section1/.venv/bin/python` (3.13). Docker daemon was NOT running; nothing below used compose.

## 1. Health endpoints - PASS for DB / schema, GAP for worker / events
Code: `deploy/runners/services.py:119-176` (`_install_probes`: `/health` liveness, `/ready` readiness), compose healthchecks hit `/ready` (`deploy/docker-compose.yml:244-393`). Identity also has its own `/health` (`section1/src/harness_identity/app.py:165`), models/review have `/health?deep=1`.
Simulation (script mounts `_install_probes` on a FastAPI app, real asyncpg to the scratch PG):
```
healthy        /health 200 {'ok': True}   /ready 200 {'ok': True,'db':'ok','migrations':'ok'}
db unreachable /health 200 {'ok': True}   /ready 503 {'ok': False,'not_ready':['db-unreachable']}   (port 55499)
schema drift   /health 200 {'ok': True}   /ready 503 {'ok': False,'not_ready':['schema-drift']}     (ledger row '9999' inserted, removed after)
```
Existing tests: `section1/tests/test_deploy_resource_limits.py:189-230`.
No Redis exists anywhere in the stack (`rg -i redis` hits docs only). Queue = Postgres `run_claims`; worker = `RunQueueWorker` task inside the orchestrator process. `/ready` does not probe worker or event delivery (documented in ops.md 3.2); those are covered via metrics (item 3).

## 2. Log correlation - PARTIAL (fixed request id; run/action id not structured)
- Before: `JsonFormatter` (`section4/src/harness_orchestrator/logging_utils.py`) read `requestId` only from `record.request_id`, which only the access-log line sets; every other line inside a request had `requestId: null` despite the contextvar being set (`deploy/runners/observability.py:308`).
- Fix: fall back to `get_request_id()`; also emit `runId` / `actionId` when passed via `extra=`. Test: `section4/tests/unit/test_log_correlation.py` (2 tests, pass). Existing header tests: `section4/tests/unit/test_observability.py:88-131`, `section1/tests/test_deploy_observability.py`.
- Still open: no call site passes `extra={"run_id":...}`; run ids exist only in message text (`service.py:866,979,996`, `collab.py:194-906`); collab action ids live in `run_collab_events.request_id` (DB), not in logs. Documented in ops.md 3.4.

## 3. Alert rules - PARTIAL
- `deploy/observability/alerts.yml` does NOT exist; rules a-e are PromQL in `docs/runbooks/alerting.md` only (structurally tested by `section1/tests/test_deploy_observability.py`). `prometheus.yml` has no `rule_files`.
- Metric-name match (rg over code): emitted = `harness_queue_depth`, `harness_event_bus_lag_seconds`, `harness_db_pool_checked_out_connections`, `harness_runs_*_total` (`observability.py`). NOT emitted by any code: `harness_backup_last_success_timestamp_seconds`, `harness_pg_wal_archive_failed_count_total` (rules d/e inert; doc says so).
- Gap closed: no stalled-run signal (depth>50 cannot see one stuck run). Added gauges `harness_oldest_queued_run_age_seconds`, `harness_stale_running_claims` (`observability.py`, `_read_stall_gauges`), rule f in alerting.md, test `test_stall_gauges_flag_old_queued_and_stale_running`.

## 4. Concurrency limits - ENFORCED after fix; tier caps NOT enforced
Documented: `docs/engine-concurrency.md`, `PRICING.md:32`. Enforced: per-team via claim SQL `run_queue.py::_CLAIM_SQL`; executor via `engine_runtime.py` (default 8).
Race found on real PG (cap=4, 16 concurrent `claim_next_run`, three runs): running = 16, 13, 12. Cause: COUNT(*) guard under READ COMMITTED. Fix: `pg_advisory_xact_lock` before the claim UPDATE (`run_queue.py`). After: running = 4, 4, 4 (+ pytest `section4/tests/unit/test_claim_cap_live_pg.py`, opt-in `HARNESS_TEST_PG`, passes). Shipped topology (one orchestrator, sequential claims) was not exposed; multi-replica would have been.
Not enforced: Pro/Team concurrent-run tiers (`[M2]` in PRICING.md).

## 5. Migrations - PASS (forward); no down path by design
Applier: `deploy/runners/migrate.py` (`migrate()`, `check_drift`); `section1/scripts/bootstrap_db.py` only freezes 0001 from SQLAlchemy models (not an applier). `Makefile pg-probe-migrate` = `deploy/runners/migrate_selftest.py`.

| Range | Result |
|---|---|
| 0001-0035 from empty (34 files; 0014 absent, renumbered to 0017 per its header) | applied clean, 43 public tables; re-run applied 0 |
| `migrate_selftest.py` (scratch DB per run) | `PASS: 34 migrations (0001->0035) applied clean ... re-run idempotent` |
| failing migration (0099 with bad 2nd statement) | ProgrammingError; no `zz_ok` table, ledger unchanged at 33 (single transaction) |
| rollback 0035 -> 0034 | no `*down*` files exist. Rehearsed restore: dump at 0034, apply 0035, old-image `check_drift` -> `SchemaDriftError` naming 0035; dropdb + pg_restore -> ledger (33 rows, max 0034), `run_collab_events` absent, drift check PASS |

Rollback procedure = restore (rollback.md). Failures: none in the above.

## 6. Clean-environment start - PARTIAL (compose bring-up not executable)
Fresh rsync copy (no .git/.venv/node_modules) under scratchpad:
- `cp .env.example .env` + master key line: OK. `docker compose --env-file .env -f deploy/docker-compose.yml config -q`: OK (rc 0).
- `docker compose ... up -d`: NOT RUN, Docker daemon not running (`failed to connect to the docker API`). Unverified.
- Dev path for section1 (fresh venv, `pip install -e ../contracts`, `-e ".[dev]"`): OK; `make test-section1`: 652 passed, 10 skipped; `make lint-section1`: all checks passed. Full `make bootstrap` (8 venvs + npm ci) not run.
- Doc fixes (README): runbook list omitted rollback/alerting; added tool prerequisites; added forward-only migration note + `make pg-probe-migrate`. Link/path check over README and runbooks: only `deploy/observability/alerts.yml` missing (by design, now stated).

## 7. Incident/recovery runbooks - PASS with gaps filled
Exist: ops.md, rollback.md, degraded-mode.md, backup-and-pitr.md (+ alerting, maintenance, external-validation). rollback.md matches observed drift-gate behaviour. Gaps filled: ops.md 3.2-3.5 (what /ready covers, stalled runs, log correlation, concurrency status), degraded-mode.md 8 (platform dependency failures; sections 1-7 were provider-outage/mission process only), rollback.md rehearsal entry. NOT re-drilled: PITR and image rollback (need Docker).

## Test runs
section4 full: 696 passed, 31 skipped. section1 `test_deploy_observability.py` + `test_deploy_resource_limits.py`: 50 passed. Source files changed: `deploy/runners/observability.py`, `section4/src/harness_orchestrator/logging_utils.py`, `section4/src/harness_orchestrator/run_queue.py`; tests: `section1/tests/test_deploy_observability.py`, `section4/tests/unit/test_log_correlation.py`, `section4/tests/unit/test_claim_cap_live_pg.py`.
