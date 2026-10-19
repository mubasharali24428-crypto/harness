# Part 57 — billing, quotas and cost controls

## Product and pilot boundary

The product has plan metadata and Stripe checkout/subscription synchronization, but the current
pilot does not require paid/live billing. No paid or live provider calls were made for this session.
Part 57 is capped at 8 until live Stripe and OIDC evidence is reviewed; source and mocks do not
prove provider operation.

## Directive map

| # | Result | Evidence / remaining limit |
|---|---|---|
| 001 | Partial | Plan/billing model exists; pilot execution can use internal/free limits. Live paid product behavior is unverified. |
| 002 | Partial | Team monthly spend and daily run counts are attributed; agent run pools track allocations/reservations/spend. Organization-wide aggregation is not established. |
| 003 | Partial | Tier daily-run caps and monthly spend caps are enforced; overrides exist. Tool-call count and in-flight spend caps remain unimplemented (Session 6 gap). |
| 004 | Partial | Run-start checks use estimated call cost and expose budget/cap status; no rich pre-run estimate for an entire agent plan. |
| 005 | Partial | Hard spend and daily run limits are enforced; billing alert configuration/notification delivery is incomplete. |
| 006 | Implemented locally | Run-start tier/budget gate blocks before execution. Budget reservation uses atomic conditional updates; existing state/audit history is retained on rejection. |
| 007 | Partial | Team, run and agent allocation are visible in existing billing/agent APIs; org-level consolidated attribution and alerts are absent. |
| 008 | Partial | Billing endpoints require billing permissions; the billing UI is team scoped. No live account-role/provider separation test was run. |
| 009 | Partial | Stripe event-id claim and tier update are transactional; failed-call reconciliation/refunds and live dispute/reconciliation handling are not verified. |
| 010 | Implemented for pilot boundary | No paid/live call was run. The local billing controls do not enable live charges by themselves. |

## Verification and cap

`section4/src/harness_orchestrator/budget.py` checks the resolved tier daily-run cap and the shared
monthly spend budget before run start; the gate fails closed on unreadable state. Per-agent pool
reservations use conditional updates. `contracts/src/harness_contracts/budget.py` and `tiers.py`
provide the shared cap resolution. Section 1 billing and Stripe tests plus Section 4 budget/tier
tests are listed in `session7-summary.md`.

The enforced code paths can be verified locally, but live Stripe and OIDC behavior is external and
unverified. Accordingly the capability matrix records `partial (cap 8)`, not `verified`.
