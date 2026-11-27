# Sprint 5 gap-close: END report addendum (Mac results), TEMPLATE

> **Every `TBD` line below is a placeholder awaiting the owner's Mac paste. TBD is not PASS. Do not read any row as passing until it is filled with a real log and SHA.**

- Prepared 2026-11-25 (cloud). Fill date (UTC): TBD.
- Branch / SHA under test: TBD (`git rev-parse HEAD` on the Mac).
- Extends `END-report-gap-close-2026-11-25.md` on PR 27's branch and `../sprint5/END-report-2026-11-24.md`.
- Verdict: **NOT READY** unless the owner changes the gates in writing.

## Mac results (owner paste)

| Check | Command | Result | Log / evidence |
|---|---|---|---|
| Gate | `make gate` | TBD | TBD |
| section1 Postgres | `cd section1 && HARNESS_TEST_PG=1 ./.venv/bin/python -m pytest tests/ -q` | TBD | TBD |
| section1 lint | `./.venv/bin/ruff check src tests scripts` | TBD | TBD |
| Playwright live B-09 (Task 36/43) | `cd section7 && E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 E2E_ORCHESTRATOR_URL=http://localhost:8004 npx playwright test e2e/invite-revoke-live.e2e.ts --retries=0` | TBD (owner saw 1 passed at 5793ce9 after 0082; not yet recorded here) | TBD |
| test-all (Task 50) | `HARNESS_TEST_PG=1 TEST_ALL_DOCKER=1 make test-all` | TBD | TBD |
| 45b readiness manifest | see `task-45b-blocked-waiting-on-mac.md` | **BLOCKED** | TBD |
| R4 RC rows 6-12 | manual, `docs/briefs/evidence/ui/r4-since-last-iteration.md` | TBD | TBD |

## Task ledger additions

| Task | Result | Commit | Evidence |
|---|---|---|---|
| 36 live invite revoke | TBD (cloud has no Mac run) | 5793ce9 | `sprint5/task-36-live-invite-revoke.md` |
| 40 closure re-issue | TBD | TBD | this file |
| 43 un-test.fail live journey | TBD | TBD | TBD |
| 45b manifest | BLOCKED | none | `task-45b-blocked-waiting-on-mac.md` |
| 50 Mac truth | TBD | TBD | TBD |

## Open items (carried, not closed)
- F-05 read semantics: OPEN. Shell-gate xfails stay.
- Blind-approval rate: UNPROVEN.
- PR 27: HOLD (see `pr-27-merge-options.md`). PR 28: draft.
- P-D7 DEFERRED; payment and global P-D6 off.

## Fill-in checklist for the cloud thread after the paste
1. Replace each TBD with PASS/FAIL and the pasted log excerpt; keep FAIL as FAIL.
2. Update `task-36-live-invite-revoke.md`, `B-09-checklist-2026-11-24.md` (if still FAIL), and `mac-gate-<shortsha>.md`.
3. STATUS.md: Task 36 row only if the Mac log says PASS. Verdict stays NOT READY.
