# Cloud pass report (Session 11, 2026-10-20)

Branch: `claude/path-to-9-finish-ax6hie` (the designated branch for this session; see "Deviations"). Nothing was pushed to `master` or to
the owner's `cloud/path-to-9-finish`; no pull request was opened; auto-merge was not touched.

This file is the running record and is completed at the end of the session (the "Final status" section below says whether it was).

## Deviations from the brief (read first)

1. **The checkout did not match the brief.** The checkout started at `master` (`9f85590`, the v1.0.0-candidate of 2026-08-26), not at
   `84703cb`. The expected history lives on `origin/cloud/path-to-9-finish` (82 commits ahead; tip `bd48c41`, which already carried the
   plain-language UI work and the draft process evidence). The designated branch was fast-forwarded onto that tip (no history rewritten)
   and work continued from there. The brief's "new branch named `cloud/path-to-9-finish`" was not used because that branch already exists
   on the remote with the owner's commit.
2. **Tasks 1 and 3 had already landed** in `bd48c41`; they were re-verified, not redone (see below).
3. **`docs/master-prompts/` is not in the repository.** Directive wording could not be re-read against the source; see
   `evidence/s11/process-parts.md`.
4. **Section 4 cannot be imported from the pushed history**, so every section 4 result below was produced with a verification-only
   stand-in for the uncommitted `container_runtime` module (and, where stated, two more shims). Full detail: `evidence/s11/clean-checkout-gaps.md`.

## What was verified

| Check | Result |
|---|---|
| `section7`: `npx tsc --noEmit` | clean |
| `section7`: `npx vitest run` | 70 files, 824 tests passed |
| `test_session6_live_pg.py` on real PostgreSQL 16.15 (own cluster, port 5544) | failed three different ways; all fixed; now passes (`evidence/s11/live-pg-search-export.md`) |
| all 7 section 4 live-PG modules | 18 passed |
| `deploy/runners/migrate_selftest.py` | PASS, 55 migrations (0001 to 0056), idempotent re-run |

## What was fixed

* `section4/src/harness_orchestrator/search.py`: empty `IN` lists were typed integer; PostgreSQL rejected them, so an export of a run with no
  sessions failed with `EXPORT_FAILED` on PostgreSQL only. New test `test_search_in_list_typing.py` (fails 4 of 6 on the old code).
* Two wrong assertions in `test_session6_live_pg.py` (stale `items` key; inverted `created` flag), a stale key set in `test_run_get.py`, and
  a tight polling budget in `test_limit_sweeper.py` (precaution after one unexplained failure).
* Docs: process parts 01, 02, 15 to 19, 34, 35 (`process-parts.md`, `final-deliverables.md`), `PROJECT-STATE.md` rewritten to 98 lines,
  19.010 note, `test_program_docs.py` (links, brief length, 61 matrix rows, verified rows cite existing evidence, external caps, counts).

## Matrix

Before this pass (tree at `bd48c41`): verified 34 · implemented 11 · partial 14 · missing 0 · blocked 2.
After (so far): verified 43 · implemented 4 · partial 12 · missing 0 · blocked 2 (rows 01, 02, 15 to 19, 34, 35 flipped).

## Not run, and why

* The pilot smoke (`deploy/pilot/run_smoke.py`), compose stack, anything needing the Docker daemon: no daemon in this box.
* A real browser, a screen reader, axe or contrast tooling: not run in this pass.
* The 4 section 1 tests that need `docker exec harness-db-1`: no Docker.
* `pip-audit`, Linux Landlock, an independent penetration test, a real backup restore: not run.
* `test_lifecycle_recovery.py::test_worker_crash_blocks_honestly_and_stays_resumable`: fails on the pushed history because the committed
  `standin_worker.py` has no crash case; not fixable here (reserved file) and not run with the case added.
* T13b (OTP redeem race): documented only; `auth.py` was not edited.

## Still externally blocked

Part 03 (real pilot cohort and baseline), 37 (live identity provider), 52 and 58 (pen test, counsel), 53 (real restore), 61 (full acceptance).

## Final status

In progress: this section is replaced at the end of the session.
