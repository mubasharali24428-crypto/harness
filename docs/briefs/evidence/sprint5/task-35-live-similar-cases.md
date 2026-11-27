# Task 35 — Live similarCases (G-E2E-5)

- Date (UTC): 2026-11-25
- Status: **PASS — fresh P1 pilot stack (PG18, real identity/orchestrator/case memory)**
- Tree: `c42f8550` + this commit's `deploy/pilot` changes (the smoke runner rebuilds
  `harness-engine-worker:p1-tests` from the working tree on every run).

## What runs where

`deploy/pilot/run_smoke.py --spec similar-cases-live.e2e.ts` (new `--spec` flag: same fresh stack,
skips the multiplayer-only post-checks). The worker is the documented **deterministic Docker
stand-in** (`deploy/pilot/worker.py`). Its new `P1_DENY` goal prints three refused shell calls as
progress records, worded exactly like `harness-shell.js`
(`Denied by the harness command policy: … [scope-request] {"path": "deploy/.env"}`), three different
commands so the repeat-loop guard does not fire first, then sleeps.

Everything after the stand-in's output is product code on real Postgres: the orchestrator's
`LoopGuard` stops the run on `denials_repeated`, kills the worker, `case_memory.recall_and_record`
stores the case (migration 0080), and the second run recalls the first.

## What the spec proves (`section7/e2e/similar-cases-live.e2e.ts`)

1. Run 1 (`P1_DENY`) → real `GET /runs/{id}/learned-rule`: `offer.failureClass == "denials_repeated"`,
   `offer.paths == ["deploy/.env"]`, `status "proposed"` (CD-02), `similarCases == []` (never itself).
2. Run 2 (same denial) → `similarCases.length >= 1`, `similarCases[0].runId == run 1`, never run 2.
3. The shipped run page for run 2 shows the lesson (rule text names `deploy/.env`) and
   "Similar past denials in this team" naming run 1.

## Run

```
section4/.venv/bin/python deploy/pilot/run_smoke.py --spec similar-cases-live.e2e.ts
P1 scratch database migrated and two engineers seeded
> playwright test similar-cases-live.e2e.ts --retries=0 --reporter=list,junit
  ✓  1 [chromium] › e2e/similar-cases-live.e2e.ts:24:5 › live: a second denial stop recalls the first one (similarCases >= 1) (8.2s)
  1 passed (11.6s)
rc=0
```

## Honest limits

- The denial **text** comes from the stand-in, not from the real shell plugin refusing a real
  command; the plugin's wording is unit-tested in section4. Guard → stop → case memory → API → UI is real.
- If the guard did not stop the run, the stand-in completes the run and step 1 fails; that path
  was not forced.
- The runner regenerates `docs/briefs/evidence/p1/browser.junit.xml`;
  reverted, not committed. Screenshot `task-35-similar-cases.png` left uncommitted.
