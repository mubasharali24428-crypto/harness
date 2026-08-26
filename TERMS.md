# Terms of Service — AI Engineering Harness

> **Status: SKELETON.** This document states the real, code-backed mechanics
> of the platform and honestly marks every point where legal judgment is
> required with `[PLACEHOLDER: counsel review]`. It is **not** legal advice
> and is **not** ready for production use until every placeholder is resolved
> by qualified counsel. Do not delete a placeholder without resolving it.

## 0. Who this covers

These terms govern use of the AI Engineering Harness ("the Platform"): the
self-hosted control plane (sections 1–10 as described in `AGENTS.md`) plus,
if operated commercially, any hosted offering built on it.

- `[PLACEHOLDER: counsel review]` — contracting entity legal name, jurisdiction
  of incorporation, and governing law are not yet chosen.
- `[PLACEHOLDER: counsel review]` — whether self-hosted operators accept these
  terms at deploy time (click-through in ONBOARDING) or only hosted customers
  do.

## 1. What the Platform does

The Platform lets human-governed AI agents propose plans that require explicit
human approval before execution (`section4` plan gate), execute inside an OS-
confined sandbox with a command allow-list (`section5`), and emit an
append-only event-sourced audit trail (`agent_events`). Merges are manual;
there is no auto-merge anywhere.

## 2. Your data and where it lives

Self-hosted posture by default:

- All project data — events, plans, cost ledger, audit log — stays in YOUR
  Postgres instance inside your own infrastructure. The Platform ships no
  phone-home analytics channel; telemetry behavior is defined in §5 below.
- You hold `HARNESS_MASTER_KEY`; secret material never leaves Section 1/3
  storage (per `AGENTS.md`).

`[PLACEHOLDER: counsel review]` — if/when a vendor-hosted variant exists:
data-processing addendum, subprocessor list, data-residency options, and
breach-notification timelines must be drafted before any customer data is
accepted.

## 3. Acceptable use

You may not use the Platform to violate law or others' rights, to attack
systems you do not own, or to circumvent its safety machinery (plan approval,
sandbox allow-list, forbidden paths). The sandbox allow-list exists for your
protection; deliberately weakening it is a material breach.

`[PLACEHOLDER: counsel review]` — industry-specific prohibitions (e.g.
regulated-data workloads), export-control classification, and sanctions
clauses not yet drafted.

## 4. Plans, caps, and billing mechanics

Plan tiers are named exactly as implemented in code
(`section1/src/harness_identity/billing.py::PLAN_TIERS = ("free", "pro",
"team")`). Caps, metering, and upgrade triggers are defined normatively in
[`PRICING.md`](PRICING.md); that table controls over any summary here.

Mechanics that exist today, stated plainly:

- Spend metering is real: every successful model call writes
  `cost_ledger` rows; per-team monthly spend is aggregated from the ledger
  against a monthly budget cap resolved through ONE chain
  (`contracts/budget.py`: team override > plan row > env default > explicit
  unlimited).
- Billing integration behind `HARNESS_BILLING=stripe` requires an injected
  Stripe adapter (`StripeClient` Protocol); until Mission 22-M3 lands the
  adapter, no payment card data is collected and no charge can occur.
- `[PLACEHOLDER: counsel review]` — price changes, refunds, proration, tax
  treatment (VAT/sales tax), and auto-renewal disclosures for paid tiers.
  PRICING.md currently carries NO dollar amounts precisely because these are
  unresolved.

## 5. Telemetry and communications

As implemented (`section10/src/harness_onboarding/telemetry.py`,
server-enforced):

- Baseline telemetry is ON by default: error logs, usage analytics, prompt
  metadata (model + token counts), code metadata (files-changed counts,
  languages).
- Full prompt text and full code content are collected ONLY after explicit
  opt-in requiring acknowledgment, enforced server-side — a UI checkbox alone
  cannot enable them.
- Self-hosted deployments may disable baseline telemetry entirely via their
  own configuration; nothing transmits off-box unless you opt in.

Privacy detail lives in [`PRIVACY.md`](PRIVACY.md).

## 6. No warranty; limitation of liability

The Platform is provided under the license in [`LICENSE`](LICENSE) (MIT by
default): **"AS IS", WITHOUT WARRANTY OF ANY KIND** — express, implied, or
statutory, including merchantability, fitness for a particular purpose, and
non-infringement. Governance features (plan gate, sandbox, audit log) reduce
risk; they do not make agent output trustworthy. Human approval gates are part
of the product's contract with you — use them.

To the maximum extent permitted by law, the authors and operators are not
liable for indirect, incidental, special, consequential, or punitive damages,
or lost profits/data/goodwill, arising from use of the Platform. Agent actions
are executed under YOUR approval and in YOUR infrastructure; you remain
responsible for what gets merged to your repositories.

`[PLACEHOLDER: counsel review]` — liability cap structure (fees-paid vs.
fixed cap vs. unlimited for breach/confidentiality carve-outs) once a paid
tier exists; indemnities (mutual? IP infringement?) not yet drafted.

## 7. Term, suspension, termination

- You may stop using the self-hosted Platform at any time; your data is in
  your database and deleting it is your operation (retention sweeps are
  configurable; see §8 of PRIVACY.md).
- `[PLACEHOLDER: counsel review]` — suspension/termination rights, cure
  periods, and data-export windows for future hosted accounts.

## 8. Changes to these terms

`[PLACEHOLDER: counsel review]` — amendment mechanism (notice period, version
history page, continued-use clause) must be settled before first published
version. Until then this file may change without notice; treat the repo
history as the changelog.

## 9. Governing law and disputes

`[PLACEHOLDER: counsel review]` — governing law, venue, arbitration vs.
courts, and class-action waiver are entirely undecided. No default is
pretended here because none exists yet.

---

Related: [`LICENSE`](LICENSE) · [`PRICING.md`](PRICING.md) ·
[`PRIVACY.md`](PRIVACY.md)
