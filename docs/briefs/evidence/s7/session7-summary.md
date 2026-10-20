# Session 7 summary — integrations, governance and billing

Date: 2026-10-19. Scope: master prompt parts 49, 51 and 57. No live provider calls were made.

## Delivered

- Added migration `0051_webhook_integrations.sql`, team-admin v1 webhook configuration and delivery
  APIs, encrypted one-time secrets, an identity-only audit event projection, durable outbox, signed
  delivery, stable idempotency key, bounded retry, pause/resume/rotation/delete, and a dedicated
  `webhook-delivery` compose worker.
- Added Section 7 integrations and governance settings pages. Generated identity OpenAPI and Python/
  TypeScript SDK clients from the updated app schema.
- Added operational instructions in `docs/integrations.md`; billing boundary and known enforcement
  limits in `docs/billing-and-quotas.md`.
- Session 7 follow-up adds a step-up protected organization execution stop and admin-configurable
  team monthly cap, concurrency limit, and budget warning threshold.
- Wrote directive-level reports in `part49.md`, `part51.md`, and `part57.md`; updated matrix rows.

## Verification

| Command | Result |
|---|---|
| `section1/.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_webhooks.py tests/test_policy_gate.py tests/test_policy_api.py tests/test_billing.py tests/test_stripe_adapter.py` (from `section1/`) | 58 passed, 9 deprecation warnings |
| `section4/.venv/bin/python -m pytest -p no:cacheprovider -q tests/unit/test_budget.py tests/unit/test_tiers.py` (from `section4/`) | 45 passed, 10 deprecation warnings |
| `npm run typecheck` (from `section7/`) | passed |
| `npm test -- --run app/settings/billing/billingApi.test.ts` (from `section7/`) | 7 passed |
| `npx tsc --noEmit` (from `clients/ts/`) | passed |
| `.venv/bin/ruff check src/harness_identity/app.py src/harness_identity/audit.py src/harness_identity/audit_emit.py src/harness_identity/webhooks.py src/harness_identity/webhook_worker.py tests/test_webhooks.py` (from `section1/`) | passed |
| `ruff check src/harness_contracts` (from `contracts/`) | two findings in untouched `__init__.py` and `ratelimit.py` |

An initial test invocation used nonexistent `tests/test_billing_api.py` and exited before running
tests; it was corrected to the repository's `test_billing.py` and `test_stripe_adapter.py`.

## Matrix decisions and limits

- **49 partial.** Outbound webhook delivery is implemented and locally tested. GitHub defaults to
  `public_repo read:user`, which still grants public-repository write access; there is no issue-tracker or chat adapter, third-party extension sandbox, common
  provider health API, or automated compatibility checker. Live provider connections were not
  tested.
- **51 partial.** The governance page exposes member/role/policy/emergency/history APIs, policy
  simulation, and an audited organization execution stop. High-risk changes can require a second
  step-up protected admin; owners can shorten event/audit retention. Tenant-specific physical
  residency remains unsupported. Active runs stop cooperatively as
  workers observe the organization setting.
- **57 partial.** Local run-start tier/spend gates, configurable team monthly/concurrency
  limits, warning threshold, agent-pool reservations, atomic daily run reservations and durable
  per-run tool-call enforcement are present. In-flight engine spend remains estimate-based and
  alert delivery is absent. Stripe is intentionally out of scope (owner direction); no score cap depends on Stripe.
  Refunds and reconciliation are not part of the current pilot.
- Part 44 remains partial as recorded in Session 6.

No full Section 1/2/4/7 suite or PostgreSQL migration/smoke was run during Session 7. The prior
environment record documents unavailable local Docker/Postgres access and localhost socket
restrictions; Session 7 does not claim those checks passed. No changes were made to frozen
contracts. Unrelated dirty work was preserved.

## Session 7 completion pass

The follow-up closed two locally actionable gaps: an audited, step-up protected organization-wide
execution stop and billing-admin controls for the team monthly cap, concurrent-run limit, and
warning threshold. New requests and queue claims observe the stop immediately; active workers cancel
cooperatively when they poll the organization flag. Migration `0052_governance_and_billing_limits.sql`
adds the control and override fields. Identity OpenAPI and generated SDKs were refreshed.

| Command | Result |
|---|---|
| Section 1 billing + policy API tests | 27 passed, 34 deprecation warnings |
| Section 4 org execution gate, configurable queue caps, queue, budget and tier tests | 56 passed, 12 deprecation warnings |
| Section 1 Ruff on touched files | passed |
| Section 4 Ruff on touched files | passed |
| `npm run typecheck` (Section 7) | passed |
| `npx tsc --noEmit` (TypeScript SDK) | passed |
| `git diff --check` | passed |

Parts 49, 51 and 57 remain partial. Part 49 still needs truly read-only GitHub authorization,
provider adapters and extension governance. Part 51 still lacks tenant-specific residency. Part 57
has atomic daily run reservations and durable tool-call enforcement; in-flight engine cost remains
estimate-based and warning delivery is not wired. Stripe is intentionally out of scope. PostgreSQL
migration execution was not verified in this pass.
