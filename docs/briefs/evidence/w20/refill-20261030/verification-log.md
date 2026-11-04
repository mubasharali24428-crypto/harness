# Verification log — W20 owner-readiness refill (2026-10-30 evening)

Everything here is cheap and safe: no model calls ($0 spent), no compose
restart, no write to the running stack, no edit of any other session's file.
Host: macOS (Darwin 25.6.0), Docker Desktop, three sessions active in the same
tree and on the same Docker daemon, so timings are noisy. Each command ran from
the directory shown, with that directory's own `.venv/bin/python` or
`.venv/bin/ruff`. Times are PDT.

## Revisions measured

| Label | What | Notes |
|---|---|---|
| dirty | the shared worktree, `HEAD = e9e5589` | started 23:07:21; the tree changed under the run ([start](worktree-snapshot-start.txt), [end](worktree-snapshot-end.txt) differ). Measures other sessions' unfinished edits as well as HEAD. |
| `e9e5589` export | `git archive e9e5589` into a scratch directory | `PYTHONPATH` points every section at the export's `src`; imports were checked to resolve inside the export. |
| `55ad4b1` export | same, after the W13 commit at 23:24:08 | run with empty `DSH_WORKSPACE` / `DSH_SESSION_ROOT` (see finding 1). |

## Commands

```
# repository root = "/Users/user/untitled folder 2/harness"
python3 scripts/eval/run_eval.py --verify-only        # read-only: docker exec sha256sum
python3 scripts/eval/run_eval.py --check
section4/.venv/bin/python scripts/spec_drift.py
section4/.venv/bin/python -m pytest scripts/test_spec_drift.py -q
python3 scripts/check_env_knobs.py
python3 contracts/ts/generate.py --check
bash section7/scripts/ci_route_module_guard.sh routes ; … isolation
docker compose -f deploy/docker-compose.yml config -q
sh -n deploy/entrypoint.sh ; sh -n section7/scripts/ci_route_module_guard.sh
# per section S in section1 2 3 4 5 6 8 10 11:
cd S && ./.venv/bin/ruff check src tests [scripts]        # scripts/ only where the tree has one
cd S && ./.venv/bin/python -m pytest tests/ -q
cd mcp && ./.venv/bin/ruff check src tests ; ./.venv/bin/python -m pytest tests -q
cd section7 && npx tsc --noEmit ; npx vitest run
```

These are `make static-gates`, `make lint`, `make test` and `make frontend`
split into their parts. `make static-gates` as one target and `next build` were
not run.

## Results

| Check | Dirty worktree (`e9e5589` + edits) | `e9e5589` export | `55ad4b1` export |
|---|---|---|---|
| `run_eval.py --verify-only` | **`verified: false`**, only `engine.py` differs ([json](verify-only-e9e5589.json)); re-run at 23:35 on `55ad4b1`: still only `engine.py` ([json](verify-only-55ad4b1.json)) | - | - |
| `run_eval.py --check` | ok: 39 tasks, 6 held-out, 33 implemented | - | ok (same) |
| `spec_drift.py` | PASS at 23:07:28; **FAIL at 23:12:11** (two W13 routes missing from `identity.openapi.json`) | PASS | PASS |
| `scripts/test_spec_drift.py` | PASS | - | PASS |
| env knobs, TS mirror, route guards, compose config, `sh -n` ×2 | all PASS | - | env knobs and TS mirror PASS |
| Ruff, Sections 2 3 4 5 6 8 10 11 and MCP | clean | clean | clean |
| Ruff, Section 1 | 21 errors at 23:07 (15 in the untracked `tests/test_runner_exec.py`, 5 in the modified `runner_api.py`, 1 in an untracked `test_w17_*` file); 17 errors on a re-run minutes later. All in uncommitted W13/W17 files | clean | clean |
| pytest Section 1 | 1207 passed, 14 failed, 63 skipped (all 14 in the untracked `tests/test_runner_exec.py`, W13 mid-edit) | 1193 passed, 1 failed, 66 skipped | 1208 passed, 1 failed, 66 skipped |
| pytest Section 2 / 3 | 48 / 146 passed | same | same |
| pytest Section 4 | 1899 passed, 1 failed, 59 skipped (`test_spec_drift`, W13 routes); a second full run: 3 failed (finding 1); with empty workspace dirs: **1910 passed, 59 skipped** | 1894 passed, 1 failed, 60 skipped (finding 1); with empty workspace dirs: 1894 passed, 1 failed (finding 2) | 1904 passed, 1 failed, 60 skipped (finding 2) |
| pytest Section 5 | 440 passed, 8 skipped | 436 passed, 8 skipped | 451 passed, 8 skipped |
| pytest Sections 6 / 8 / 10 / 11 / MCP | 123 / 91 / 91 / 56 / 18 passed | same | same |
| `tsc --noEmit`, `vitest run` (Section 7) | PASS; 93 files, 1015 tests (includes the W22 follow-up's uncommitted tests) | not run | not run |

Per-section lines and failed test names: [`pytest-results.txt`](pytest-results.txt).
Skips are opt-in live-Postgres/Docker tests and are not passes.

## Findings from the runs (none is a product defect we could show)

1. **Section 4 tests share a default workspace.** `DeepSeekHarnessWrapper`
   defaults to `/tmp/harness-dsh-workspace`. Test runs leave `<uuid>/manifest.json`
   directories there (two appeared at 23:18, during these runs). The engine
   prepends a "Repository context" block built from that directory to the goal,
   so tests that assert the goal text then fail on any run after the first:
   `test_resume.py::test_pause_leaves_run_resumable…`,
   `test_engine.py::test_injected_backend_still_works`,
   `tests/stress/…::test_custom_backend_runner`. Evidence: `test_resume.py` passes with
   `HARNESS_REPO_CONTEXT=0`, and the whole Section 4 suite passes (1910 passed,
   all three included) with `DSH_WORKSPACE`/`DSH_SESSION_ROOT` pointed at empty
   directories. I did
   not delete the shared directory: other sessions may use it. A test-hygiene fix
   (per-test workspace) belongs to Section 4's owner.
2. **Intermittent timing failures in Section 4 full runs.** A different single
   test failed in each of three loaded full runs (`test_s1_r05_resume_queue`,
   `test_lifecycle_recovery::test_reclaimed_run_fences_the_original_worker`, and
   finding 1's). Each passes alone (`test_s1_r05_resume_queue` 3/3 in-tree and
   3/3 in the export; `test_lifecycle_recovery` 5/5). The machine was running
   three sessions and a Docker stack; the cause is not established.
3. **Section 1 version-pin test fails only in an export.**
   `test_version_pin_conformance` refuses an installed `harness-contracts` that
   is not an editable install of the tree under test; the section venvs point at
   the real worktree. In the real tree it passes with a warning that the
   installed metadata says `1.0.0rc3` while the source is `1.0.0rc6` (heal:
   `pip install -q -e ../contracts`).
4. **`verify-only` has a short reach.** It hashes three files per target
   (`engine.py`, `image_gate.py`, `harness-images.js`). After `55ad4b1` the
   deployed identity image still carries `e9e5589`'s `runner_api.py`
   (`8dfa1353…`, HEAD has `476d254c…`), yet `verify-only` reports nothing about it.
   `verified: true` therefore does not mean the stack equals HEAD. This is
   what W23's drift check is for.
5. **Spec drift flipped within minutes** because the W13 session was adding
   routes; it is a moving-target effect, gone in the exported commit.

## Reads (no change made)

* Running stack at 23:07: `harness-backend:vw22fix2` ×11 services,
  `harness-worker-daemon:vw22fix2`, `harness-frontend:vdev-local`.
  `DSH_MODEL` in the orchestrator container: `anthropic/claude-haiku-5.5`.
* `cost_ledger` (read-only `SELECT`, 2026-10-31 06:10 UTC): 219 rows,
  $0.288891 in total. Model by UTC hour for 2026-10-30: qwen rows through
  16:00, Haiku rows 16:00–18:00 (12 + 9 + 3 rows), `stub` rows 09:00 and 15:00.
* `agent_events` partitions: `_2026_09` … `_2026_12`; the last ends
  2027-01-01 00:00 UTC.
* Tree `e5bc4856` (what `vw22fix2` was built from, per `docs/m2-w22-plan.md`)
  versus `e9e5589`: no difference in any runtime source directory.
