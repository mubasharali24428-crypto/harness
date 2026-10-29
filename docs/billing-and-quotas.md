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
threshold. Two HTTP budgets also apply per team at request time (W15): run start 60/min and model
call 300/min, stored in `rate_buckets` and answered 429 `RATE_LIMITED` with `Retry-After`. They are
abuse guards against a runaway script, not usage quotas: they do not consume the monthly cap and a
store outage fails open, exactly like the per-IP limiter. Tune them with
`HARNESS_TEAM_RUN_START_LIMIT` / `HARNESS_TEAM_MODEL_CALL_LIMIT`. Concurrent queue claims apply the configured limit under the queue's serialized claim
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
who has no seat in the capped team gets the row in their first other team of the org (the inbox only
shows the team a person is signed in to; `source_id` still names the capped team); one with no seat
in any team of the org has nowhere to see it and is skipped.

When the monthly cap actually blocks a run (the pre-spawn gate or the reviewer model call), the same
function sends the same people a `budget_exhausted` notification ("Team spend reached its monthly limit"),
once per person per team per UTC month (dedupe key `budget-exhausted:<team>:<YYYY-MM>`). It is separate
from the warning, so a person who got both sees two rows. A degraded denial (the money store was down)
is an outage, not a cap, and tells nobody. The blocked run still fails with "Budget cap reached" and its
participants are told as usual; a delivery failure never changes that outcome.

When the daily run cap (plan limit) blocks a run, the same people get a `plan_limit_reached`
notification ("Your team reached its daily run limit"), once per person per team per UTC **day** (dedupe key
`plan-limit-reached:<team>:<YYYY-MM-DD>`), because the cap resets each day. A degraded reservation (the
store was down) is an outage and tells nobody. Concurrent workspace creates are serialized per team, so the
workspace quota (`HARNESS_MAX_WORKSPACES_PER_TEAM`) can no longer be overshot (see `docs/workspaces.md`).

See `docs/briefs/evidence/s7/part57.md` for the directive map and exact local verification.
