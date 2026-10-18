# Checkpoint — Session 2 (INTERIM, lane A pending): pilot residuals + sandbox fixes (2026-10-18)
1. Objective: NOT complete. Done: baseline cleanup, sandbox T15-T17/T7b, shared UI + Playwright legs, alerts/rollback/runbook. Pending lane A: lifecycle (10,22,23), audit chain/WS push/metrics (11), approval rules (05-08); acceptance map (27) has PENDING-LEAD rows.
2. Verified (paths): `evidence/s2/lane-b.md`, `lane-c.md`, `lane-d.md`; `docs/sandbox-isolation.md`; `docs/runbooks/pilot.md`; `deploy/observability/alerts.yml`.
3. Changed: engine.py EPERM fix (uncommitted, section4/src shared with lane A), Makefile pg-up, section5 sandbox, section7 UI, run_smoke.py, alerts. Migrations: none by me; lane A may add 0036+.
4. Commands run (by me): `make pg-probe-s1` 662 passed; section5 230 passed 1 skipped; vitest 540 passed; tsc clean; promtool check config 15 rules; flag-rollback 8 passed; alert-load+observability 26 passed 1 skipped; claim/stop tests 3/3 and 5/5. Lane C ran `run_smoke.py --otp` (1 passed, twice). Not run by me yet: smoke after integration, `make gate`, `make e2e*`, pip-audit, `--real`.
5. Blockers: lane A unreported; major: pilot counters (harness_approval_denied_total etc.) absent so pilot alerts are silent; sdk-gen sandbox spec lacks `resume`.
6. Risks: Landlock path unrun; `confinement=off` no boundary; no Alertmanager; backup/WAL alerts inert; UI lacks proposal file/amend and decision timestamp.
7. Decisions: commits made (5, no push, see git log); `HARNESS_PILOT_TEAMS` empty = open to all teams (rollback needs non-empty list without the team).
8. Agents: B, C, D done and committed; A running. Session 1: baseline, contracts, ops, threat model (carried).
9. Next: integrate lane A, reconcile alert label regexes, spec_drift --write, one smoke run, finish rows 01,02,05-08,10,11,22,23,27, commit lane A.
