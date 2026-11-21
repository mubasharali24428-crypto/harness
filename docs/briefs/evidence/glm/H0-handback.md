# Hand-back: H0
Model: GLM 5.3 Flash   Date: 2026-11-14   Revision: a049897
Status: DONE
Token use (estimate): 70000

Measure-only packet (baseline ledger). No source or test file touched. Nothing installed. No service started. No Playwright.

## Pre-edit snapshot
- git status --short (first 80 lines):

```
 M .env.example
 M docs/plans/ui-overhaul-plan-2026-11-13.md
 M scripts/eval/bench_stats.py
 M section4/src/harness_orchestrator/reviewer.py
 M section4/src/harness_orchestrator/service.py
 M section4/src/harness_orchestrator/signals.py
 M section4/tests/conformance/test_orchestrator_conformance.py
 M section4/tests/conftest.py
 M section4/tests/stress/test_section4_stress.py
 M section4/tests/test_done_signal.py
 M section4/tests/test_stateless_orchestrator_n1.py
 M section7/e2e/a11y.e2e.ts
 M section7/e2e/palette.e2e.ts
 M section7/src/components/workspace/RunColumn.tsx
?? .openclaw/
?? "FUTURE PLAN 2.0.md"
?? docs/briefs/evidence/glm/
?? docs/briefs/evidence/ui/before-home-1280-dark.png
?? docs/briefs/evidence/ui/before-home-1280-light.png
?? docs/briefs/evidence/ui/before-home-375-dark.png
?? docs/briefs/evidence/ui/before-home-375-light.png
?? docs/briefs/evidence/ui/before-home-768-dark.png
?? docs/briefs/evidence/ui/before-home-768-light.png
?? docs/briefs/evidence/ui/before-review-1280-dark.png
?? docs/briefs/evidence/ui/before-review-1280-light.png
?? docs/briefs/evidence/ui/before-review-375-dark.png
?? docs/briefs/evidence/ui/before-review-375-light.png
?? docs/briefs/evidence/ui/before-review-768-dark.png
?? docs/briefs/evidence/ui/before-review-768-light.png
?? docs/briefs/evidence/ui/before-run-1280-dark.png
?? docs/briefs/evidence/ui/before-run-1280-light.png
?? docs/briefs/evidence/ui/before-run-375-dark.png
?? docs/briefs/evidence/ui/before-run-375-light.png
?? docs/briefs/evidence/ui/before-run-768-dark.png
?? docs/briefs/evidence/ui/before-run-768-light.png
?? docs/briefs/evidence/ui/u0-inventory-2026-11-14.md
?? docs/briefs/evidence/ui/week-ui.md
?? docs/briefs/glm-harness-prompt-2026-11-13.md
?? docs/briefs/glm-master-prompt-2026-11-14.md
?? docs/briefs/ui-build-prompt-2026-11-14.md
?? docs/briefs/ui-master-prompt-2026-11-14.md
?? docs/decisions/2026-11-13-du1-component-stack.md
?? docs/decisions/2026-11-13-du2-visual-direction.md
?? docs/decisions/2026-11-13-du3-u0-before-p17.md
?? docs/decisions/2026-11-13-du4-risk-tiers.md
?? docs/decisions/2026-11-14-verification-first-default-on.md
?? docs/plans/compiler-hx-hermes-plan-2026-11-13.md
?? docs/plans/hermes-improvement-plan-2026-11-13.md
?? scripts/eval/test_bench_split.py
?? section7/e2e/ui-baseline.e2e.ts
?? section7/src/components/PhoneTriage.test.tsx
?? section7/src/components/PhoneTriage.tsx
```

- `git rev-parse --short HEAD`: `a049897`
- sha256 of each file I may edit: none applicable. The packet lets me create only `H0-handback.md`, `H0-ledger-2026-11-14.md` and the folder `H0-raw/`; no pre-existing file is editable, so there was nothing to hash. No shasum was needed before editing.

## Plan
1. Create `docs/briefs/evidence/glm/H0-raw/` -> verify: `ls` shows the directory - DONE
2. Run section1 pytest, tee to raw file -> verify: raw file non-empty, summary in tail - DONE
3. Run section4 pytest, tee to raw file -> verify: same - DONE
4. Run section5 pytest, tee to raw file -> verify: same - DONE
5. Run section7 vitest, tee to raw file -> verify: same - DONE
6. Run section7 tsc --noEmit, tee to raw file -> verify: same - DONE
7. Classify every skip from `-rs` output -> verify: perl aggregation prints a TOTAL_SKIPS matching the summary count per suite - DONE (63 / 60 / 8)
8. Group every failure by first line of error text -> verify: single group covers all 4 failures, proof lines cited - DONE
9. Write `H0-ledger-2026-11-14.md` -> verify: file exists, 5-row table + skip table + failure groups + comparison line - DONE
10. Re-run `git status --short` and compare to snapshot -> verify: no change outside `docs/briefs/evidence/glm/` - DONE (output identical to the snapshot; my files live inside the already-untracked `docs/briefs/evidence/glm/` dir)

## Files changed (uncommitted)
- `docs/briefs/evidence/glm/H0-handback.md` - hand-back, written as I go
- `docs/briefs/evidence/glm/H0-ledger-2026-11-14.md` - the baseline ledger
- `docs/briefs/evidence/glm/H0-raw/section1.txt` - raw section1 pytest output (1617 lines)
- `docs/briefs/evidence/glm/H0-raw/section4.txt` - raw section4 pytest output
- `docs/briefs/evidence/glm/H0-raw/section5.txt` - raw section5 pytest output
- `docs/briefs/evidence/glm/H0-raw/section7-vitest.txt` - raw vitest output
- `docs/briefs/evidence/glm/H0-raw/section7-tsc.txt` - raw tsc output (1 line; tsc printed nothing, exit 0)
No other file was created, edited, moved or deleted.

## Checks run
- `git status --short | head -80` - exit 0 - snapshot captured above; re-run after all suites produced output identical to the snapshot (see Plan step 10)
- `git rev-parse --short HEAD` - exit 0 - `a049897`
- cd section1 && `.venv/bin/python -m pytest tests -q -rsfE -p no:cacheprovider 2>&1 | tee "$R/section1.txt" | tail -15` - pipeline exit code 0, pytest's own exit masked by the tee (see Known limits) - `4 failed, 1241 passed, 63 skipped, 1714 warnings in 104.57s (0:01:44)`, shell wall 108s
- cd section4 && same via background process with `set -o pipefail` - exit 0 - `2379 passed, 60 skipped, 26234 warnings in 132.32s (0:02:12)`; `RUN_META: exit=0 wall=137s`
- cd section5 && same - exit 0 - `542 passed, 8 skipped, 4 xfailed, 1 warning in 44.15s`; `RUN_META: exit=0 wall=45s`
- cd section7 && `npx vitest run 2>&1 | tee "$R/section7-vitest.txt"` - exit 0 - `Test Files  108 passed (108)` / `Tests  1186 passed (1186)`; `RUN_META: exit=0`
- cd section7 && `npx tsc --noEmit 2>&1 | tee "$R/section7-tsc.txt"` - exit 0 - no output; `RUN_META: exit=0 wall=2s`
- skip aggregation (perl over each raw file) - exit 0 - `TOTAL_SKIPS 63` (section1), `TOTAL_SKIPS 60` (section4), `TOTAL_SKIPS 8` (section5)
- `grep -n "sqlite3.OperationalError: no such column"` - exit 0 - error appears at `H0-raw/section1.txt:195, 546, 895, 1252`, one per failed test
- `shasum -a 256 docs/briefs/evidence/glm/H0-raw/*.txt` - exit 0 - digests recorded in the ledger

## Red runs (failures before the change)
- n/a - measure-only packet; no behaviour added, so the red-then-green rule does not apply.

## Checks not run
- Playwright / any browser run - not in scope per packet - environmental (explicit exclusion)
- any suite run from the repo root to reproduce the `test_f1_envelope_idempotency` root-.env fault - not needed by the packet, and the master prompt already labels it an environment fault - environmental

## Decisions I made (reviewer may overrule)
- Classified `promtool not installed` (section1, 1 skip) as environmental: the packet's classes name Docker/Postgres/env vars as environmental and TODO/no-reason as defect; a missing external binary fits neither cleanly. I chose environmental and flag it here.
- Recorded section1's exit code as "not captured (pipeline masked it)" rather than re-running the suite; the summary line shows `4 failed`, so pytest's exit would have been 1, but that is inference, and the ledger says so.
- The 4 section5 `xfailed` tests are counted in their own entry in the suite table, not as passed, failed or skipped.
- Interpreted "first line of the error text" as the first `E`-prefixed exception line in each failure block (`E  sqlite3.OperationalError: no such column: e.payload`), not pytest's separator line.

## Open questions
- Was the old vitest baseline (1161 passed / 1 failed) measured at the same commit as the old section4 baseline (f7af86d)? Today's HEAD is `a049897`, so the vitest delta (+25 passed, 0 failed) may come from tree differences, not a fix. Command to settle it: `git log --oneline f7af86d..a049897 -- section7` if a reviewer wants the history.

## Unrelated issues seen
- `section1/tests/test_runs_index_tuning.py` - 4 tests fail with `no such column: e.payload`: the test query selects `e.payload` from `agent_events`, which the test schema does not provide. Classified as defect group G1 in the ledger; not fixed (out of scope). Related to no change of mine - I changed nothing - but it is in scope of the packet's failure-grouping step.
- No failures in section4, section5, vitest or tsc; post-run git status identical to snapshot.

## Known limits of this change
- Baseline is valid only for the dirty tree at `a049897` as found; other lanes' uncommitted edits are inside the measurement and cannot be separated by me.
- section1's exit code was masked by `tee` in the first run (my sequencing error: I piped through `tail` before switching to `set -o pipefail` for the other four checks); no number was invented to fill the gap.
- section4 and section5 runs went through background processes (`tools.process.start`) because a 300s shell limit was in play; output was `tee`'d to the raw files exactly as the packet specifies. An earlier attempt to run section4 with a plain shell background job died when the shell call returned; the failed attempt produced no output file.
- Wall times include interpreter/session start-up differences between the shell-measured wall (108s/137s/45s) and the in-run reported durations (104.57s/132.32s/44.15s); both are recorded.
- The tsc raw file is 1 line: `tsc --noEmit` emitted nothing and exited 0. "0 errors" means "no output, exit 0", not a count of type checks performed.
