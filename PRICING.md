# Pricing — AI Engineering Harness

> **Status: DRAFT — COUNSEL REVIEW REQUIRED.** Tier structure, caps, and upgrade
> triggers below are mapped to real code, and a test
> (`section1/tests/test_pricing_page_matches_enforced_caps.py`) parses the two
> tables in §1 and fails if any number stops matching the code. **Dollar amounts
> are deliberately absent** — pricing numbers are a business decision with
> tax/legal consequences (`[PLACEHOLDER: counsel review]` in TERMS.md §4).

## 0. How to read this

- The Platform is **self-hosted**: every tier runs the same open-source stack on
  YOUR infrastructure (see [`LICENSE`](LICENSE)). Paid tiers buy *rights, support,
  and hosted/convenience surfaces* — they do not unlock a secret binary.
- **"Enforced" means a door refuses.** Two dimensions vary by tier and are
  refused at run-start (`section4/src/harness_orchestrator/budget.py`) and at
  index time (`section11/src/harness_context/service.py`). Everything else here is
  either a platform-wide limit or a *declared* tier fact with no door behind it,
  and this page says which.
- Metering truth: per-call model spend writes `cost_ledger`
  (`NUMERIC(12,6)`); monthly usage = ledger SUM for the current UTC month; budget
  caps resolve through ONE chain (`contracts/budget.py`): `teams.budget_usd` >
  `billing_plans.monthly_budget_usd` > `HARNESS_TEAM_BUDGET_USD` env > explicit
  unlimited.
- Plan names are exactly `PLAN_TIERS` from
  `contracts/src/harness_contracts/tiers.py`: **free / pro / team**.

## 1. Tiers

### What varies by tier

<!-- pricing:caps -->
| Field | Free | Pro | Team |
|---|---|---|---|
| runsPerDay | 20 | unlimited | unlimited |
| contextIndexMaxChunks | 2000 | unlimited | unlimited |
| seatLimit | 1 | 5 | 25 |
| auditRetentionDays | 30 | 30 | 365 |
<!-- /pricing:caps -->

`runsPerDay` and `contextIndexMaxChunks` are **enforced**: a Free team's 21st run
of a UTC day answers `402` with the frozen upgrade CTA, and the context index
refuses to store beyond the chunk ceiling instead of silently dropping refs.
They are also the only dimensions an operator may tune deploy-wide
(`HARNESS_TIER_RUNS_PER_DAY`, `HARNESS_TIER_CONTEXT_CHUNKS`) or contractually per
team (the `billing_plans` override columns); an env value can tighten a capped
tier but never bound an unlimited one.

`seatLimit` and `auditRetentionDays` are **declared, not enforced**: the roster
and agent-seat routes accept more members than the number, and retention follows
the operator's sweep window, not the plan row. They are listed because a buyer
asks, and labelled because a page that implied a door would be lying. Closing
that gap is a product decision, not a documentation edit.

### What every deployment has

<!-- pricing:platform -->
| Limit | Value | Where it comes from |
|---|---|---|
| runConcurrencyPerTeam | 4 | `HARNESS_RUN_CONCURRENCY` default, `deploy/runners/services.py` |
| enginePoolMax | 8 | `HARNESS_ENGINE_MAX_CONCURRENCY` default, deploy runner |
| workspacesPerTeam | 50 | `HARNESS_MAX_WORKSPACES_PER_TEAM` default, `section1/src/harness_identity/workspaces.py` |
| siemSinksPerTeam | 5 | `MAX_SINKS_PER_TEAM`, `section1/src/harness_identity/siem_sink.py` |
| scimTokensPerTeam | 10 | `MAX_TOKENS_PER_TEAM`, `section1/src/harness_identity/scim.py` |
<!-- /pricing:platform -->

Concurrency is **deploy-wide, not tiered**: one value per team, and a total
engine pool that bounds the whole deployment. A team that needs more raises the
knob and the hardware with it; no plan unlocks a higher number today.

### Tier-only surfaces (refused, not hidden)

These answer `403 PLAN_REQUIRED` ("… requires the Team tier") on any other plan —
real gates, not marketing:

| Surface | Path | Limit |
|---|---|---|
| SSO configuration (group→role mapping) | `PUT /api/v1/teams/{id}/sso/config` | one config per team |
| Directory provisioning (SCIM 2.0) | `/scim/v2/Users`, token API | ≤10 live tokens per team |
| SIEM audit push | `/api/v1/teams/{id}/siem/sinks` | ≤5 sinks per team |

### Pilot offer (draft, not a quote)

The private pilot is one trusted team, up to five developers, for four weeks.
There is no automated billing or payment collection in the pilot. Price,
taxes, payment rail, support commitment, renewal, refund, and data-processing
terms are **COUNSEL REVIEW REQUIRED** and must be agreed in the pilot SOW
before customer data is accepted.

Pilot acceptance is evidence-based: the team completes at least 10 real tasks
unaided, reviews a human-approved plan before every run, sees an audit trail
and cost line for each run, and reports whether it would pay for the workflow.
No acceptance criterion overrides the macOS-only sandbox restriction recorded
in the pilot checklist.

### Free — what you actually get

Everything needed to run governed agent work on real repos:

- Full governance core: mandatory human plan approval, OS-confined sandbox
  (allow-list + egress pinning), append-only event audit, manual merges only.
- One team, **one declared human seat** (not enforced at invite time — see §3).
- **20 runs per UTC day per team** (enforced), with the operator's per-run budget
  by default.
- Context indexing up to **2000 chunks per project** (enforced); beyond that,
  @refs fall back to pasted-text context.
- 4 concurrent runs per team by default (`HARNESS_RUN_CONCURRENCY`); the deploy's
  engine pool bounds the total at 8 unless raised.
- BYO model keys against any OpenAI-compatible endpoint (local Ollama counts);
  spend metering visible in the cost dashboard from day one.
- No credit card, no telemetry opt-out burden: baseline telemetry only, full
  content collection off unless you flip it server-side.

**Upgrade triggers:** the 21st run of a day, an index that wants more than 2000
chunks, or SSO/SCIM/SIEM. Cap-hit responses render an upgrade CTA
(`contracts/src/harness_contracts/tiers.py`), never a dead end.

### Pro — for individual power users and small crews

Everything in Free, plus:

- **No runs/day cap. No context-index size cap** (bounded by your own disk and
  embedding backend).
- Up to **5 declared human seats** (informational today — see §3).
- Paid-tier billing mechanics once the Stripe sync lands: checkout session +
  signature-verified webhook sync (`HARNESS_BILLING=stripe`).
- Concurrency is unchanged by tier: raise `HARNESS_RUN_CONCURRENCY` and the
  engine pool together when you add hardware.

**Upgrade triggers:** SSO/OIDC, directory provisioning, SIEM export, or
audit-retention commitments beyond the operator's sweep window.

### Team — for orgs with procurement requirements

Everything in Pro, plus:

- **SSO/OIDC login** with admin-written group→role mapping (enforced
  `PLAN_REQUIRED`), alongside OTP.
- **SCIM 2.0 directory provisioning** and **SIEM audit push** (enforced
  `PLAN_REQUIRED`), with the per-team caps in the platform table above.
- **365-day audit-retention target** (`auditRetentionDays`), delivered by the
  operator's retention sweep; shorter windows remain the deployment's call.
- Up to **25 declared human seats** (informational today).
- Invoicing/purchase-order flow `[PLACEHOLDER: counsel review]`.

**Trigger to downgrade:** none automated — a lapsed subscription lands on Free
caps at the next reconcile, with data intact because it was always yours.

## 2. What is NOT sold (by design)

- Per-step model selection and silent fallbacks — the engine pins THE configured
  model by law (`AGENTS.md` no-downgrade rule). All tiers get the same honesty.
- Auto-merge. Manual merges only, every tier, forever.
- Access to YOUR code or events by us — self-hosted means we have no channel
  even if we wanted one.

## 3. Enforcement & disclosure notes

- **Enforced today:** the daily run cap and the context-index ceiling, resolved
  per team through `tier_caps` (plan row → per-team override → env knob → tier
  default), and the Team-tier surfaces above.
- **Declared, not enforced:** `seatLimit` (the roster and agent-seat routes do not
  check it) and `auditRetentionDays` (the retention runner uses its own window).
  A deployment that needs a hard seat cap should enforce it at the invite door
  first; this page will say so on the day it exists.
- Rate limiting is abuse protection, not a paywall, and identical across tiers
  (shipped defaults: run-start 30/min/IP, model-call 60/min/IP, brief-generate
  20/min/IP, auth edges 10/min/IP — `section1/src/harness_identity/ratelimit.py`,
  tunable per team since W15).
- Spend caps are financial, not tier features: any tier can set
  `teams.budget_usd` or `HARNESS_TEAM_BUDGET_USD`; unset = explicitly unlimited,
  labeled as such in the UI.

## 4. Cap value provenance (for reviewers)

Every number above is pinned by
`section1/tests/test_pricing_page_matches_enforced_caps.py`, which compares the
`pricing:caps` block with `harness_contracts.tiers.tier_caps()` and the
`pricing:platform` block with the constants the doors read. Beyond that:

| Claim | Source |
|---|---|
| `PLAN_TIERS = ("free","pro","team")` | `contracts/src/harness_contracts/tiers.py` |
| Tier defaults (20 / 2000 / 1-5-25 / 30-30-365) | `contracts/src/harness_contracts/tiers.py` `DEFAULT_*` |
| Daily-run and chunk caps are refused | `section4/src/harness_orchestrator/budget.py` (402 + `cap_hit_wire`), `section11/src/harness_context/service.py` |
| Team-tier gate | `section1/src/harness_identity/sso.py:_require_team_tier` |
| Abuse rate limits | `section1/src/harness_identity/ratelimit.py` |
| Telemetry baseline-on/full-opt-in | `section10/src/harness_onboarding/telemetry.py` |

## 5. When the pricing changes

Change the code, run `make test-section1`, and update the two marked tables in
this file in the same commit — the test names the field and the tier that
disagree. A number that appears only in prose is not checked, so numeric claims
belong in the tables.

---

Related: [`LICENSE`](LICENSE) · [`TERMS.md`](TERMS.md) · [`PRIVACY.md`](PRIVACY.md)
