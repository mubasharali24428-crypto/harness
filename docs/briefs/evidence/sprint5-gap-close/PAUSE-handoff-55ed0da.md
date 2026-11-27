# PAUSE handoff — Mac truth lane at `55ed0da`

- **Paused (UTC):** 2026-11-25 ~13:05 (owner request)
- **Branch:** `w28-s2-sprints1-4` @ `55ed0da` (Task 42 D2 soft-remove pushed)
- **Verdict:** NOT READY (unchanged)

## DONE before pause

| Step | Result |
|------|--------|
| Checkout / `git pull --ff-only` | **OK** — HEAD `55ed0da` |
| PG soft-remove tests | **PASS** — `HARNESS_TEST_PG=1 pytest tests/test_member_soft_remove_pg.py -q` → **3 passed** (ran twice on pause day) |
| Cloud gap-close through Task 42 | On origin — see `task-41` … `task-42`, `task-44`, `task-45a`, `task-49`, END addendum |

## PAUSED / not completed

| Step | State | Notes |
|------|--------|--------|
| `make gate` (full) | **STOPPED** | Started ~12:45 UTC (pid chain from `make gate`); still in **section1** full pytest after ~20+ min wall; **SIGTERM** sent on owner pause — **no exit code recorded** |
| Task 43 — `invite-revoke-live.e2e.ts` | **NOT RUN** | `test.fail(true, …D2…)` **still present** at `55ed0da`; stack on host had `harness-*` containers but not rebuilt/verified at this SHA |
| Task 45b — readiness manifest | **NOT RUN** | Per `task-45-readiness-drift.md`: redeploy RC + `make readiness-manifest` only after deliberate owner window; expected schema head **0082** post–Task 42 |
| `mac-gate-55ed0da.md` | **NOT WRITTEN** | Superseded by this pause file until gate/e2e complete |

## Known flake / pre-existing (not attributed to Task 42)

- Cloud Task 49: **8** `test_runs_index_tuning` errors on sqlite lane **before** Task 42 — re-check on Mac full gate when resumed.

## Resume commands (owner Mac, repo root)

```sh
cd "/Users/user/untitled folder 2/harness"
git fetch origin && git checkout w28-s2-sprints1-4 && git pull --ff-only
git rev-parse HEAD   # expect 55ed0da or newer Task 43/45 commits

# quick confidence
cd section1 && HARNESS_TEST_PG=1 python -m pytest tests/test_member_soft_remove_pg.py -q

# full gate (allow ~10–15 min; ensure no duplicate pytest from other terminals)
make gate
echo "make gate rc=$?"

# Task 43: drop test.fail in section7/e2e/invite-revoke-live.e2e.ts after confirming stack at HEAD
# Rebuild compose if needed, then:
# cd section7 && E2E_BASE_URL=http://localhost:3010 E2E_API_URL=http://localhost:8001 \
#   E2E_ORCHESTRATOR_URL=http://localhost:8004 npx playwright test e2e/invite-revoke-live.e2e.ts --retries=0

# Task 45b: see task-45-readiness-drift.md (redeploy harness-rc, then make readiness-manifest)
```

After green gate + e2e + optional manifest: write `mac-gate-<shortsha>.md`, commit per task, push `w28-s2-sprints1-4`.