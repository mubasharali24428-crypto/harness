# Session 2 — report (FINAL, amended 2026-10-17 by session 3)

Scope: risk areas 6–20 + adversarial review of Session 1's verified claims.

> Status: **final.** Drafting facts below were true at the drafting HEAD
> (`9f85590` + commits `69453cc`/`fe7c69c`/`afce55e`); every superseded fact now
> carries a `[AMENDED]` line. Session-3 amendment at the bottom closes the two
> items this report escalated to a design decision.

## Verified ground truth (as drafted)

- `make test` at the original HEAD was RED (time-bombed metering fixture).
  Fixed in `69453cc`; after the fix: section4 482 passed / 5 skipped.
- `make frontend` (tsc) was RED only due to stale `section7/.next` cache;
  clean after deleting that cache dir (no code change needed).
- Frontend `vitest`: 500 passed.
- The committed frontend Docker image did NOT build (area 11 defect):
  `node:crypto` import inside a `"use client"` file makes the Next.js
  webpack bundle fail with UnhandledSchemeError.
  `[AMENDED]` Fixed and committed at HEAD `923711f` (`fix(section7):
  frontend build on clean tree — drop node:crypto from client component`);
  `sha256.ts` is tracked at HEAD and `useEventStream.ts` imports it — a
  fresh checkout builds.
- Docker daemon + compose boot from `.env`: all 9 services healthy;
  migration ledger `0030/32 applied`.
  `[AMENDED]` Live re-probe 2026-10-17: 32 rows applied, max version `0033`
  (`0014` skipped by design — numbered-only checkpoints).
- Live hero journey (docs/TUTORIAL.md) reproduced end-to-end against the
  stack: login/OTP → project → plan propose/approve → start → events; the
  provider endpoint refused (Ollama not alive) and the run ended `blocked`
  with a plain-English `finalResponse` (`model qwen3.5:9b returned an
  empty response`) — the honest dead end the tutorial describes.
- `run/sandbox/meeting/…` events replay via
  `/api/v1/projects/{id}/events/replay?afterSeq=0` works (4 events for the
  drill project in correct causal order).
- Deploy backup + restore consequences (area 10) PROVEN live:
  pg_dump snapshot <1 s and `verify_restore.sh` PASS with identical row
  counts for `users/teams/projects/agent_events`.

## Species of verified/observed defects (besides "test integrity")

1. Area 12/13 (deploy/observability): compose orchestrator logged
   `domain-metrics poll failed: TypeError("'AsyncSession' object does not
   support the context manager protocol")` every 15 s — the poller used a
   sync `with factory()` on an async sessionmaker. Additionally confirmed
   unsafe in lab: per-poll `asyncio.run` against the shared engine breaks
   under asyncpg (loop-bound pooled conns) AND aiosqlite (no thread-usable
   sync facade → MissingGreenlet). Correct outcome: poll task runs on the
   app's own event loop (`fe7c69c`). Live post-fix evidence: 0 poll failures
   and `/metrics` now reports nonzero `harness_event_bus_lag_seconds` and
   a populated `harness_db_pool_checked_out_connections` histogram.
   STATUS: FIXED + live-verified.
2. Area 14 (error redaction): engine error text and the service's raw
   exception branch embedded `str(exc)` directly into `finalResponse` and
   the `ENGINE_FAILED` event payload with no scrubbing of the provider
   key (section3 has `_scrub`; the section4 engine path did not). Fixed
   with `harness_orchestrator.redact.scrub_secrets` at both engine
   exception paths and the service's `except Exception` branch (`afce55e`).
   STATUS: FIXED (2 regression tests).
3. Area 11 (clean-install/boot): frontend image build break.
   STATUS: FIXED — committed `923711f` `[AMENDED 2026-10-17]`, so the whole
   committed tree now builds from a clean checkout.
4. Area 16 (approvals/expiry): NO approval-expiry enforcement found for
   `approve_plan`/`start_execution` — an approval could be replayed at any
   time as long as the run is alive. Escalated to a DECISION.
   STATUS: **FIXED — M23-A (session 3, 2026-10-17).** TTL 24 h default via
   `HARNESS_PLAN_APPROVAL_TTL_SECONDS` (<=0 disables); `plan_approved_at`
   stamped on approve, `approvedAt` ISO added to the `plan_approved` payload;
   enforced in `state_machine.start_execution` AND durably in
   `OrchestratorService` against the event log (`PlanNotApprovedError`, 409).
   Absent/unreadable timestamp = lenient (bare-unit + scratch-mirror compat).
   No migration (event log is the witness). Evidence:
   `section4/tests/unit/test_plan_approval_ttl.py` (11 tests incl. restart
   round-trip: approve on replica A → start on replica B → aged payload →
   third replica refuses); S4 suite 503 passed / 5 skipped.
5. Area 9 (cost alerts): no `budget_warning` / 80% threshold machinery
   existed in the codebase (grep empty; only the hard budget stop).
   STATUS: **FIXED — M23-B (session 3, 2026-10-17).**
   `HARNESS_TEAM_BUDGET_WARN_PCT` default 80.0 (valid (0,100), else
   disabled); `BudgetStatus.warned`/`warnAtUsd` computed on the allowed
   branch only (a blocked decision is never also warned); service logs the
   crossing once per run (`run_data["budget_warned"]`), run-aggregate
   `budget` now carries `warned`, and `BudgetDegradedOut.warned` exposes it
   on the API. Evidence: 10 new tests in
   `section4/tests/unit/test_budget.py` (threshold matrix + service seam
   "warn but still execute"); S4 suite 503 passed / 5 skipped.
6. doc statements in `docs/runbooks/ops.md` claimed "nightly `pg_dump` cron"
   and a `walg` sidecar-serialize picture the live compose layout had
   outgrown.
   STATUS: FIXED — `ops.md` at HEAD describes the walg profile + live
   postgres:18 archiving; no "nightly pg_dump cron" claim remains (grep
   clean 2026-10-17).

## Session-1 adversarial review (as drafted, at drafting HEAD)

- R-01d (section8 `approve_pr` role-gate missing): CONFIRMED at drafting HEAD.
- R-01b (section5 sandbox team/ownership beyond role checks): CONFIRMED.
- R-01a (section3 router bearer auth missing): CONFIRMED at drafting HEAD.
- `[AMENDED]` All three are FIXED at current HEAD by the Session-1
  remediation (`deploy/runners/services.py:263-267` installs the bearer dep
  on every router route; S5/S8 team+role gates landed in the S1 wave — see
  `RISK_REGISTER.md` §SESSION 1 R-01 and `session1/S1-R01-*.md`).

## 50-risk register coverage (as drafted)

- DRILLS EXECUTED: areas 6,7,8,10,11,12,13,14,18,20 (events retention sweep
  verified from S1 evidence; hero journey + backup drills; UI flow after the
  frontend fix compiles cleanly).
- AREAS 15 (S4 alerts calibration partly performed), 19 (doc facts — fixed
  per defect 6 above), 9 + 16 (design decisions — both FIXED by M23-A/B),
  17 (support workflows ok, no findings).
- NOT STARTED: none.

## Session 2 fix commits (small & reviewable)

- `69453cc` — test-gate determinism (time-bomb / race) proof + fix.
- `fe7c69c` — deploy domain-metrics poller: rotate to on-loop task +
  regression test. Live-verified.
- `afce55e` — section4 secret redaction hybrid (engine + service) + 2 tests.

## Session-3 amendment (2026-10-17)

- M23-A (area 16) and M23-B (area 9) implemented, tested, and green:
  S4 503 passed / 5 skipped (baseline 482 + 21 new), ruff clean, focused
  files green first (43 passed). Both fixes are additive; no contracts or
  migrations touched. Full reconciliation of every cited audit item lives in
  `../RECONCILIATION_MATRIX.md`; register closure states in
  `../RISK_REGISTER.md` §"Session 2/3 closure pass".
- No commits made by this session (program rule).
