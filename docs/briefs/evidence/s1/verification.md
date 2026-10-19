# S1 Lane A verification (run 2026-10-18, fresh)

Logs: `docs/briefs/evidence/s1/logs/`. Tests were run with each section's own `.venv` (Makefile recipe), `-p no:cacheprovider`. Backend sections 1,2,3,4,5,6,8,10,11 were run concurrently (so timings are under load).

| Command | cwd | Result | Duration | Notes |
|---|---|---|---|---|
| `python -m pytest tests/ -q` | section1 | 651 passed, 10 skipped | 46s | skips are PG-gated |
| same | section2 | 39 passed | 2s | |
| same | section3 | 95 passed | 2s | |
| same | section4 | run 1: 693 passed, 30 skipped, **1 failed**; reruns: **1 failed**, 695 passed, 31 skipped; later: 696 passed, 31 skipped | 25s / 22s | FLAKY, 2 different tests, see Failures |
| same | section5 | 182 passed, 1 skipped | 7s | |
| same | section6 | 118 passed | 4s | |
| same | section8 | 88 passed | 3s | |
| same | section10 | 69 passed | 1s | |
| same | section11 | 49 passed, 1 skipped | 2s | not in Makefile SECTIONS; has its own venv |
| `make lint-section{1,2,3,4,5,6,8,10}` (ruff) | root | all 8 "All checks passed!" | <1s each | section11 has no lint target |
| `make guard-route-law` | root | routes OK (17 modules), isolation OK (171 src files) | <1s | |
| static-gates parts: `sh -n deploy/entrypoint.sh`; `docker compose config -q`; `contracts/ts/generate.py --check`; `scripts/spec_drift.py` | root | all exit 0 | ~1s each | compose config run when daemon was down; config-only |
| `make frontend-tsc` | root (section7) | **FAILED**, 1 error TS2344 | 2s | see Failures |
| `make frontend-vitest` | root (section7) | 59 files / 526 tests passed | 3s | |
| `pytest tests` (scratch venv, mcp<2) | mcp | 18 passed | 3s | no Makefile target / no venv; used scratch venv outside repo |
| same | packages/telemetry | 31 passed | 4s | scratch venv |
| same | packages/extensions | 90 passed | 15s | needs integrations/connectors/wiki installed (first attempt: collection error ModuleNotFoundError harness_wiki_connector, dependency not declared) |
| same | packages/ops | 77 passed | 9s | needs connectors/workflows installed |
| same | integrations/connectors/workflows | 35 passed | 6s | |
| same | integrations/connectors/wiki | 8 passed | <1s | |
| same | clients/python | 27 passed | <1s | |
| `npm test`, `npm run typecheck` | clients/ts | 33 passed, 0 failed; tsc clean | 1s | |
| `make pg-probe-migrate` (scratch PG18 :55433) | root | PASS: 34 migrations (0001->0035) applied clean, idempotent | 1s | |
| `make pg-probe-integration` (scratch PG :55433, schema empty) | root | 3 passed, **1 failed** | 1s | `relation "teams" does not exist`: probe expects a pre-migrated DB (compose migrate one-shot) |
| `make pg-probe-integration` after running `deploy/runners/migrate.py` on scratch DB | root | 4 passed | 1s | |
| `make pg-probe-s1` (scratch PG :55433, `PG_URL` override) | root | 655 passed, **4 failed, 3 errors** | 44s | see Failures: hardcoded to `docker exec harness-db-1` / localhost:5499, not env-driven |
| same, with PATH shim mapping `docker exec/inspect harness-db-1 psql` to scratch PG :55433, `HARNESS_TEST_PG` = explicit DSN | section1 | **662 passed, 0 failed, 0 skipped** | 55s | shim is in scratchpad, not in repo; proves live-PG tests pass when the container is substituted |
| P0 container isolation: docs/briefs/p0-container-isolation.md (build 2 images, then `HARNESS_P0_DOCKER=1 HARNESS_P0_LIVE=1 pytest section4/tests/integration/test_container_p0.py -v`) | root | 18 passed | 74s | Docker Desktop was not running at first; I launched it (`open -a Docker`); live Ollama qwen3.5:9b present. Test containers cleaned up |
| P1 Playwright shared-UI proof: `section4/.venv/bin/python deploy/pilot/run_smoke.py` | root | 1 passed (22.4s test), retries=0 | 32s | uses own PG container and ports 8101/8104/8105/3107. NOTE: it overwrote `docs/briefs/evidence/p1/{browser.junit.xml,live-result.json,multiplayer-complete.png}` and `section7/test-results/` with fresh run artefacts |

## Failures

1. **section7 `tsc --noEmit`** (`make frontend-tsc`): single error in generated `.next/types/app/projects/[projectId]/runs/[runId]/page.ts(34,29)` TS2344. Cause: `section7/app/projects/[projectId]/runs/[runId]/page.tsx:42` declares `params` as `Promise<{...}> | {...}`; Next 15's generated PageProps check requires `Promise<any>`, so the union (kept deliberately for tests, comment at lines 58-59) fails. Same error already noted in docs/briefs/p1-shared-ui-evidence.md. Not caught by vitest. Clean `tsc` is not achievable while `.next/types` is present, unless the prop type changes or .next/types is excluded.
2. **section4 flaky (non-deterministic) failures**:
   - `tests/unit/test_staging_e2e.py::test_stop_kills_worker_confirms_and_reports_stopped` (run 1, under 9-way load): `PermissionError: [Errno 1]` at `section4/src/harness_orchestrator/engine.py:180` (`os.killpg(proc.pid, 0)` only catches ProcessLookupError; on macOS killpg on an already-reaped/zombie group can return EPERM). Passed 2/2 when rerun alone.
   - `tests/test_heartbeat_claims.py::test_claim_query_contention_skip_locked_guard` failed in 4 consecutive runs (file alone x3, full suite x1) with `TypeError: fake_exec() missing 1 required positional argument: 'params'` at test_heartbeat_claims.py:183 (test fake `fake_exec(sql, params)` vs `claim_next_run` calling `db.execute(sql)` without params on some path), then passed 7/7 afterwards and in the full suite (696 passed). Unexplained; most likely `section4/src` was edited by another lane (repo has many in-flight modifications) between runs. Treat as unstable until a clean re-run on a frozen tree.
3. **pg-probe-s1 as documented (`make pg-probe-s1`) is not self-contained** (4 failed + 3 errors): `tests/test_money_numeric.py:503` falls back to hardcoded `localhost:5499` when `HARNESS_TEST_PG=1` (ignores `HARNESS_DATABASE_URL`/`PG_URL`); `test_fk_deletion_story.py:537-573`, `test_runs_index_tuning.py:510`, `test_ws_fingerprint.py` use `docker exec harness-db-1 psql`. Failures are environment (no compose DB / no Docker daemon at the time), not product bugs: with the shim all 662 pass.
4. `pg-probe-integration` against an unmigrated DB fails (see table); passes once migrations applied.

## Could not run / not run

- `make e2e` (section7 Playwright vs the compose stack): the stack was down at first and `make e2e` returned its documented skip (exit 246, "compose stack not reachable"). After Docker Desktop came up the pre-existing compose stack (harness-*-1, plus unrelated facescope-v2-*) auto-started; I deliberately did NOT run `make e2e` / `make e2e-compose` against it because they write into that stack's existing DB (harness-db-1 :5499) and I did not create it. The P1 proof was run via `deploy/pilot/run_smoke.py` instead (isolated).
- `make static-gates` as a single target not run verbatim; its four component commands were run individually (all pass).
- `make pip-audit`, `make test`/`make gate` aggregates not run (components run individually). No lint target for section11, mcp, packages, clients (no ruff venv/Makefile rule); not run.
- contracts/: no tests directory.
- Side effects: Docker Desktop launched by me and left running (compose stack containers restarted by Docker Desktop's own restart policy); scratch PG18 on :55433 stopped, data dir in scratchpad; scratch venv `vx` in scratchpad.
