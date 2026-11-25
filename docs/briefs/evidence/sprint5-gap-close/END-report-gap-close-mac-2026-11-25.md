# Sprint 5 gap-close: END report addendum (owner Mac results)

> Filled from the owner's Mac paste of 2026-11-25 18:07 UTC. Only rows with a real log are PASS/FAIL; everything else stays TBD or BLOCKED. This replaces the TBD template `END-report-gap-close-2026-11-26.md` on PR 37's branch.

- Date (UTC): 2026-11-25.
- **SHA under test on the Mac: `5793ce9f`** (`w28-s2-sprints1-4` tip before the master merge; worktree `harness-phaseb-b2`).
- PR 27's branch has since moved to the master merge `f2d0a5e` (master `84ee71d` merged in). **Nothing below was run on the Mac at `f2d0a5e`.** `make gate`, section1 Postgres and the readiness check at `f2d0a5e` are still to run.
- Extends `END-report-gap-close-2026-11-25.md` and `../sprint5/END-report-2026-11-24.md`.
- Verdict: **NOT READY** (unchanged; no STATUS flip from this report).

## Mac results at `5793ce9f` (owner-reported)

| Check | Command | rc | Result |
|---|---|---|---|
| Gate | `make gate` | 0 | **PASS**: "GATE GREEN" |
| section1 Postgres | `HARNESS_TEST_PG=1` section1 suite | 1 | **FAIL (test setup)**: 1367 passed, 2 failed in `test_partitioned_retention.py` (missing `HARNESS_DATABASE_URL` on the scratch-DB cases). Fixed on the branch after the merge, see below. |
| 45b readiness | RC `harness-rc` redeploy + manifest | 0 | **PASS on the Mac**: migrate 0077 to 0082, schema `80\|0082`, `stack_tags` AGREE (vv90r7), `make readiness-drift` (CI mode) rc=0, 0 drift items. Manifest written on the Mac, **not committed**. |
| RC build | `rc build` | fail | DB image pull timed out; 45b finished with backend/frontend build + `migrate` one-shot + `up --force-recreate`. |
| Playwright live B-09 (Task 36/43) | `npx playwright test e2e/invite-revoke-live.e2e.ts --retries=0` | none | **TBD**: not in this paste. `make gate` does not run this spec. |
| test-all (Task 50) | `HARNESS_TEST_PG=1 TEST_ALL_DOCKER=1 make test-all` | none | **TBD**: not in this paste. |
| R4 RC rows 6-12 | manual | none | **TBD** |

Owner's 45b handback, verbatim:

```
Mac truth at SHA 5793ce9f:
- stack_tags Agreement: AGREE (vv90r7)
- schema_migrations: 80|0082
- make readiness-drift (CI): rc=0
- manifest file written: Y (docs/briefs/evidence/90day/image-manifest-vv90r6-deployed.json) — NOT committed
```

Owner's log: `docs/mac-phase-b-run-2026-11-25.md` (addendum) in the owner's Mac agent store; not in this repo.

## CI on PR 27 at `f2d0a5e` (run 45670232971)

All jobs green: `changes`, static gates, vitest + tsc, security scan, e2e compose journey (with master's R4 Playwright spec), migrations on real PG, mcp, hx, and all nine pytest legs. One job is red: **`readiness drift (manifest vs HEAD)`**, `disk 80|0082 != manifest 74|0076`, pending the owner's manifest push. Cloud CI is not the Mac gate.

## section1 Postgres: the 2 failures

Cause: test bug, not a product regression. `test_pg_fresh_install_partitions_end_to_end` and `test_pg_partition_pruning_explain_and_detach_drop` read `os.environ["HARNESS_DATABASE_URL"]`. `make pg-probe-s1` exports it; a direct `HARNESS_TEST_PG=1 pytest` run does not, so both died with `KeyError`. Every other live-PG suite falls back to `HARNESS_TEST_PG` (as a URL), then `HARNESS_TEST_PG_DSN`, then `127.0.0.1:5499`.

Fix: `_pg_base_url()` in `section1/tests/test_partitioned_retention.py` uses the same fallback and forces the asyncpg driver.

Cloud check (local PG 16 on :5499, not the Mac):
```
# before the fix, HARNESS_DATABASE_URL unset
HARNESS_TEST_PG=1 pytest tests/test_partitioned_retention.py -k pg_   -> 2 failed (KeyError: 'HARNESS_DATABASE_URL')
# after the fix
HARNESS_TEST_PG=1 pytest tests/test_partitioned_retention.py          -> 24 passed (URL unset)
HARNESS_TEST_PG=1 HARNESS_DATABASE_URL=postgresql+asyncpg://... (make-style) -> 24 passed
ruff check                                                            -> All checks passed
```
The full section1 Postgres suite with this fix is **not** re-run on the Mac yet.

## Task ledger additions

| Task | Result | Commit | Evidence |
|---|---|---|---|
| 36 live invite revoke | API/log journey passed after 0082 (earlier Mac report). **Playwright PASS not claimed**: no log yet. | `5793ce9` | `../sprint5/task-36-live-invite-revoke.md` |
| 40 closure re-issue | this file | (this commit) | this file |
| 43 un-test.fail live journey | marker removed at `5793ce9`; **TBD** until the Playwright log exists | `5793ce9` | `../sprint5/task-36-live-invite-revoke.md` |
| 45b manifest | **PASS on the Mac at `5793ce9f`; manifest NOT committed**, so CI `readiness drift (manifest vs HEAD)` stays red on PR 27 until the owner pushes it | none yet | `task-45-readiness-drift.md` |
| 49b section1 PG test setup | **FIXED** (cloud-verified on the 2 tests only) | (this commit) | this file |
| 50 Mac truth | gate PASS at `5793ce9f`; test-all **TBD** | none | this file |

## Open items (carried, not closed)
- Manifest commit (owner Mac). Since `f2d0a5e` carries the same migrations (`80|0082` on disk), a manifest from the `5793ce9f` redeploy also satisfies the CI check at `f2d0a5e`; the CI job shows that once it is pushed. CI mode checks lineage, migrations and image tags only. The live check (`make readiness-drift-live`) also compares the RC daemon's shell gate with HEAD, and `f2d0a5e` brings master's PR 33 `shell_gate.py` change, so the RC stack deployed from `5793ce9f` is expected to show gate drift until it is redeployed from the post-merge SHA.
- Mac `make gate` + section1 Postgres at `f2d0a5e` (post-merge SHA), then the owner decides "merge PR 27".
- F-05 read semantics: OPEN. Shell-gate xfails stay.
- Blind-approval "diff seen" rule under R4: applied, owner-confirmable (`docs/decisions/2026-11-25-blind-approval-diff-seen-DRAFT.md`). Rate itself UNPROVEN.
- PR 27: not merged. PR 28: draft, untouched.
- P-D7 DEFERRED; payment and global P-D6 off.
