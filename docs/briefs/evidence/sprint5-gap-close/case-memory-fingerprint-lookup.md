# Track 2: case-memory similarity hardening (exact-fingerprint lookup)

Date: 2026-11-25. Branch `claude/project-thread-4hfm82` off `w28-s2-sprints1-4` @ fe2233c.
Scope: hybrid-memory layer B (case memory). Cloud-only work; no live run.

## What changed (implemented)

`section4/src/harness_orchestrator/case_memory.py`

* `load_candidates(db, team_id, fp)` = union of
  1. `load_cases`: the team's newest `MAX_SCAN` (200) cases, as before, now with a
     `case_id` tie-break so the window edge is deterministic when timestamps tie;
  2. `load_fingerprint_matches`: up to `MAX_FINGERPRINT_MATCHES` (50) cases in the same team
     whose `fingerprint` equals the query's, at any age, newest first.
* `merge_candidates` dedupes by `caseId`, first occurrence wins.
* `recall_and_record` (denial stop) and `collab.similar_cases_for_run` (approvals list,
  run-page `similarCases`) both use `load_candidates`. Ranking (`retrieve_similar_cases`:
  Jaccard on paths + tool bonus, times outcome weight with one-week half-life) is unchanged.
  `MAX_RECALLED` stays 3.

Effect: an exact repeat of a denial (same failure class, tool and path set) is recalled
even after the team has logged more than 200 newer cases. For a team with at most 200
cases the result is byte-identical to the old window scan (property test below).

## Laws kept

* Team-scoped: both queries filter `team_id`; the foreign-team case is asserted absent.
* CD-02: no new write path. The only write is still the existing `INSERT ... status='proposed'`
  in `recall_and_record`. Nothing writes a project rule or touches a prompt.
* No migration, no contracts change, no schema change.

## Why no migration (decision)

The kickoff allowed a forward-only migration "if needed". An index
`case_memory (team_id, fingerprint)` would make lookup (2) an index seek, but adding
`0083_*.sql` now makes CI's `readiness-drift` job fail: it compares disk migrations
(`81|0083`) to the deployed RC manifest (`80|0082`,
`docs/briefs/evidence/90day/image-manifest-vv90r6-deployed.json`), which only a real
redeploy may refresh. Today lookup (2) uses the existing `case_memory_team_recent
(team_id, recorded_at DESC)` index for the team prefix and filters fingerprint inside the
team; that is correct at any size and cheap at pilot sizes.

**Proposed (not landed)**, to ship with the next RC redeploy:

```sql
-- 0083_case_memory_fingerprint_idx.sql
CREATE INDEX IF NOT EXISTS case_memory_team_fingerprint
    ON case_memory (team_id, fingerprint, recorded_at DESC);
```

## Still proposal (not implemented)

* Partial-overlap recall beyond the window (cases sharing some but not all paths, older
  than the newest 200). Options: a `case_memory_paths (team_id, path, case_id)` token table,
  or pgvector. pgvector is not a safe dependency: `0030_pgvector.sql` creates the extension
  only when the host image ships it, so stock `postgres:*` deployments have no vector type.
* Live journey check (Task 35 style, similarCases on the run page): unchanged code path in
  the UI; not re-run here. Owner Mac only.

## Commands (cloud container, Python 3.13, section4 venv from `make bootstrap` steps)

```
cd section4
./.venv/bin/python -m pytest tests/unit/test_case_memory.py tests/unit/test_similar_cases_api.py -q
  -> 12 passed (exit 0)
# Same new tests against the old case_memory.py/collab.py (git stash of src/):
  -> 5 failed, 7 passed (the new tests catch the window-only behaviour)
./.venv/bin/python -m pytest tests/unit -q   (full section4 unit suite)
  -> 2197 passed, 44 skipped, 4 failed (exit 1). The 4 failures are real-container /
     root-launcher sandbox tests (test_forbidden_path_mounts, test_scope_mounts,
     test_isolation_and_staging, test_worker_daemon); they fail identically on the base
     fe2233c in this container and do not touch case memory.
./.venv/bin/python -m pytest tests/unit -q -k "case or similar or denial or learned or deployed_router"
  -> 128 passed (exit 0)
./.venv/bin/ruff check src tests -> All checks passed (exit 0)
```

New tests:

* `test_exact_repeat_older_than_the_window_is_recalled_and_stays_team_scoped`
* `test_union_is_identical_to_the_window_scan_while_a_team_fits_in_the_window` (seeded random,
  60 cases, 25 queries)
* `test_recall_and_record_finds_an_old_repeat_then_stores_a_proposal`
* `test_merge_candidates_dedupes_by_case_id_and_keeps_first_order`
* `test_approvals_list_recalls_an_exact_repeat_beyond_the_scan_window`

`make gate` and the PG suites were not run in the cloud; they stay on the owner's Mac.
