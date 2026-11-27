# Task 49 — cloud self-test (2026-11-25, branch tip after Task 45a)

Cloud container: no Docker daemon, no section `.venv`s. `make gate` as a whole **cannot run here**; steps were run individually with a throwaway venv (`pip install -e section1[dev] -e contracts`, `npm ci` in section7 and clients/ts). Nothing below is a live/Docker PASS.

| Step | Result | Notes |
|---|---|---|
| `make gate` (whole) | **FAIL (env)** | stops in `static-gates` at `section4/.venv/bin/python scripts/spec_drift.py`: venv absent. Not a code failure. |
| `sh -n deploy/entrypoint.sh`, `docker compose config -q` | PASS | |
| `python3 contracts/ts/generate.py --check` | PASS | "TS mirror up to date" |
| `scripts/spec_drift.py` (run with the section1 venv instead of section4's) | PASS | "orchestrator specs and UI calls match the router" (output tail only; section4-only imports not exercised) |
| `scripts/eval/run_eval.py --check`, `scripts/check_env_knobs.py` | PASS | |
| `make guard-routes guard-isolation` | PASS | isolation: 325 src files, 10 sections |
| `make check-ts-dist` | PASS | after Task 41 regenerated `clients/ts/dist` |
| section1 `ruff check src tests scripts` | PASS | |
| section1 pytest, targeted (`test_roster_api.py`, `test_invites.py`) | PASS | 39 passed (incl. new D3 test) |
| section1 pytest, whole suite | **1288 passed, 64 skipped, 1 error** | the error: `tests/test_runs_index_tuning.py::test_0026_indexes_exist_after_verbatim_apply` — `sqlite3.OperationalError: too many SQL variables` while applying migrations verbatim on the sqlite lane. In a file this change does not touch (no migration or runs code edited); not re-run on the pre-change baseline, so "pre-existing" is likely but unproven. 10 tests in that file deselected for the rerun. PG-gated tests skipped (no Postgres here). |
| section7 `tsc --noEmit` | PASS | |
| section7 vitest (full) | PASS | 122 files / 1245 tests |
| `make readiness-drift` | **FAIL (expected)** | manifest 0076 vs disk 0081; see `task-45-readiness-drift.md` |
| Not run | — | section2–6, 8, 10, 11 pytest, mcp, language, e2e/Playwright, `make test-all` (needs Docker + PG 18): owner Mac. |

Verdict: no regression found in what could run; the cloud cannot certify the gate. Owner Mac must run `make gate` and `make test-all` (Task 50).
