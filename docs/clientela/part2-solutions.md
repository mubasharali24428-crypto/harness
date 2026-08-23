# Part 2 — Solutions Catalog (S-001–S-030)

Pre-built remediation clusters. Each entry lists what it fixes (finding refs), effort (S/M/L), priority (P0–P3), and the approach.

## Split A — Security, Engine, Frontend, API

### S-001 Enforce OTP-verified ownership on login; delete legacy direct-add invite path
**Fixes:** C-001, security C1/H3 · **Effort:** S · **Priority:** P0

Set `require_ownership=true` as the default in compose and Settings so login requires an OTP-verified owner email. Remove the legacy direct-add invite path at app.py:327-363. Add a test proving an unverified email cannot create a team.

### S-002 Wire write_audit into all sensitive handlers
**Fixes:** C-002, DB#2, hardening sweep · **Effort:** M · **Priority:** P0

Emit the existing allowlist of audit events from every sensitive handler: key mint/revoke, invite lifecycle, billing PUT, plan approve/reject/start. Add an audit-walk verifier probe confirming each sensitive action leaves an audit row behind.

### S-003 Make budgets bind: record dsh usage + atomic check + fail-closed for money paths
**Fixes:** C-003, H-022 partial · **Effort:** M · **Priority:** P0

Add a `ledger.record_usage` call in the engine completion path (S4→S3 internal API) so dsh spend actually lands in the ledger. Convert the TOCTOU-prone check_budget into a single atomic UPDATE ... WHERE cap not exceeded RETURNING statement. Billing paths fail closed when budget state cannot be confirmed.

### S-004 Engine boot validation: required env, no silent fallback
**Fixes:** C-004, H-006, ops A1 · **Effort:** S · **Priority:** P0

services.py raises at boot unless DEEPSEEK_API_KEY is a real value and the model is explicitly pinned. Remove the 'ollama' placeholder semantics entirely. Log the resolved model chain at boot so operators see exactly what will run.

### S-005 Plan-gate UI completion (PlanGate card)
**Fixes:** C-005, H-009 adjacent · **Effort:** M · **Priority:** P0

Add proposePlan/approvePlan/startRun methods to api.ts. Build a PlanGate card in WorkingArea driven off status.state, and deep-link to it from the meeting approval note so approvals surface where the work happens.

### S-006 Real identity after login
**Fixes:** C-006 · **Effort:** S · **Priority:** P0

login/page.tsx fetches listProjects post-auth and routes to project-create when the list comes back empty. Delete all fabricated identity/project fallbacks so the UI never renders invented state.

### S-007 Install rate limiter correctly + verify-otp throttle
**Fixes:** H-004, H-021, hardening · **Effort:** S · **Priority:** P0

make_app() actually calls install_rate_limit() with DEFAULT_ROUTES fixed (the auth route is /auth/request-login, not otp/request) and verify-otp added as a throttled route.

### S-008 Kill dev-key defaults in compose
**Fixes:** H-005 · **Effort:** S · **Priority:** P0

HARNESS_MASTER_KEY becomes a required env var with no default. ALLOW_DEV is permitted only behind an explicit HARNESS_ENV=dev.

### S-009 done-signal hardening: writer-role gate, idempotency, non-empty diff requirement
**Fixes:** H-007 · **Effort:** S · **Priority:** M-P1

Require a RunWriter dependency on the done-signal endpoint. Reject a second POST with 409 for idempotency. Validate diffText is non-empty before handing the run to review.

### S-010 Terminal state machine fix: reviewer_passed→DONE candidate + reject_plan event/audit
**Fixes:** H-008 · **Effort:** M · **Priority:** P1

Permit DONE via reviewer_passed only when the run is in awaiting_done_signal. On plan rejection, emit a plan.rejected event plus an audit row so terminal outcomes are fully observable.

### S-011 PR flow wiring: derive prId from pr_opened events
**Fixes:** H-001 · **Effort:** S · **Priority:** P1

WorkingArea watches the event stream and sets prId on pr_opened events instead of guessing. Runs-table rows deep-link to /review?pr=<id>.

### S-012 Real LLM behind @ai via BYO keys
**Fixes:** H-002 · **Effort:** M · **Priority:** P1

Route S6 invoke_ai through S3 call_model using the team's api_keys decrypted via KeyVault. Keep the regex-based parser as fallback when no key is configured.

### S-013 Global 401 interceptor + friendly errors
**Fixes:** H-003 · **Effort:** S · **Priority:** P1

apiFetch clears the session and redirects to /login?expired=1 on any 401. Map error codes to a human-readable message table.

### S-014 Runs table click-through + team roster management
**Fixes:** H-009, H-010 · **Effort:** S · **Priority:** P1

Link runs rows to a detail route. Add member removal and role-change endpoints plus the corresponding UI controls.

### S-015 Unify error envelope: global RequestValidationError handler
**Fixes:** H-019 · **Effort:** S · **Priority:** P1

Register a FastAPI exception_handler returning error_body() for validation failures, keeping a 422 compat field for existing clients.

### S-016 Idempotency-Key middleware
**Fixes:** H-020 · **Effort:** M · **Priority:** P1

Replay cache keyed by the Idempotency-Key header on POST /keys, /runs start, and meetings create.

## Split B — Database, Ops, Testing, DX, Business, Roadmap

### S-017 Pool sizing + PG max_connections alignment
**Fixes:** H-017 · **Effort:** S · **Priority:** P1

Set pool_size=8, max_overflow=4 per service across 7 services (≤100 total) so the sum stays under Postgres max_connections with headroom. Document the math in a services.py comment.

### S-018 PITR enablement + scheduled backups + restore drill
**Fixes:** H-018, data-infra · **Effort:** M · **Priority:** P1

Enable archive_command for point-in-time recovery. Add cron backup.sh under a compose profile. Add a quarterly restore-drill step to the runbook so backups are proven restorable.

### S-019 NUMERIC money migration
**Fixes:** H-023 · **Effort:** M · **Priority:** P2

Migration 0013 alters cost columns to NUMERIC(12,6). Switch the ORM to Decimal and update the metering tests to match.

### S-020 E2E hero-journey test + migration walk test + concurrency tests
**Fixes:** H-012..H-014 · **Effort:** L · **Priority:** P1

Playwright E2E against the compose stack covering the hero journey. A pytest migration walker that applies 0001→HEAD cleanly. Two-actor approve/reject race tests for concurrency safety.

### S-021 Proxy-ready frontend URLs + persistent workspace volume
**Fixes:** H-015, H-016 · **Effort:** M · **Priority:** P1

Introduce a runtime config endpoint or rewire NEXT_PUBLIC URLs through next.config rewrites so the frontend works behind proxies. Mount DSH_WORKSPACE as a named volume for persistence.

### S-022 WS truthfulness: live pill off WS + close-code tests
**Fixes:** H-012 · **Effort:** S · **Priority:** P2

useEventStream drives the live pill from real WebSocket state. Unit tests assert the server sends close code 1013 on overload.

### S-023 Docs truth pass + missing docs
**Fixes:** raw-12 · **Effort:** M · **Priority:** P2

Fix stale counts across the docs. Add LICENSE, CONTRIBUTING, SECURITY.md, CHANGELOG, .env.example, and a glossary.

### S-024 UI primitives a11y pass
**Fixes:** raw-17 bulk · **Effort:** M · **Priority:** P2

Create shared Pill/Button/Input primitives with aria attributes, focus rings, skip-link, and @media breakpoints; adopt them across all pages.

### S-025 Metering wire-up → cost dashboard honest
**Fixes:** H-011, C-003 adjacent · **Effort:** M · **Priority:** P1

Uses the same recording path as S-003. The cost page shows an empty-state explaining unwired mode until the first data arrives.

### S-026 Auth shim extraction + meetingId rename + timeout constants
**Fixes:** sweepD · **Effort:** S · **Priority:** P2

Move the get_principal shim into a harness_contracts.fastapi_auth factory. Rename camelCase sites to meetingId conventions. Hoist magic timeouts into named constants.

### S-027 Stripe reference adapter + flag-guard PUT
**Fixes:** H-022 remainder, business audit · **Effort:** M · **Priority:** P2

Implement the StripeClient protocol minimally (checkout + webhook reconcile). Billing PUT returns 409 when the feature flag is off.

### S-028 GDPR baseline: export + deletion cascade + policy docs
**Fixes:** business audit · **Effort:** M · **Priority:** P2

Add GET /me/export returning a JSON dump and DELETE /team cascading per the FK map. Draft PRIVACY.md and Terms of Service.

### S-029 Observability minimum: scrape metrics + alert rules + runbooks
**Fixes:** data-infra obs · **Effort:** M · **Priority:** P2

Add Prometheus and Alertmanager in a compose profile. Define 5 alert rules, each linked to its own runbook section.

### S-030 Roadmap builds (indexing/RAG, editor surface, video decision, checkpoints, rules files, notifications)
**Fixes:** part3 roadmap · **Effort:** L · **Priority:** P2-P3

Sequenced per part3 horizons; each build gets its own future mission.

