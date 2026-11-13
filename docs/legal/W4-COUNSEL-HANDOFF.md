# Week 4 counsel handoff — factual packet, not legal advice

Prepared 2026-11-12. **No counsel has been contacted and no draft is approved.**
This file tells the owner what to send and what remains unanswered. Do not send customer
data, secrets, tokens or private repository contents to counsel without a separate decision.

## Send for review

1. `README.md`, `DPA_DRAFT.md`, `SUBPROCESSORS.md`, `SECURITY_QUESTIONNAIRE.md`,
   `COUNSEL_OPEN_QUESTIONS.md`, `PILOT_SOW_TEMPLATE.md`.
2. Root `TERMS.md`, `PRIVACY.md`, `LICENSE`, `PRICING.md`.
3. `docs/pilot-remediation/PILOT_SOW_DPA_CHECKLIST.md`,
   `docs/pilot-remediation/A01-DISPOSITIONS-2026-11-11.md`.
4. `docs/briefs/evidence/90day/week-03.md`, `week-04.md`, and the current image manifest.
5. `docs/decisions/2026-11-12-payment-rail-P-D2-DRAFT.md`.

## Current factual posture

- Solo operator in Pakistan; contracting entity, jurisdiction, insurance and tax status
  are not recorded. No customer SOW is signed.
- Software is pilot-grade. No SOC 2/ISO/HIPAA/PCI certification or independent penetration
  test is claimed. Known boundary findings remain open in the Week 3 review.
- The RC is a local scratch deployment, not a customer environment. It uses dry mode,
  development authentication inherited from the operator config, and no published database port.
- The worker daemon holds the host Docker socket; container isolation does not enforce
  within-workspace read scope; Landlock has no network boundary. These are engineering
  limitations, not approved risk acceptances.
- The old checklist names macOS Seatbelt; the live orchestrator uses a container worker.
  Counsel/security/owner must reconcile the approved pilot host and boundary before data access.
- Billing is disabled. Stripe has an adapter but no live drill; Paddle/FastSpring have no
  adapter. Public buyer-country or tax-table entries do not establish seller eligibility.
- No subprocessor contracts are recorded. Model providers, hosting, storage, email and
  repository connections must be mapped to the actual customer deployment.

## Questions counsel must answer

Use `COUNSEL_OPEN_QUESTIONS.md` as the master list. Week 4 priorities:

1. Contracting entity and jurisdiction; whether an unpaid pilot is permissible and what
   liability/indemnity/insurance wording applies.
2. Controller/processor roles for code, prompts, identity, audit and billing data;
   model-provider subprocessor status and international transfer mechanism.
3. Approved pilot host/backend, security warranties, breach clock, retention/deletion,
   backup expiry and residency disclosure.
4. Payment route: Pakistan-seller MoR eligibility vs US entity + Stripe; tax, refund,
   invoice, payout and ongoing compliance obligations. Do not form an entity on engineering advice.
5. Exact public claims allowed for "private design-partner pilot", confinement, audit,
   manual merge and provider compatibility.
6. Name/trademark clearance process; the rename shortlist is not clearance.

## Owner actions before sending

- Fill entity/contact placeholders only with verified information.
- Attach the current evidence manifest and known-limits list, not marketing summaries.
- Record counsel name, engagement date, scope, fee arrangement and conflict check.
- Record written answers against each open question; engineering cannot sign them.
- Update subprocessors/DPA/SOW only after counsel approves the actual deployment.

## Status

Packet prepared. Counsel engagement: **not started**. Legal approval: **not granted**.
