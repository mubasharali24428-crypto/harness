# Session 2 — orchestrated report draft (working, not final)

Scope: risk areas 6–20 + adversarial review of Session 1's verified claims.
Working tree state at end of drafting: 3 commits on top of `9f85590`
(`69453cc` s4 test-integrity determinism, `fe7c69c` deploy domain-metrics
poller fix, `afce55e` s4 secret redaction). Session 1's in-flight edits are
untouched in the working tree and excluded from every commit made here.

## Verified ground truth at committed HEAD (independent of STATUS.md)

- `make test` at the original HEAD was RED (time-bombed metering fixture).
  Fixed in `69453cc`; after the fix: section4 482 passed / 5 skipped.
- `make frontend` (tsc) was RED only due to stale `section7/.next` cache;
  clean after deleting that cache dir (no code change needed).
- Frontend `vitest`: 500 passed.
- The committed frontend Docker image does NOT build (area 11 defect):
  `node:crypto` import inside a `"use client"` file makes the Next.js
  webpack bundle fail with UnhandledSchemeError. The operator's stashed
  `sha256.ts` + `useEventStream.ts` pair restores the build; they are
  currently restored to the working tree for local commits only.
- Docker daemon + compose boot from `.env`: all 9 services healthy;
  migration ledger at version 0030/32 applied.
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
   app's own event loop (fe7c69c). Live post-fix evidence: 0 poll failures
   and `/metrics` now reports nonzero `harness_event_bus_lag_seconds` and
   a populated `harness_db_pool_checked_out_connections` histogram.
2. Area 14 (error redaction): engine error text and the service's raw
   exception branch embedded `str(exc)` directly into `finalResponse` and
   the `ENGINE_FAILED` event payload with no scrubbing of the provider
   key (section3 has `_scrub`; the section4 engine path did not). Fixed
   with `harness_orchestrator.redact.scrub_secrets` at both engine
   exception paths and the service's `except Exception` branch (afce55e).
3. Area 11 (clean-install/boot): frontend image build break (fix restored
   from the operator's stash; needs a real commit decision).
4. Area 16 (approvals/expiry): NO approval-expiry enforcement found for
   `approve_plan`/`start_execution` — an approval with no signed-out plan
   TTL could be replayed at any time as long as the run is alive. Needs a
   DECISION before implementation because approval semantics are review
   territory (this is an ask for the orchestrator owner, in my lane).
5. Area 9 (cost alerts): no `budget_warning` / 80% threshold machinery
   exists in the codebase (grep is empty; only hard budget stop exists).
6. doc statements in `docs/runbooks/ops.md` claim "nightly `pg_dump` cron"
   and a `walg` sidecar-serialize picture that the live compose layout has
   already outgrown: the live db now archives WAL itself on postgres:18;
   cron still absent locally (area 19 doc facts fix, my lane).

## Session-1 adversarial review (claims verified against code, HEAD)

- R-01d (section8 `approve_pr` role-gate missing): CONFIRMED at HEAD.
- R-01b (section5 sandbox team/ownership beyond role checks): CONFIRMED
  at HEAD.
- R-01a (section3 router bearer auth missing on runway endpoints): 
  CONFIRMED at HEAD (`simple_app(router, "harness-router")`, no bearer
  dependency), deployed compose exposes :8003 publicly today — the fix in
  their tree (`services.py`) perspectives in tests... (S1 work, not mine)

## 50-risk register coverage (drafting percentages)

- DRILLS EXECUTED TODAY (mine): areas 6,7,8,10,11,12,13,14,18 (events
  retention sweep verified from S1 evidence),20 (hero journey + backup
  drills; UI flow after the frontend fix compiles cleanly).
- AREAS 15 (S4 alerts calibration partly performed), 19 (doc facts), 9
  (budget alerts missing — a design decision), 16 (plan approval expiry —
  needs design decision), 17 (support workflows ok, no findings).
- NOT STARTED in this session (did not touch): none; area 15 alerting
  threshold config only exists as ENV knobs.

## Session 2 fix commits (mine only, small & reviewable)

- 69453cc — test-gate determinism (time-bomb / race) proof + fix.
- fe7c69c — deploy domain-metrics poller: rotate to on-loop task +
  regression test. Live-verified.
- afce55e — section4 secret redaction hybrid (engine + service) + 2
  tests. (uncommitted change pending review files added in this session:
  docs/pilot-remediation/session2/*)
EOF
wc -l "/Users/user/untitled folder 2/harness/docs/pilot-remediation/session2/README.md" 2>/dev/null; ls "/Users/user/untitled folder 2/harness/docs/pilot-remediation/" 2>/dev/null