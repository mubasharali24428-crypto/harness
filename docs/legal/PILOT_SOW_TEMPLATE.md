# Private pilot — Statement of Work TEMPLATE

**Status: DRAFT — COUNSEL REVIEW REQUIRED. Not a contract, not legal advice, not an
offer.** Every `[BRACKET]` is deliberately unanswered. Nothing here may be sent to a
customer as an executed agreement until qualified counsel has reviewed it alongside
the rest of this packet (`README.md`, `DPA_DRAFT.md`, `SUBPROCESSORS.md`,
`SECURITY_QUESTIONNAIRE.md`, `COUNSEL_OPEN_QUESTIONS.md`) and
`docs/pilot-remediation/PILOT_SOW_DPA_CHECKLIST.md`.

Drafted 2026-11-11 for task P-03 of `docs/plans/90-day-execution-plan-2026-11-11.md`.
Pilot limits below are inherited from the checklist and `PRICING.md` §1 ("Pilot offer").
The checklist's macOS Seatbelt wording and the container-worker deployment are not yet
reconciled; **the host/execution boundary must be approved by owner, security and counsel
before customer data is accepted**. Dollar amounts are decision P-D4; payment rail is
decision P-D2 (`docs/decisions/2026-11-12-payment-rail-P-D2-DRAFT.md`). Billing remains off.

---

## 1. Parties and pilot window

| Field | Value |
|---|---|
| Operator contracting entity | `[ENTITY — P-D2: PK entity vs US LLC; counsel]` |
| Pilot team ("Customer") | `[LEGAL NAME, COUNTRY, CONTACT]` |
| Pilot participants | Up to **5 developers**, one team (PRICING.md pilot offer) |
| Duration | **4 weeks** from `[START DATE]`, supervised |
| Support contact / channel | `[NAME, CHANNEL, RESPONSE WINDOW — no SLA unless counsel approves]` |
| Operator environment | Self-hosted on Customer-approved host; execution backend and isolation boundary recorded in the approved security annex |
| Model provider | Customer-approved provider with written data scope, retention/transfer terms and budget; local/mock unless live use is separately approved |

## 2. Scope

The Operator provides the Harness platform (MIT-licensed stack, self-hosted) for
governed AI coding-agent work on Customer repositories:

- mandatory human plan approval before every run; manual merges only — no auto-merge;
- OS-confined sandbox execution (allow-list + egress pinning) within the documented limits;
- append-only event audit and per-run cost line (`cost_ledger`);
- onboarding walkthrough (`docs/TUTORIAL.md`) and supervised first run.

**Exclusions (non-negotiable for this pilot):** no production secrets, no regulated data,
no payment-card data. Execution is limited to the approved host/backend in the security
annex; any other boundary is out of scope. Repositories used in the pilot are
`[NON-PRODUCTION REPOS — list]`.

## 3. Acceptance criteria (evidence-based, from the checklist)

Pilot succeeds when, at the final revision of the pilot window:

1. the team completes **≥10 real tasks unaided**;
2. every run had **explicit human plan approval before execution**;
3. **audit trail and cost line** are available for each run;
4. **no confirmed sandbox escape**;
5. Customer issues a **written continue/stop decision** at week 4, including whether
   it would pay for the workflow and at what price (feeds P-14 pay-intent evidence).

Measurements recorded weekly (90-day plan P-09/P-12): tasks completed, acceptance rate,
human interventions, blocked/denied actions, time-to-first-useful-output, cost per task
from `cost_ledger`, incidents. Aggregate measurements only may be published; Customer
private information is never published (FUTURE PLAN §10).

## 4. Commercial terms — ALL PLACEHOLDERS (P-D4 / counsel)

| Term | Value |
|---|---|
| Pilot price | `[PRICE — owner band $500–2K/mo per plan P-D4; counsel + tax review]` |
| Payment rail | `[P-D2 — MoR (Paddle/FastSpring, eligibility unverified) vs US LLC + Stripe; no rail live until decided]` |
| Taxes / invoicing | `[COUNSEL]` |
| Refund / termination | `[COUNSEL — either party, notice period, data return]` |
| Conversion to paid | `[COUNSEL — pilot outcome does not auto-convert; separate agreement]` |
| Billing mechanics | **None automated during pilot** (PRICING.md); no payment collection until rail + terms approved |

## 5. Data processing and security

- DPA: `DPA_DRAFT.md` annexes apply once completed by counsel; controller/processor
  roles, data categories (source code, prompts, transcripts, identity, audit events,
  spend metadata), retention/deletion, backup expiry, legal holds per checklist.
- Subprocessors: `SUBPROCESSORS.md` — none approved merely by appearing there.
- Security posture: `SECURITY_QUESTIONNAIRE.md` — honest limits, including: no SOC 2 /
  ISO / HIPAA / PCI certification, no independent pen test yet, backups not encrypted
  at rest (storage-layer + app-level encrypted secrets), sandbox pilot-grade.
- Customer has reviewed `docs/pilot-remediation/BASELINE_2026-10-21.md` and the explicit
  blockers before acceptance (checklist).
- Incident classification, notice clock, contacts: `[COUNSEL + DPA]`.

## 6. Operator evidence attached before signature

- [ ] real master key + backups configured; no secrets in examples/logs
- [ ] approved host/backend designated; boundary evidence and negative tests attached
- [ ] provider/data scope approved; live budget and retention terms recorded
- [ ] retention/deletion + restore drill evidence attached
- [ ] BASELINE_2026-10-21.md and current Week 3/4 limitations reviewed by Customer
- [ ] counsel sign-off on this SOW + DPA + questionnaire (A-11 gate)

## 7. Signatures

`[OPERATOR SIGNATORY]` · `[CUSTOMER SIGNATORY]` · `[DATE]` — **blocked until counsel
review complete and owner go/no-go recorded (FUTURE PLAN A-11, §10).**