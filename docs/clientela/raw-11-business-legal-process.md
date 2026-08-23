Audit complete. All evidence gathered read-only from `/Users/user/untitled folder 2/harness` (billing.py, notifier.py, audit.py, models.py, migrations, section4 budget.py, section3 ledger/api.py, section7 UI, deploy/, docs/, git metadata). Findings below — 76 one-liners across areas 29–40.

# Audit Wave 2 — Business/Legal/Process Domains (areas 29–40)

## 29–31. Billing/Payments
- [billing] `StripeClient` is an uninstantiated Protocol — no adapter exists anywhere in repo; `HARNESS_BILLING=stripe` + key silently degrades to `"local"` mode → ship a reference adapter or fail loudly at boot instead of degrading.
- [billing] `PUT /api/v1/billing/plan` has **no flag check** (only GET does) — plan mutations succeed and persist while billing is flag-off → guard PUT with `_billing_mode()` and 409 honestly when disabled.
- [billing] No Stripe webhook receiver (`customer.subscription.*`, `invoice.paid/failed`) — local `billing_plans` row stays system-of-record and drifts from provider forever → add webhook ingest + reconciliation.
- [billing] No dunning/retry logic — failed payment has no retry schedule, no grace period, no downgrade path → add dunning state machine.
- [billing] No proration — mid-cycle upgrade/downgrade applies instantly and free; no credit/proration math → prorate on plan change or block until cycle end.
- [billing] No refund workflow (API/UI/policy) → define refund path before charging anyone.
- [billing] No invoice generation or receipts; nothing emails customers about charges → generate invoices per cycle from cost_ledger.
- [billing] No checkout flow at all — nothing collects a payment method, so paid plans are honor-system strings → add checkout session creation once Stripe lands.
- [billing] No tax/VAT handling, no billing address, no EU VAT reverse-charge story → integrate tax provider or geo-block paid plans.
- [billing] Quota enforcement split-brain: orchestrator `check_budget()` reads only `teams.budget_usd`/env default — never `billing_plans.monthly_budget_usd`, so admin-set plan budgets never gate runs → unify cap resolution.
- [billing] New `BillingPlan` rows default `monthly_budget_usd=0`; `max(plan,override)` yields $0 budget display for teams with neither → map tiers to real default budgets.
- [billing] Plans are bare strings — free/pro/team have zero behavioral differences anywhere in code → encode per-plan limits and enforce them.
- [billing] Downgrade mid-cycle needs no confirmation and has no data-preservation/over-limit policy → add confirmation + grace semantics.
- [billing] No idempotency/replay protection on billing mutations (PUT is a blind upsert) → add request idempotency keys.
- [billing] Stripe adapter exceptions swallowed into silent local fallback — billing outage invisible to operators → alert on degrade path.
- [billing] No seat licensing: "team" tier untied to `TeamMember` count; free gets unlimited members/projects/runs → tie limits to plan.
- [billing] No metered overage despite EXPANSION_PLAN.md:101 promising it from cost_ledger → implement or descope explicitly.
- [billing] No billing change history: `audit_log.APPEND_ONLY_ACTIONS` excludes plan changes; only `updated_at` remains → audit plan mutations.
- [billing] `STRIPE_API_KEY` read per-request from env with no boot-time validation warning when set but adapter absent → validate config at startup.
- [billing] USD-only `Numeric` columns, no currency field → add currency column before multi-currency becomes a migration nightmare.

## 30. Pricing/Packaging
- [pricing] No pricing page or tier-comparison surface anywhere in section7 UI → build /pricing with feature matrix.
- [pricing] `/settings/billing` renders plan names as bare `<option>` text — no prices, features, or limits displayed → show tier value props.
- [pricing] Free-tier limits not enforced anywhere (unlimited projects/runs/invites on every plan) → enforce concrete free caps.
- [pricing] No trial mechanics (no trial flag, expiry, or conversion tracking) → add time-boxed pro trials if selling.
- [pricing] Feature gating absent — no dependency/middleware ties any endpoint to a plan → add plan-gate dependency.
- [pricing] Flag-off upsell card says "ask your deployment operator" — no self-serve upgrade path exists even conceptually → define upgrade journey.
- [pricing] `seed_demo.py` seeds no billing/demo plan data — sales demos cannot showcase tiers → seed example plans/budgets.

## 31. Privacy/GDPR
- [privacy] Zero consent management — no consent capture, records, or withdrawal anywhere → add consent model + UI.
- [privacy] No data-export endpoint (Art. 15/20 portability) → add authenticated JSON export of user's teams/projects/messages/runs.
- [privacy] No account deletion / right to erasure — no `DELETE /users/me`, not even soft-delete via `is_active` → implement deletion workflow.
- [privacy] Team deletion cascades missing: 26 FK `REFERENCES` in `0001_initial.sql`, zero `ON DELETE` clauses (only outbox uses SET NULL) — deleting a team orphans everything → add cascade migration.
- [privacy] Dev notifier prints recipient email + live OTP code to stderr/logs (`docker logs`) — PII + credential in logs → redact in prod mode.
- [privacy] `rate_buckets.bucket_key` persists raw client IPs (`"<ip>|<route>"`) with no purge job or retention period → TTL-sweep old windows, document retention.
- [privacy] `login_otps` keeps plaintext email past expiry; no cleanup of expired rows → purge job + documented retention.
- [privacy] `notification_outbox` retains `to_email` + payloads forever including failed/sent rows → archive/purge policy.
- [privacy] No privacy policy, ToS, DPA, or cookie notice files exist anywhere in repo → author legal docs before external users.
- [privacy] No DPA prerequisites: no subprocessor list (SMTP relay, Stripe, GitHub, model providers), no data-inventory/data-map doc → produce both.
- [privacy] `audit_log.detail` is free-form JSON with no PII constraints or retention rule → schema-constrain + retention policy.
- [privacy] No retention schedules for `agent_events`, meetings/messages, cost_ledger (EXPANSION_PLAN.md:91 admits retention job unstarted) → implement partition/purge jobs.
- [privacy] GitHub tokens encrypted but no key-rotation procedure documented (crypto.py undocumented for assessors) → document envelope format + rotation runbook.
- [privacy] Sessions of removed users persist — no revocation on offboarding; `AuthSession` rows survive indefinitely → revoke sessions on member removal.
- [privacy] ONBOARDING.md advertises `HARNESS_TELEMETRY_FULL=1` telemetry toggle but **no code reads that variable** — false disclosure → implement or remove the claim.
- [privacy] No DPO/privacy contact point published anywhere → designate + publish contact.

## 32. Regulatory / Claims vs Reality
- [regulatory] Governance claims (append-only audit, secrets-never-logged, manual merges) have no SOC2/ISO controls mapping or posture doc → publish trust/compliance page mapping claims→controls.
- [regulatory] No PCI posture note; card data never touches system only because Stripe is unwired — undocumented → record SAQ-A intent once Stripe lands.
- [regulatory] GitHub OAuth app unregistered by default (`GITHUB_CLIENT_ID:-}` empty) and no registration/callback-setup guide → add setup doc + fail-fast error when unset.
- [regulatory] SMTP OTP email with zero SPF/DKIM/DMARC guidance — transactional mail will land in spam; sender `harness@noreply.local` default is undeliverable → deliverability runbook + real From requirement.
- [regulatory] STARTTLS refusal falls back to plaintext with only a warning log — regulatory-grade transport should hard-fail or be config-gated → make plaintext opt-in.
- [regulatory] "No auto-merge anywhere" asserted in two docs but no executable policy test proves it at system level → add conformance test asserting no merge call-path exists.
- [regulatory] Notification templates say "do not reply" with no unsubscribe/opt-out footer (CAN-SPAM/GDPR ePrivacy exposure) → add opt-out handling.
- [regulatory] Repo ships **no LICENSE file** — nobody can legally use/self-host it yet → pick and add a license.

## 33. Support/Process
- [support] No README at repo root — first contact for users/operators is missing → write root README (what/install/run/support).
- [support] No user guide, FAQ, or help content in docs/ (docs/ holds internal briefs, audits, clientela research only) → author end-user docs.
- [support] No feedback channel in UI — no report-issue link, survey, or contact in AppShell/settings → add feedback affordance.
- [support] No status page or user-facing health view; per-service `/health` endpoints exist but only section10 checks them internally → expose aggregated status.
- [support] Single runbook (`backup-and-pitr.md`) — no runbooks for OTP delivery failure, event-bus lag, sandbox failures, migration rollback, OAuth breakage → write the five likely-incident runbooks.
- [process] Bus factor = 1: all 78 commits by one author; no CODEOWNERS, no succession docs → document operations + add second maintainer path.
- [process] Tribal knowledge concentrated in STATUS.md (440 lines), docs/briefs mission tables, GAP_ANALYSIS ledger — narrative, not operational reference → distill into maintained docs.
- [process] No CHANGELOG — operators pulling updates can't see what changed → start CHANGELOG.md fed by PRs.
- [process] No release/versioning process (contracts "v0.2.0" exists only as a comment in ts/types.d.ts) → tag releases, version contracts properly.
- [process] No CONTRIBUTING.md or review policy despite "tests must pass before any PR" rule → codify contribution flow.
- [process] No incident severity levels, on-call, or escalation path documented → define incident process.
- [process] ONBOARDING.md security checklist buried in deploy/ — undiscoverable from UI or root → surface/link it.

## 34. Analytics
- [analytics] Zero product analytics — no instrumentation of the signup→project→first-run→first-PR funnel → add lightweight event capture (bus already carries the events).
- [analytics] No per-team usage trends: `models/spend` is a global aggregate; billing quota computes on-demand only → per-team rollups + admin metrics endpoint.
- [analytics] No churn signals tracked (login recency, abandoned runs, invite acceptance) → derive from existing auth/audit tables.
- [analytics] `/metrics` Prometheus endpoint is public/unauthenticated by explicit choice — operational data exposed, and it's infra-not-product analytics anyway → gate behind auth/network policy.
- [analytics] `cost_ledger.record_usage` swallows all failures silently — spend analytics can go dark unnoticed → dead-man's-switch alert on metering gaps.
- [analytics] Onboarding funnel UI exists (section7 onboarding pages) but step completion is never recorded server-side → log funnel milestones.
- [analytics] Notification outbox is a ready-made engagement dataset with zero queries/dashboards → build engagement views.
- [analytics] No experimentation/feature-flag service — only `HARNESS_*` env booleans → adopt runtime flags before A/B anything.
- [analytics] No periodic operator reporting job (weekly active teams, spend, run outcomes) → scheduled digest job.

## Cross-cutting
- [legal] No LICENSE + no pricing + no ToS together mean the product currently cannot be lawfully sold or clearly self-hosted by third parties → treat license/terms/pricing as launch blockers.

**Files created/modified:** none (read-only audit as instructed). **Key verified facts:** single author ×78 commits; zero `ON DELETE` FKs in frozen schema; `PUT /billing/plan` unguarded by flag; `check_budget` ignores `billing_plans`; `HARNESS_TELEMETRY_FULL` documented but unread; LICENSE/CHANGELOG/README/legal docs all absent.