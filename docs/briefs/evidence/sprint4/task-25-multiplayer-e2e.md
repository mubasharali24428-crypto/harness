# Task 25 — Multiplayer pilot e2e on a live stack (G-E2E-2)

- Date (UTC): 2026-11-24
- Status: **PASS** — engineering evidence only; P-D7 stays DEFERRED
- SHA at verification: `49445e4f`

## Command

```sh
cd /Users/user/untitled folder 2/harness
section4/.venv/bin/python deploy/pilot/run_smoke.py
# rc=0
```

`run_smoke.py` checks ports 8101/8104/8105/3107 are free, builds `harness-engine-worker:p1-tests`,
starts a disposable PG18 with its own container, runs `deploy/pilot/stack.py init` (real migration
ledger + two seeded engineers in one team), starts identity/orchestrator/sandbox + Next dev, waits
for health, then runs the spec and cleans up its children. No existing stack was stopped.

## Output (trimmed)

```
P1 scratch database migrated and two engineers seeded
> playwright test multiplayer.e2e.ts --retries=0 --reporter=list,junit
Running 1 test using 1 worker
  ✓  1 [chromium] › e2e/multiplayer.e2e.ts:34:5 › P1 shared pilot: create → join →
      redirect → handoff → approve/apply → stop (33.7s)
  1 passed (37.1s)
```

## What this closes / does not close

- Closes: "multiplayer.e2e.ts never run" — it ran green on a real, freshly migrated stack with two
  browser contexts and the documented deterministic stand-in worker (no API mocks in the spec).
- Does not close: Gate 6 / B-09 build authorization. P-D7 remains **DEFERRED**; invite → revoke with
  two differently skilled members is still absent. The B-09 checklist was updated with the
  passed-at-SHA row, not a gate verdict.

## Side effects (not committed)

The smoke runner regenerated `docs/briefs/evidence/p1/browser.junit.xml`,
`docs/briefs/evidence/p1/live-result.json`, `docs/briefs/evidence/p1/multiplayer-complete.png`
and `docs/briefs/evidence/p3/pilot-metrics-synthetic-run.json`. They are generated artifacts and are
deliberately left out of this commit, as with previous sprints' screenshots.
