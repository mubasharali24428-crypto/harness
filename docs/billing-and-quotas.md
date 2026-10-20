# Billing and quotas (part 57)

The pilot does not require paid execution. Billing plan and Stripe integration code exists, but no
live Stripe or OIDC provider flow was exercised in Session 7. Stripe is intentionally out of scope
for the current pilot (owner direction) and does not cap the local billing and quota score.

## Budget truth and enforcement

The displayed monthly spend limit and run-start budget check share
`harness_contracts.budget.resolve_cap`: team override, then plan limit, then the configured default.
Usage is attributed to the team through the project/run ownership chain. Section 4 checks the tier's
daily run limit and estimated call cost before run start. Agent run pools reserve child budgets with
conditional database updates; work that exceeds an allocated pool is refused.

Billing administrators can set a hard team monthly cap, concurrent-run limit, and warning
threshold. Concurrent queue claims apply the configured limit under the queue's serialized claim
transaction. Daily run reservations are idempotent by run id and use a team-scoped transaction lock
to serialize cap decisions. Per-run tool-call limits count durable tool-call records and gate start,
claim, and running sweeps. The monthly spend check still uses recorded usage plus an estimate; it
does not reserve exact in-flight model spend. Billing settings are separate from run permissions.

Stripe inbound events validate the raw-body signature timestamp and use the event id as a
transactional idempotency claim when synchronizing tier state. Live subscription lifecycle,
refunds, disputes and reconciliation remain unverified.

When a run's spend projection first crosses the warning threshold, `budget.deliver_budget_warning`
puts a `budget` / `budget_warning` notification in the inbox of each billing manager: team admins,
or for a team in an org the org's owners and billing admins (the rule of `billing._billing_manager`).
It is sent once per person per team per UTC month (dedupe key `budget-warning:<team>:<YYYY-MM>`), so
raising the cap and crossing again in the same month does not warn twice. It is not urgent: email
follows each person's own notification settings (off unless they opted in). An org billing manager
who is not a member of the team is not told (`notify.emit` only writes to team members). The hard stop
is not announced to billing managers: the blocked run fails and its participants are told as usual.

See `docs/briefs/evidence/s7/part57.md` for the directive map and exact local verification.
