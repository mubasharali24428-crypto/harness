# Checkpoint — Session 2: pilot residuals + sandbox fixes (2026-10-18)
1. Objective: done for lane A/B/C/D deliverables; parts 05-08, 10, 11, 22, 23, 27 now `verified` in the matrix. Parts 01, 02, 12, 24, 32 unchanged (see below).
2. Verified (paths): `evidence/s2/{lane-a,lane-b,lane-c,lane-d,part05-gap-checklist,acceptance-core-flow}.md`; `docs/audit-and-events.md`; `docs/lifecycle.md`; `docs/sandbox-isolation.md`; `docs/runbooks/pilot.md`.
3. Changed: lifecycle table, audit hash chain (migrations 0036, 0037), WS push, approval rules, domain metrics wired to root `/metrics`, alert labels reconciled, sandbox T15-T17/T7b, SDK specs regenerated.
4. Commands run (by me): `make pg-probe-s1` 706 passed; `make pg-probe-migrate` PASS 0001→0037; section1 681 passed; section4 833 passed; live-PG chaos 6 passed; section5 230 passed; vitest 540; tsc clean; ruff clean s1/s4/s5; `run_smoke.py --otp` 1 passed on the integrated tree. Not run: `make gate`, `make e2e*`, pip-audit, `--real`, Landlock.
5. Blockers: none for Session 3; UI still polls (WS push unused by UI).
6. Risks: audit chain has no external anchor; no Alertmanager; `confinement=off` has no boundary; pause/resume not audited; part 05-002 live presence missing.
7. Decisions: `HARNESS_PILOT_TEAMS` empty = open to all teams; commits local, no push.
8. Agents: A, B, C, D done and verified by lead.
9. Next: UI consumes WS push; decision timestamp/amend event in approvals API; Session 3.
