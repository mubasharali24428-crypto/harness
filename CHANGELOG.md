# Changelog

All notable changes to the AI Engineering Harness are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
entries below are backfilled from `STATUS.md` + git history (Mission 12 G4
docs-truth lane, 2026-08-23). Migration numbers refer to files in
`section1/migrations/`.

## [Unreleased]

### Mission 12 · Wave 2.1 (2026-08-23)

Baseline `ebdfeb7`; campaign graph `docs/briefs/mission12-wave2-graph.md`.

#### Added

- **E1** — `@ai` meeting invocations call real LLMs through Section 3
  (`call_model`) using team BYO keys; KeyVault decrypt happens server-side;
  the regex stub engine remains only as an honest fallback that says so when
  no key is configured.
- **E2** — Frontend 401 interceptor clears the session and routes to
  `/login?expired=1`; sign-out wipes every `harness.*` storage key; backend
  error codes map to human-readable messages instead of internal jargon.
- **E3a/E3b** — Roster management API (`DELETE`/`PATCH` members; admin-only,
  last-admin guard → 409 `LAST_ADMIN`, cross-team → 404 without an existence
  oracle; audited `member.removed`/`member.role_changed`) plus Runs rows
  click-through and roster UI consuming it.
- **E4a/E4b** — PR-open persists `pr_id` on the run (migration **0014**) and
  emits `pr_opened {runId, prId, url}`; minimal `GET /runs/{id}` aggregate;
  Review page consumes `?pr=` and subscribes to `pr_opened` with an honest
  empty state.
- **F1** — Global `RequestValidationError` handler returning the unified
  `{"error":{code,message}}` envelope (detail mirrored for back-compat);
  `Idempotency-Key` middleware on mutating POSTs (migration **0015**; pilots:
  keys, run start, meetings).
- **F2** — Optimistic concurrency for plan decisions: `agent_plans.version`
  column (migration **0013**, slot re-assigned after the M11 collision),
  conditional `UPDATE … WHERE version=:seen`, mismatch → 409
  `PLAN_ALREADY_DECIDED`.

#### Fixed

- Duplicate `invokeAi` client calls removed (single invocation per `@ai`).

## Mission 11 · Wave 1 (2026-08-23)

CLIENTELA remediation: six CRITICALs + flow-blocking HIGHs. Baseline
`d5d9619`, seven lanes, closed at `b255ce2` with all verifier verdicts READY
and CI green.

### Security

- **C-001/H-004** — Ownership proof enforced by default everywhere
  (config, compose, deploy services): unverified email can no longer mint an
  admin team; plain login joined the DB-backed protected set; legacy
  direct-add invite endpoint removed (`f04650c`, `61cb1a9`); verify-OTP
  throttled, OTP codes single-use.
- **C-002** — Audit emission wired at every sensitive call site (API keys,
  invites, billing): never-throw, secret-scrubbing typed helpers
  (`audit_emit.py`) (`7a6b2b3`).
- **C-003** — Atomic budget enforcement: fail-closed reserve/release guard +
  `cost_ledger` writer, proven under an 11-test concurrency suite
  (`7cc21f8`).
- **C-005** — Plan-gate orchestrator API per the frozen pair contract —
  propose/approve/reject with role gates (admin/engineer decide;
  viewer/guest → 403), idempotent approve, audited reject (`281d769`) — and
  the PlanGate UI consuming contract DTOs field-by-field (`c2b487c`).

### Changed

- **C-004/H-005/H-006** — Engine boot integrity: compose engine env block
  stripped of placeholder/dev defaults; unset `ENGINE_LIVE_MODE` is now a
  loud boot failure (explicit `live|dry` enum); silent provider/model
  downgrade ladder removed (`db9e333`, `65525b2`).
- **H-007/H-008** — Done-signal & terminal-state hygiene: role-gated
  done-signal requires a non-empty diff and refuses resurrection;
  `reviewer_passed → DONE` is terminal (`608f0ce`).

### Fixed

- Rate limiter was wired to a route that never existed (`/auth/otp/request`);
  moved onto the real login/OTP routes (`dc9b75f`).
- CI lint: `LiveMode` → `StrEnum` (ruff UP042) and import sort (I001)
  (`70532c0`).

## Mission 10 (2026-08-23)

ALEX_TEAM Phase 2 completion + Phase 3 edge-hardening begins. Baseline
`abcab51`; mission closed with all CI jobs green (`602c505`).

### Added

- **BYO keys** — `/api/v1/keys` CRUD over the frozen `api_keys` table:
  KeyVault-encrypted at rest, hint-only exposure, idempotent soft revoke,
  foreign-team requests 404 with no existence oracle; Settings → Keys page.
- **Cost dashboard** — `/dashboard/cost` over live `/spend`: range selector,
  totals cards, by-model bars, SVG sparkline, honest empty state.
- **Run history** — team-scoped `GET /api/v1/orchestrator/runs` with
  server-driven paging (limit ≤ 100) + filters; Runs page with status pills
  and relative times.
- **Billing** — `/api/v1/billing/state|plan` behind `HARNESS_BILLING`;
  local quota from `cost_ledger` vs `teams.budget_usd` (migration **0010**);
  Stripe adapter is an injectable protocol (no package); Billing settings
  page with meter.
- **Onboarding revival** — checklist API derives completion from real state
  only; interactive onboarding page with progress.
- **WAL-G PITR tooling** — `deploy/walg` scripts + opt-in compose profile +
  Dockerfile; shipped as reviewed-but-unexercised scaffolding (no S3 creds
  in build env — honesty note in `docs/runbooks/backup-and-pitr.md`).
- **Rate limiting** — fixed-window Postgres limiter on login/OTP routes
  (migration **0011**): atomic upsert, 429 + `Retry-After` envelope; DB
  failure fails OPEN.

### Fixed

- CI red on a missing checklist golden fixture (`582c044`); gitleaks
  allowlist for the fake test key.
- Compose `CORS` env shape: origins parser now accepts both the JSON-array
  form and comma-separated lists (`96c1ffb`).

## Wave 9 (2026-08-23)

Phase 2 SaaS core: metering, budgets, orgs, notifications, security headers.
Baseline `6d5e52c`; three first attempts died to provider outages with zero
tree writes and were re-dispatched; every node independently re-verified.

### Added

- **Metering** — `cost_ledger` gains real writers: usage recorded on every
  successful `call_model`; `GET /api/v1/models/spend` aggregates
  totals/byModel/byDay; models service boots an asyncpg ledger pool and
  drains pending writes on shutdown.
- **Budgets** — spend cap checked pre-spawn and on reviewer model calls;
  precedence `teams.budget_usd` (migration **0007**) >
  `HARNESS_TEAM_BUDGET_USD` > unlimited; over-cap produces an honest blocked
  event.
- **Orgs/invites** — `team_invites` (migration **0008**) with hashed tokens,
  admin-only mint/revoke, atomic redeem (404/409/410), members roster; team
  settings page with roles, invite form, revoke, join-by-token.
- **Notifications** — `notification_outbox` (migration **0009**) with
  templates for `run_finished`/`review_failed`/`pr_opened`; worker drains
  with max 3 attempts; dev mode is offline-green; EventBus listener tap is
  disclosed and exception-guarded so notifications can never break event
  publishing.
- **Security headers** — pure-ASGI middleware on S1/S4/S5/S6/S8
  (nosniff/DENY/referrer everywhere; HSTS only behind `HARNESS_TLS_ENABLED=1`;
  report-only CSP on HTML); CI gained a security-scan job (pip-audit WARN +
  gitleaks).

### Fixed

- Merge wiring caught live: double rate-limiter install turned 6 tests red —
  limiter moved to assembly level, suite green.
