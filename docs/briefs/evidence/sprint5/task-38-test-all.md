# Task 38 — `make test-all` evidence (G-X02-1)

- Date (UTC): 2026-11-25
- Tree: `bb124ba` (Task 37) on `w28-s2-sprints1-4` — Tasks 31, 32, 37 included.
- Host: cloud container (Linux 6.18 Firecracker VM, running as **root**), Python 3.13.18, Node 22.
- Docker: CLI installed, **daemon down** (`/var/run/docker.sock` missing). No compose stack.
- Postgres: no compose `db`. Stood up a **local PostgreSQL 16.15** on `:5499` (`harness/harness`,
  db `harness`), fresh database, migrated with `deploy/runners/migrate.py` (79 migrations, 0001→0081)
  to match what the compose `migrate` one-shot does. CI and the owner Mac use **PostgreSQL 18**; a
  16-vs-18 difference is possible and is called out below where it could matter.

## How it was run

`make test-all` itself cannot run here: its first prerequisite `pg-up` calls
`docker compose up -d db`. The same groups were run with the Makefile's own commands and `PG_ENV`
values (`HARNESS_TEST_PG=1`, `HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@localhost:5499/harness`,
`HARNESS_TEST_PG_DSN=postgresql://…`, `HARNESS_TEST_PG_CONTAINER=` empty because there is no container),
in the target's order. Venvs: `make bootstrap`'s recipe per section.

## Results per group

| # | Group (Makefile line) | Result | Numbers |
|---|---|---|---|
| G1 | `pg-probe-integration` — `integration/test_checkpoint1.py` on PG | **PASS** (with 1 documented skip) | 3 passed, 1 skipped — the skip is the checkpoint-1 happy path, intentionally skipped pending the redesign (thread C / `checkpoint1-redesign-2026-11-21.md`) |
| G2 | `pg-probe-s1` — section1 suite on PG | **PASS except 12 BLOCKED (env)** | 1348 passed, 1 skipped, 1 failed + 11 errors. All 12 are `tests/test_fk_deletion_story.py::TestLivePostgresDeletionStory` (3) and `tests/test_runs_index_tuning.py` (9): they `docker exec` into the compose db container (`AssertionError: docker container  not reachable`). Not a product failure; need Docker → owner Mac |
| G3 | `pg-probe-migrate` — `deploy/runners/migrate_selftest.py` | **PASS** | `migrate_selftest PASS: 79 migrations (0001→0081) applied clean on real PG; re-run idempotent.` (PG 16) |
| G4 | section4 suite against real PG (PG-gated tests unskipped) | **PASS except 4 BLOCKED (env)** | 2497 passed, 22 skipped, 4 failed. The 4 are listed below; each fails identically on the untouched baseline `17015624` in this container |
| G5 | Docker P0 isolation (`tests/integration/test_container_p0.py`) | **BLOCKED (env)** | `TEST_ALL_DOCKER` path needs a daemon: would print `SKIP Docker P0: docker daemon not reachable` |
| G6 | promtool-gated section1 tests | **SKIP (tool absent)** | `promtool` not installed in the container |
| G7 | landlock (section5, Linux) | **SKIP (runtime)** | 2 passed, 13 skipped — landlock tests skip with `landlock not usable in this runtime` (Firecracker kernel), Seatbelt tests are macOS-only. Not a PASS of confinement |
| — | Opt-in live groups (`HARNESS_P0_LIVE`, `HARNESS_LIVE_OPENROUTER`) | **Not run (by design)** | local model / paid endpoint |

### The 4 section4 failures (G4) — environment, pre-existing

| Test | Cause in this container |
|---|---|
| `test_forbidden_path_mounts.py::test_real_container_readonly_bind_blocks_bypass` | `docker run` → daemon not reachable (CLI present, so the test does not skip) |
| `test_scope_mounts.py::test_real_container_scope_mounts_refuse_the_m01_bypass` | same |
| `test_worker_daemon.py::test_a_root_launcher_runs_the_worker_as_the_workspace_owner` | container runs as uid 0, workspace is owned by root → expected `10001:10001`, got `0:0` |
| `test_isolation_and_staging.py::test_hung_worker_is_killed_at_the_operator_bound` | grandchild reported alive after the group kill — no reaping init here, so the killed process lingers as a zombie |

Baseline check: the same four were run against a worktree of `17015624` (before any Sprint 5 commit)
with the same venv → `4 failed`. Sprint 5 changes did not introduce them.

One more data point: on an **unmigrated** PG database the section4 run also failed
`test_verdict_post_live_pg.py::test_collect_facts_and_build_on_real_pg`
(`relation "agent_runs" does not exist`); after `migrate.py` it passes. The test assumes a migrated
DB, which compose provides; worth a one-line note in `docs/ci-gates.md` for anyone running PG
externally (`HARNESS_TEST_PG_EXTERNAL=1`). Not changed here.

## Sprint 5–specific PG coverage

- `test_collab_approvals_live_pg.py` + `test_collab_chaos_live_pg.py` → **6 passed** on PG: the
  Task 32 change (rendering read in its own session before the decision transaction) holds on a real
  Postgres, including the concurrent approve/reject race.
- Task 37's route is SQLite-tested only (no PG-specific SQL beyond a plain `SELECT`).

## Verdict

Everything this container can run is green apart from tests that need a Docker daemon, a non-root
user with an init, `promtool`, or landlock. Those are **BLOCKED (env)**, not PASS. The truth run stays
the owner Mac: `make test-all` with `TEST_ALL_DOCKER=1` on PG 18, plus `make gate` on the final SHA.

Logs (container-local, not committed): section4/section1 PG runs and the baseline comparison were
captured from the commands above; the counts in this file are copied from their summary lines.
