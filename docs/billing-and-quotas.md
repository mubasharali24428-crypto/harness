# Billing and quotas (part 57)

The pilot does not require paid execution. Billing plan and Stripe integration code exists, but no
live Stripe or OIDC provider flow was exercised in Session 7. The part 57 capability score remains
capped at 8 until that external evidence is reviewed.

## Budget truth and enforcement

The displayed monthly spend limit and run-start budget check share
`harness_contracts.budget.resolve_cap`: team override, then plan limit, then the configured default.
Usage is attributed to the team through the project/run ownership chain. Section 4 checks the tier's
daily run limit and estimated call cost before run start. Agent run pools reserve child budgets with
conditional database updates; work that exceeds an allocated pool is refused.

The daily run count is checked before inserting a run, so two simultaneous starts can both observe
space under the cap. The monthly spend check also evaluates recorded usage plus an estimate; it is
not a provider-side atomic spend reservation. Tool-call count and in-flight spend are not capped.
These constraints are why quota controls are not marked complete. The billing UI exposes current
plan, usage, cap and tier limits, while administrative changes remain separate from run permissions.

Stripe inbound events validate the raw-body signature timestamp and use the event id as a
transactional idempotency claim when synchronizing tier state. Live subscription lifecycle,
refunds, disputes and reconciliation remain unverified. No alerts configuration or notification
delivery is exposed yet.

See `docs/briefs/evidence/s7/part57.md` for the directive map and exact local verification.
