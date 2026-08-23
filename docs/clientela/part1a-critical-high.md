# Part 1A — CRITICAL & HIGH Registry

> Provenance: consolidated from 9 domain audits (`docs/clientela/raw-*.md`). Every entry below is evidence-based — cited from code paths observed in the reports, deduplicated across domains. Severity reflects blast radius × certainty of impact as reported.

## CRITICAL

| ID | Finding | Domain | Evidence | Impact | Recommended fix |
|----|---------|--------|----------|--------|-----------------|
| C-001 | Unverified-email team creation + legacy invite account-injection chain | Security | auth.py:25-80, app.py:197-217/327-363; require_ownership=false ships in compose | anyone with any email string owns an admin team; cross-team engineer injection without consent | enforce OTP-verified ownership by default, delete legacy direct-add path |
| C-002 | write_audit has zero production call sites | Security/Compliance | audit.py allowlist defines apikey.added etc, nothing emits | key mint/revoke, invite lifecycle, billing changes unaudited; append-only guard protects empty table | wire audit writes into every sensitive handler |
| C-003 | Budget caps inert end-to-end | Engine/Billing | cost_ledger zero writers outside S3 router; check_budget fail-open; TOCTOU non-atomic | spend limits never bind; silent overruns | wire dsh usage recording + make checks atomic + fail-closed for money paths |
| C-004 | Engine config lost on recreate + placeholder counts as live | Ops/Engine | compose defaults DEEPSEEK_API_KEY=ollama, BASE_URL=host Ollama; engine.live_mode=bool(key and model) | prod restart silently downgrades models violating no-forced-downgrade rule | required-env validation at boot, no silent fallbacks |
| C-005 | Plan gate unreachable from any UI screen | Frontend | api.ts lacks proposePlan/approvePlan/startRun; circular pointers page.tsx:108 ↔ meeting note | run can never leave idle from UI; core flow dead | add API clients + PlanGate card state-driven off status.state |
| C-006 | Login fabricates projectId/runId | Frontend | login/page.tsx:23-24 sets proj-test-1, run-timestamp | every dashboard query 404s silently; trust damage at minute one | fetch real project post-login or route to create-flow |

## HIGH

| ID | Finding | Domain |
|----|---------|--------|
| H-001 | Review page orphaned (nothing writes harness.prId) | Frontend |
| H-002 | @ai is regex echo-bot, no LLM + redundant client invokeAi | Meeting |
| H-003 | No 401 handling in apiFetch → infinite raw errors | Frontend |
| H-004 | Rate limiter wired to nonexistent route; verify-otp unthrottled | Security |
| H-005 | Master-key dev default + ALLOW_DEV=1 ship in compose | Security/Ops |
| H-006 | Forced model downgrade ladder violates AGENTS.md law | Engine |
| H-007 | done-signal: reader-role allowed, empty-diff vacuous pass, resurrects stopped runs | Engine |
| H-008 | Zombie-EXECUTING: no terminal DONE except stop; reject_plan unlogged/unpersisted | Engine |
| H-009 | Runs rows not clickable though detail page exists | Frontend |
| H-010 | Team roster read-only (no removal/role change) | Frontend/API |
| H-011 | Cost dashboard empty-state reads broken (metering unwired) | Product |
| H-012 | WS close-code 1013 zero tests; live pill one-shot REST not WS | QA/Frontend |
| H-013 | Migrations 0010-0012 never executed by tests; migrate.py untested | QA |
| H-014 | Zero E2E test of hero journey | QA |
| H-015 | NEXT_PUBLIC_* baked at build; localhost URLs break proxy/TLS deploys | Ops/Frontend |
| H-016 | DSH_WORKSPACE=/tmp ephemeral — agent work products lost on restart | Ops/Engine |
| H-017 | Pool math 105>100 connections | DB/Ops |
| H-018 | WAL-G PITR DOA (archive_command deferred) | Ops/DB |
| H-019 | Dual error envelopes + FastAPI 422 escapes error_body | API |
| H-020 | No idempotency on mutating POSTs (double key-mint/run-start) | API |
| H-021 | Rate-limit protection inversion (login throttled, run-start unlimited) | API/Security |
| H-022 | PUT /billing/plan unguarded flag-off; stripe mode silently degrades local | Billing |
| H-023 | Float-for-money drift | DB/Billing |
| H-024 | CI/local gate divergence shipped red (S10 recipe stale) | QA |
| H-025 | Dead version-pin conformance test (never collected, fails when run) | QA |

## Counts

- **CRITICAL:** 6 entries (C-001 … C-006)
- **HIGH:** 25 entries (H-001 … H-025)
- **Total registry: 31 findings** across Security, Compliance, Engine/Billing, Ops, Frontend, API, QA, DB, Product.
