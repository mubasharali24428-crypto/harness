# Session 10 local verification — 2026-10-19

Environment: local macOS checkout. UI tests use Vitest's Node/element-tree environment; database
tests use SQLite scratch fixtures. There is no production or real-user evidence in this report.

## Changes

- `docs/runbooks/client-support.md` states the English-only UI, locale formatting boundary, intended
  browser QA target, and unsupported clients without claiming tested compatibility.
- `docs/runbooks/release-compatibility.md` consolidates staged rollout, migration ordering,
  contracts/events, and rollback constraints.
- `docs/briefs/evidence/s10/readiness-report.md` records a NO-GO for broad rollout and full-product
  acceptance, with remaining gates and a local feasibility review of parts 49, 51 and 57.
- `section4/src/harness_orchestrator/pilot_metrics.py` validates baseline file shape, team match,
  date range, method, unique IDs, finite non-negative minutes and boolean acceptance before DB use.
  The five-valid-tasks-per-side gate and descriptive-only interpretation are unchanged.
- `docs/briefs/pilot-baseline-guide.md` now describes these validations and the remaining human
  responsibility for task comparability and truthfulness.

## Commands and results

| Command | Result |
|---|---|
| `cd section7 && npm test -- src/lib/a11y.test.ts src/components/AppShell.responsive.test.tsx 'app/projects/[projectId]/runs/[runId]/keyboard.test.tsx' 'app/projects/[projectId]/runs/[runId]/collab.test.tsx'` | 4 files, 94 tests passed. Tests are not a real-browser run. |
| `cd section7 && npm run typecheck` | Passed (`tsc --noEmit`, exit 0). |
| `cd section4 && ./.venv/bin/python -m pytest tests/unit/test_pilot_metrics.py -q -ra --tb=short` | 31 passed, 1 skipped because `HARNESS_TEST_PG` is not set; 315 SQLite datetime deprecation warnings. |
| `cd section4 && ./.venv/bin/ruff check src/harness_orchestrator/pilot_metrics.py tests/unit/test_pilot_metrics.py` | Passed. |
| `cd section1 && ./.venv/bin/python -m pytest tests/test_migrate_drift_gate.py -q --tb=short` | 6 passed; these are local drift-gate checks, not PostgreSQL migration execution. |
| `git diff --check` | Passed at report preparation. |

## Not run / not verified

- No real pilot or measured baseline; part 03 remains blocked until at least five comparable real
  tasks exist on each side.
- No real browser keyboard/reflow test, screen-reader review, axe/contrast check, or locale review.
- No PostgreSQL migration/backfill or combined image/schema rollback rehearsal. Docker is not
  available in this environment.
- No admin/customer release communication was sent; no rollout was performed.
- Roadmap is reviewed as a planning artifact; no pilot interview data exists to reprioritize it.
- Part 61 full-product acceptance remains blocked by unresolved partial/blocked rows and external
  gates. See `readiness-report.md`.
