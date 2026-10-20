# Live PostgreSQL failure in `test_session6_live_pg.py`: diagnosis

Question from the owner's local run (PG 18.4): `test_session6_postgres_races_artifacts_search_export_and_retry` failed at
`before["items"]` with `KeyError: 'items'`. Is that a bug in search on PostgreSQL, or an environment difference (their scratch PG had no
`pgvector`)?

**Answer: neither of those two, and not only one thing.** The visible `KeyError` was a stale key in the test. Fixing it let the test run
on to two more failures, one of which is a real PostgreSQL-only bug. `pgvector` is not involved: this run's PG has no `pgvector` either
(`pg_available_extensions` has no `vector`), and the migration selftest below passes without it.

## The three defects, in the order the test reached them

| # | Where | What | Kind | Fix |
|---|---|---|---|---|
| 1 | `section4/tests/unit/test_session6_live_pg.py` (two reads) | `search.search()` returns its hits under `results`; the test read `items`. Every SQLite search test reads `["results"]`. | Stale test key (commit `25fde58`) | Read `results`. After this the search assertions, including "hidden comment disappears", **pass on PG**. |
| 2 | `section4/src/harness_orchestrator/search.py` `_stmt` | List arguments were bound as untyped expanding parameters. An **empty** list renders `IN (SELECT CAST(NULL AS INTEGER) WHERE 1!=1)`, and PostgreSQL refuses `character varying = integer`. SQLite accepts it. `exports.create_export` passes `sess=[]` for a run that is not in a session, so the export failed with `EXPORT_FAILED` (HTTP 500) **on PostgreSQL only**. | **Real bug**, PG only | `_list_type`: non-empty all-int lists stay `Integer` (event keys in `_live`), everything else, including empty, is `String` (every empty-capable call site is a text id or type). |
| 3 | `test_session6_live_pg.py` (last assertion) | `execution.retry()` returns `(body, created)`; a repeated `requestId` correctly returns `created == False` (HTTP 200, pinned by `test_execution_runs.py` line 497 `first 202, again 200`). The test named the element `replay` and asserted it true. | Inverted test assertion | Assert `not created`. The code and PG were right. |

Also fixed while running the full suite: `tests/unit/test_run_get.py::test_get_run_includes_prid_from_db` asserted an exact key set that
predates `workspace` and `lifecycle` (added by committed `service.py`, commit `7fd8f88`). Stale test; added the two keys.
`tests/unit/test_limit_sweeper.py` polled for 2 s and failed once in one of two full runs (it passed 5 of 5 alone and in the second
full run); the polling budget is now 10 s. The cause was not proven; this is a precaution, not a diagnosis.

## Tests added

`section4/tests/unit/test_search_in_list_typing.py` (6, need no server): compiles for the PostgreSQL dialect and asserts the empty list
renders `CAST(NULL AS VARCHAR)` and never `INTEGER`; string lists stay text; int lists stay integer; bool is not an int list; `rows()`
with an empty and a one-item list works on SQLite. Run against the pre-fix `search.py` (stashed): 4 of 6 fail, including
`test_empty_list_renders_as_text_not_integer`; with the fix all 6 pass. The end-to-end proof is the live test itself (defect 2 is exactly
its export step).

## Commands and results (this session, 2026-10-20)

Environment: PostgreSQL 16.15 (Ubuntu package), own cluster on `127.0.0.1:5544` (nothing on 5432 touched), Python 3.13.16, no Docker
daemon. Not the owner's PG 18.4.

| Command | Result |
|---|---|
| `pytest tests/unit/test_session6_live_pg.py` before any fix | 1 failed, `KeyError: 'items'` (reproduced) |
| same, after fix 1 only | failed in `exports.create_export` (`character varying = integer`) |
| same, after fixes 1 and 2 | failed at the `retry` assertion |
| same, after fixes 1 to 3 | **1 passed** |
| all seven section4 live-PG modules (`test_session4/5/6_live_pg`, `test_cost_rollup_live_pg`, `test_collab_chaos_live_pg`, `test_claim_cap_live_pg`, `test_demo_seed_live_pg`) | **18 passed** |
| `deploy/runners/migrate_selftest.py` (`HARNESS_TEST_PG=1`) | `PASS: 55 migrations (0001 to 0056) applied clean on real PG; re-run idempotent` |
| `test_search*.py`, `test_exports*.py`, `test_search_in_list_typing.py` on SQLite | 80 passed |

## Honest limits

* The typed-list fix is in the shared `search.rows` helper. The sibling helpers `discussion._q`, `execution._q` and `delivery._stmt` use
  the same untyped pattern. Their call sites were read: `discussion` returns early on an empty page and empty id list, `execution._check_deps`
  is behind `if deps:`, and `delivery`'s list is the constant non-empty `NOISE_KINDS`. So no reachable empty list was found, but those
  helpers were not made type-safe and no PG test provokes an empty list through them.
* PG 16.15 here, PG 18.4 on the owner's machine: the failure and the fix do not depend on the version, but only 16.15 was run.
* `pgvector` was absent in this run, so any behaviour that needs it was not exercised.
