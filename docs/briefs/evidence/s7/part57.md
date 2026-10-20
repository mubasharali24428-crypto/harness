# Part 57 — billing, quotas and cost controls

## Product and pilot boundary

The current pilot intentionally has no Stripe or paid billing dependency. No provider billing flow
is required for the local quota controls or the current score; Stripe checkout/subscription code is
not part of the completion criteria for this pass.

## Directive map

| # | Result | Evidence / remaining limit |
|---|---|---|
| 001 | Partial | Plan/billing model exists; pilot execution can use internal/free limits. Live paid product behavior is unverified. |
| 002 | Partial | Team monthly spend and daily run counts are attributed; agent run pools track allocations/reservations/spend. Organization-wide aggregation is not established. |
| 003 | Partial | Tier daily-run caps are now atomically reserved per run; billing admins can configure a team hard monthly cap and concurrent-run override. Tool-call limits are enforced from durable tool records. In-flight model spend remains estimated until engine completion. |
| 004 | Partial | Run-start checks use estimated call cost and expose budget/cap status; no rich pre-run estimate for an entire agent plan. |
| 005 | Partial | Hard spend and daily run limits are enforced; billing admins can configure the warning threshold. Session 11: the first crossing now puts a `budget_warning` notification in each billing manager's inbox (team admins; for an org-linked team, org owners and billing admins), once per person per team per UTC month (`budget.deliver_budget_warning`, `test_budget_warning_delivery.py` 5 tests, docs `billing-and-quotas.md`). Not delivered: the hard stop, org billing managers who are not team members, any channel beyond the inbox and opt-in email. |
| 006 | Implemented locally | Run-start tier/budget gate blocks before execution. Budget reservation uses atomic conditional updates; existing state/audit history is retained on rejection. |
| 007 | Partial | Team, run and agent allocation are visible in existing billing/agent APIs; org-level consolidated attribution and alerts are absent. |
| 008 | Partial | Billing endpoints require billing permissions; the billing UI is team scoped. No live account-role/provider separation test was run. |
| 009 | Out of scope for current pilot | No paid billing provider is enabled; provider reconciliation and refunds are excluded from the no-Stripe pilot. |
| 010 | Implemented for pilot boundary | No paid/live call was run. The local billing controls do not enable live charges by themselves. |

## Verification and cap

`section4/src/harness_orchestrator/budget.py` checks the resolved tier daily-run cap and the shared
monthly spend budget before run start; the gate fails closed on unreadable state. Per-agent pool
reservations use conditional updates. `contracts/src/harness_contracts/budget.py` and `tiers.py`
provide the shared cap resolution. Section 1 org/policy/retention tests (80 passed, 2 skipped),
Section 2 GitHub OAuth tests (11 passed), Section 7 typecheck, and Section 4 focused
reservation/tool-limit tests passed. Two Section 4 execution-test fixture failures seen at the time were
fixed in `6b6adfb`; see `session7-summary.md`.

The billing UI exposes team hard monthly cap, maximum concurrent runs, and warning threshold.
Daily reservations are idempotent by run id and serialized by team in PostgreSQL. Stripe is
intentionally out of scope; the capability matrix records the local billing result without an
external-provider score cap. In-flight engine cost estimation remains open; warning delivery is closed in-app (see directive 005).
