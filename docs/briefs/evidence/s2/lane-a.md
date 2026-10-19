# Lane A evidence: lifecycle, audit/events, approval rules (parts 10, 11, 22, 23, 05-08)

Code was written by a subagent whose process exited before it reported; the lead verified the surviving tree. Every number below
is from commands the lead ran on 2026-10-18 after the lint fixes.

## What exists
- Lifecycle: `section4/src/harness_orchestrator/lifecycle.py` (durable transition table), `docs/lifecycle.md`, `retention.py`.
- Audit: migration `0036_audit_hash_chain.sql` (hash chain, DB refuses UPDATE), `audit_chain.py`, `scripts/verify_audit_chain.py`.
- Events: migration `0037_collab_event_push.sql` (LISTEN/NOTIFY), WebSocket push with cursor resume, team-scoped bounded export.
- Rules: `collab.py`, `staging.py` changes; four closed-label counters in `domain_metrics.py` (section1, section4).
- Docs: `docs/audit-and-events.md` (limits in its section 8), `docs/lifecycle.md`.

## Commands and results
| Command | Result |
|---|---|
| `make pg-probe-s1` (live Postgres) | 706 passed, 1 skipped |
| `make pg-probe-migrate` | PASS, 36 migrations 0001→0037, idempotent re-run |
| section1 `pytest` (sqlite) | 681 passed, 26 skipped (live-PG-only) |
| section4 `pytest` | 833 passed, 32 skipped |
| section4 live-PG chaos + claim-cap | 6 passed |
| `ruff check src tests` sections 1, 4, 5 | clean |
| `scripts/spec_drift.py` | matches |
| `python3 deploy/pilot/run_smoke.py --otp` on the integrated tree | 1 passed (multiplayer.e2e.ts, ~45 s) |

## Defects found at integration and fixed
Counters were missing from the scraped root `/metrics`; alert label values were guesses; ruff auto-fix deleted a fixture import;
`pg-probe-migrate`/`-integration` needed manual setup; SDK specs lacked sandbox `resume`.

## Not run / limits
`make gate`, `make e2e*`, pip-audit, full compose up, `run_smoke.py --real`, Linux Landlock. Audit chain has no external anchor.
Follow-up (after this evidence): pause/resume now audited (`run.paused`/`run.resumed`, `test_pause_and_resume_routes_write_audit_rows`); the shared-run panel opens the collab socket (ticket + cursor) as a wake-up and polls every 20 s while it is up, 3 s otherwise (`collabSocket.test.tsx`, vitest 542 passed; smoke --otp 1 passed). Not proven: that the browser socket actually connected in the smoke run.
