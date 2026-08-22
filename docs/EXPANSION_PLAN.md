# EXPANSION PLAN — Harness → Startup-Level Platform (wave 6+)

**Status:** APPROVED 2026-08-22. Product shape: **cloud-first SaaS, self-host later**
(every cloud feature flag-gated; Postgres-only core; no hard cloud deps).
Companion evidence: `docs/GAP_ANALYSIS_AND_FIXES.md` (waves 1–5),
backend capability map (session analysis, 2026-08-22), STATUS.md.

---

## 0. Verified baseline (what analysis + testing found)

### What already works (keep, don't break)
- **Event log as source of truth**: `agent_events` + EventBus persistence-first,
  pg_notify cross-instance fanout (migration 0003), replay/afterSeq/seq-gap healing.
- **Frozen contracts discipline**: pydantic → TS mirror codegen with CI drift gate.
- **Human-gated state machine**: IDLE→PLANNING→AWAITING_PLAN_APPROVAL→EXECUTING⇄PAUSED→
  REVIEWING→BLOCKED/PR_OPENED/DONE (`section4/state_machine.py`) — the governance core.
- **Auth done right in S1/S2/S4**: bearer deps, one-time WS tickets, OTP ownership flow,
  GitHub OAuth web flow with encrypted tokens, fail-closed 503 semantics.
- **Verification spine**: `integration/test_checkpoint1.py` proves protocol conformance,
  exact event sequences, rehydration-from-log, cross-instance fanout.
- **Tests green at plan time**: 406 pytest + 4 PG integration + 58 vitest; ruff clean;
  live compose stack healthy on all 7 ports; static gates pass.

### Defects found by live smoke test + source audit (Phase 0 targets)
| # | Finding | Evidence |
|---|---------|----------|
| D1 | Compose default model `qwen3-abliterated-128k:latest` does not exist locally (available: `qwen3.5:9b`, `qwen3.5-9b-128k:latest`) | `deploy/docker-compose.yml:34`, Ollama `/api/tags` |
| D2 | Engine returns `status=completed` with **empty** finalResponse against missing model — no exception ⇒ fallback chain never triggers ("fake success") | live run smoke-run-1, `engine.py` result path |
| D3 | Event publish race under fast successive actions: `execution_started`(37) sequenced before `plan_approved`(38) — fire-and-forget publishes not serialized per run | DB trail of smoke-run-1 |
| D4 | Engine completion/failure emits **no event**, never advances state machine; UI must poll `GET /runs/{id}/engine`; `engine_result` is memory-only | `service.py:_execute` |
| D5 | S4 has **no team-ownership check** on runId/projectId and **no role check** on approve (`roles.can()` never called anywhere) | backend capability map §5 |
| D6 | S5 sandbox API fully unauthenticated; S8 auth dep wired but consumed by no route (reviewer id from header/body); S6 unauthenticated with forgeable userId | capability map §11 |
| D7 | Real model calls bypass S3 router entirely; `cost_ledger`/`api_keys`/`model_configs` orphaned (zero writers); no budget caps | capability map §12 |
| D8 | Run execution is in-process asyncio only: no durable queue, no concurrency cap, engine thread not interruptible on pause/stop, credential swap via process-global env mutation (thread-unsafe) | `service.py:287-294`, `engine.py:125-144` |
| D9 | Single container runs 7 uvicorns behind a stop-the-world watchdog; WS tickets/OAuth state/rate-limit buckets are per-process memory | `entrypoint.sh`, capability map §1 |

### Stale docs to reconcile
GAP Part 10 rows H2r/M4/M5/E-cat/E-codegen marked open but shipped; Appendix A lists 10
of 12 event types; Appendix D "ticket (planned)" row; frontend `onboarding.ts` dead code.

---

## 1. Product definition

**Offering:** hosted AI engineering team — connect a GitHub repo, meet/brief the agent
team, approve a costed plan, watch execution stream live in an OS-confined sandbox,
gate the result through AI review, receive a draft PR. Human approves; nothing auto-merges.

**Self-host-later rules (binding):**
1. Core stays Postgres-only; cloud services (Stripe, Sentry, S3, SMTP) enter behind
   settings flags with local/dev fallbacks.
2. No feature may assume exactly-one-replica after Phase 1 (state to DB or Redis).
3. Deploy-shape-agnostic packaging: same image, `SERVICE=<name>` entrypoint selection.

---

## 2. Phases

### Phase 0 — Correctness & security debt (this wave)
- **P0.1** Fix `DSH_MODEL` default drift (D1). Compose default → `qwen3.5-9b-128k:latest`.
- **P0.2** Empty-completion guard (D2): engine treats empty/whitespace finalResponse as
  failure → falls through fallback chain → honest error. Test pins it.
- **P0.3** Per-run publish ordering (D3): serialize `bus.publish` per run_id inside S4's
  `_emit` path so causal order matches API call order. Regression test.
- **P0.4** Engine lifecycle events (D4): additive contract EventTypes `engine_completed`,
  `engine_failed` (contracts bump 0.2.0 → 0.3.0 minor, TS mirror regenerated SAME commit,
  conformance pin updated), `_execute` emits via bus, state machine advances
  EXECUTING→AWAITING_DONE_SIGNAL on completion / BLOCKED on failure. Tests.
- **P0.5** S4 guards (D5): every run route verifies `agent_runs.project_id` belongs to
  `principal.teamId`; approve/reject enforce `can(principal, "approve_plan")`.
  Propose rejects projectId from another team. Tests for each 403.
- **P0.6** Auth wiring (D6): deploy assembly wires `make_bearer_dep` into S5/S6/S8 apps
  (fail-closed pattern from GAP-F2); review routes consume the principal; meeting userId
  taken from principal. Unit-test contexts stay direct-service (no wiring) as today.
- **P0.7** Ledger/doc hygiene: GAP Part 10 strike-throughs, Appendix A → 14 types,
  Appendix D ticket row, STATUS.md wave 6 entry.
- **P0.8** Full verification sweep + single wave-6 commit.

### Phase 1 — Production architecture (scale-out ready)
- Split containers: one image, `SERVICE=name`; reverse proxy (TLS, same-origin /api/v1,
  WS passthrough); `deploy/compose.prod.yml`; NEXT_PUBLIC build args fixed.
- Cross-process state → DB: `oauth_states`, `ws_tickets` (or reuse), rate-limit buckets
  table or Redis flag; keep pg_notify bus.
- Durable run execution: `run_claims` queue table (SKIP LOCKED), orchestrator worker
  loop, author the **checkpoint-aware resume design** (Appendix C intent → spec:
  engine progress persisted as events; resume rehydrates instead of restarts); bounded
  per-team concurrency; replace env-mutation credential swap with explicit per-call creds.
- Observability: structured JSON logs + request IDs, Prometheus `/metrics` per service,
  OpenTelemetry traces across S4→S5→S3 calls, Sentry hook (flag).
- Data ops: `agent_events` retention/partition job, PG backup/PITR runbook, migration
  dry-run + fresh-boot smoke in CI.
- Secrets: real master key profile, drop dev-key escape hatch in prod compose.

### Phase 2 — SaaS core
- Wire orphaned tables: cost metering writes `cost_ledger` from real model calls
  (S3 becomes the choke point OR SDK wrapper reports usage), spend aggregation endpoint,
  per-team budget caps enforced pre-call, BYO keys via `api_keys`/`model_configs`.
- Org management: invites, roles UI (admin|engineer|viewer|guest enforced server-side
  everywhere), team settings, audit viewer.
- Billing (flag): Stripe subscriptions + metered overage from cost_ledger; quotas.
- GitHub App installation flow alongside OAuth app; webhook ingestion → auto-plan.
- Notifications: email templates via existing notifier hook (run finished / review
  failed / PR opened), optional webhook.
- Frontend surface: projects list/create/settings, run history index (+ list-runs API),
  team page, billing page, cost dashboard, onboarding pages (un-dead `onboarding.ts`),
  httpOnly-cookie session option behind flag; unify design tokens.

### Phase 3 — Launch readiness
- Playwright e2e: login→project→brief→run→review→settings against compose; CI job.
- Edge rate limits + CSP + dependency/secret scanning in CI.
- Load test profile (extend section stress suites → k6 scripts) with SLOs:
  p95 WS delivery < 500 ms, N concurrent runs per team without starvation.
- Marketing site + status page + user guide + API reference (openapi.json per section)
  + self-host quickstart.
- Real credentials cutover: GitHub OAuth app, SMTP provider, HARNESS_REQUIRE_OWNERSHIP=true.

### Phase 4 — Differentiators (post-launch)
Standalone PR-review bot mode (S8 as GitHub App), fork/replay-to-new-run, scheduled
runs, Slack app, standup bot (S6), third-party provider plugins via frozen contracts.

---

## 3. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Contract additions break consumers | Additive-only enum growth; mirror regen + mocks same commit; golden fixtures updated |
| Auth wiring breaks unit tests | Follow F2 pattern: wire at deploy assembly only; tests override/skip principal |
| Engine behavior changes break conformance sequence | Update integration expected-sequence deliberately; keep old types intact |
| Scope creep across 10 sections | Wave commits, ledger discipline, tests green before any PR |
| deepseek-harness-sdk pinned old (0.1.1rc1) | Re-verify on bump per AGENTS.md; isolate behind wrapper |

## 4. Acceptance criteria (per phase exit)
- P0: all suites green incl. new regression tests; live stack re-smoked: correct model
  name executes non-empty; events ordered; engine lifecycle events visible in replay;
  foreign-team run access → 403; unwired-token request to S5/S6/S8 → 401/503.
- P1: stack runs split-container behind proxy; kill -9 worker mid-run → run resumable;
  oauth/ticket redemption works across replicas; metrics scraped.
- P2: end-to-end paid-flow fixture test; invite→role→approve chain proven; spend visible.
- P3: e2e green in CI; load profile meets SLOs; docs published.
