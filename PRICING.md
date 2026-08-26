# Pricing — AI Engineering Harness

> **Status: SKELETON.** Tier structure, caps, and upgrade triggers below are
> mapped to real code. **Dollar amounts are deliberately absent** — pricing
> numbers are a business decision with tax/legal consequences
> (`[PLACEHOLDER: counsel review]` in TERMS.md §4). Everything else is stated
> as implemented today.

## 0. How to read this

- The Platform is **self-hosted**: every tier runs the same open-source stack
  on YOUR infrastructure (see [`LICENSE`](LICENSE)). Paid tiers buy *rights,
  support, and hosted/convenience surfaces* — they do not unlock a secret
  binary.
- Caps marked **[M2]** are the tier-enforcement behaviors Mission 22-M2 wires
  at run-start + index API; the cap VALUES below are already the shipped
  defaults or named env knobs, so M2 enforces what this table promises.
- Metering truth: per-call model spend writes `cost_ledger`
  (`NUMERIC(12,6)`); monthly usage = ledger SUM for the current UTC month;
  budget caps resolve through ONE chain (`contracts/budget.py`):
  `teams.budget_usd` > `billing_plans.monthly_budget_usd` > `HARNESS_TEAM_BUDGET_USD` env > explicit unlimited.
- Plan names are exactly `PLAN_TIERS` from
  `section1/src/harness_identity/billing.py`: **free / pro / team**.

## 1. Tiers

| | **Free** | **Pro** | **Team** |
|---|---|---|---|
| Price | $0 forever | `[PLACEHOLDER: counsel review]` | `[PLACEHOLDER: counsel review]` (per-seat) |
| Seats (humans) | 1 admin | up to 5 **[M2]** | unlimited; SSO/OIDC **[M4]** |
| Agent seats | 3 **[M2]** | 25 **[M2]** | unlimited **[M2]** |
| Concurrent runs / team | `HARNESS_RUN_CONCURRENCY` default **4** | default **8** **[M2]** | default **16** **[M2]** |
| Runs / day / team | 50 **[M2]** | unlimited | unlimited |
| Context index size | 5k chunks / project **[M2]** | unlimited | unlimited |
| Audit-log retention | platform default (**365 days**, operator-tunable) | same + assisted restore | same + configurable longer windows |
| Model providers | BYO keys, all supported endpoints | same | same |
| Billing surface | none needed | Stripe checkout + webhooks **[M3]** | Stripe + invoicing `[PLACEHOLDER: counsel review]` |
| Support | community | email | priority + SLA `[PLACEHOLDER: counsel review]` |

### Free — what you actually get

Everything needed to run governed agent work on real repos:

- Full governance core: mandatory human plan approval, OS-confined sandbox
  (allow-list + egress pinning), append-only event audit, manual merges only.
- One team, one human seat, up to 3 agent seats **[M2]**.
- Runs capped at **4 concurrent per team** — the shipped default of
  `HARNESS_RUN_CONCURRENCY` (`deploy/runners/services.py`; engine pool
  honestly bounds total concurrency at `ENGINE_MAX_CONCURRENCY`, default 8).
- Runs/day soft target **50/team [M2]** — generous for evaluation; see
  trigger below.
- Context indexing via Section 11 up to **5k chunks/project [M2]** (~a few MB
  of source); beyond that, @refs fall back to pasted-text context.
- BYO model keys against any OpenAI-compatible endpoint (local Ollama counts);
  spend metering visible in the cost dashboard from day one.
- No credit card, no telemetry opt-out burden: baseline telemetry only, full
  content collection off unless you flip it server-side.

**Upgrade triggers:** a 6th agent seat, sustained >50 runs/day, or an index
that wants more than 5k chunks. Cap-hit responses render an upgrade CTA
(Mission 22-M2), never a dead end.

### Pro — for individual power users and small crews

Everything in Free, plus:

- Up to **5 human seats** and **25 agent seats [M2]**.
- Concurrent runs raised to **8/team [M2]** (still bounded by the deploy's
  `ENGINE_MAX_CONCURRENCY` — raise it when you scale hardware).
- **No runs/day cap. No context-index size cap** (bounded by your own disk
  and embedding backend).
- Paid-tier billing mechanics once Mission 22-M3 lands: Stripe checkout
  session + signature-verified webhook sync (`HARNESS_BILLING=stripe`).

**Upgrade triggers:** a 6th human seat, centralized identity/SSO needs, or
audit-retention commitments beyond operator defaults.

### Team — for orgs with procurement requirements

Everything in Pro, plus:

- Unlimited human seats with **SSO/OIDC login [M4]** (standard discovery-based
  flows; per-team IdP config) alongside OTP.
- Unlimited agent seats **[M2]**; concurrent-run ceiling **16/team [M2]**
  (hardware permitting via `ENGINE_MAX_CONCURRENCY`).
- Audit-retention windows configurable beyond defaults; export tooling for
  compliance packaging `[PLACEHOLDER: counsel review]`.
- Invoicing/purchase-order flow `[PLACEHOLDER: counsel review]`.

**Trigger to downgrade:** none automated — lapsing subscriptions degrade
honestly to Free caps at next reconcile (M3 webhook sync), with data intact
because it was always yours.

## 2. What is NOT sold (by design)

- Per-step model selection and silent fallbacks — engine pins THE configured
  model by law (`AGENTS.md` no-downgrade rule). All tiers get the same
  honesty.
- Auto-merge. Manual merges only, every tier, forever.
- Access to YOUR code or events by us — self-hosted means we have no channel
  even if we wanted one.

## 3. Enforcement & disclosure notes

- Today (pre-M2) tiers are stored but not enforced: `PUT /api/v1/billing/plan`
  sets the plan row (admin-only), quota display resolves through the ONE
  budget chain, and nothing else differs between tiers. This document is the
  contract M2 enforces and M3 bills against.
- Rate limiting is abuse protection, not a paywall, and identical across
  tiers (shipped budgets: run-start 30/min/IP, model-call 60/min/IP,
  brief-generate 20/min/IP, auth edges 10/min/IP — `section1/ratelimit.py`).
- Spend caps are financial, not tier features: any tier can set
  `teams.budget_usd` or `HARNESS_TEAM_BUDGET_USD`; unset = explicitly
  unlimited, labeled as such in the UI.

## 4. Cap value provenance (for reviewers)

Every number above traces to code at HEAD (cc69d21+):

| Claimed cap | Source |
|---|---|
| `PLAN_TIERS = ("free","pro","team")` | `section1/src/harness_identity/billing.py:66` |
| Concurrent runs default 4 | `HARNESS_RUN_CONCURRENCY` default "4" — `deploy/runners/services.py:342`; `.env.example:187` |
| Engine pool bound 8 | `ENGINE_MAX_CONCURRENCY` default 8 — `.env.example:193`; `section4/engine_runtime.py` |
| Audit retention 365d / events 30d | `deploy/runners/events_retention.py` DEFAULT_AUDIT_RETENTION_DAYS=365, DEFAULT_RETENTION_DAYS=30 |
| Abuse rate limits (10/30/60/20 per min) | `section1/src/harness_identity/ratelimit.py` DEFAULT_LIMIT=10, RUN_START_LIMIT=30, MODEL_CALL_LIMIT=60, BRIEF_GENERATE_LIMIT=20 |
| Telemetry baseline-on/full-opt-in | `section10/src/harness_onboarding/telemetry.py` |

Values marked **[M2]** are new tier levers defined HERE first; M2 must land
them at these exact values (or PR this table alongside its change). Values
marked **[M4]/[M3]** are their missions' deliverables referenced for
completeness.

---

Related: [`LICENSE`](LICENSE) · [`TERMS.md`](TERMS.md) · [`PRIVACY.md`](PRIVACY.md)
