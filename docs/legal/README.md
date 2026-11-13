# Legal packet — counsel review drafts

**Status: DRAFTS. Not legal advice, not a contract, not reviewed by a lawyer.**
Nothing here may be sent to a customer as an executed agreement until qualified
counsel in the operator's jurisdiction has reviewed and completed it. Placeholders
in square brackets are deliberately unanswered.

This packet exists so a lawyer can review a complete factual picture in one pass,
rather than interviewing the operator first.

| File | Purpose | Owner before counsel |
|---|---|---|
| `DPA_DRAFT.md` | Data processing agreement draft with processing, security and transfer annexes | Operator fills entity/jurisdiction blanks; counsel rewrites legal terms |
| `SUBPROCESSORS.md` | Every third party that can touch customer data, with location and status | Operator contracts/removes; counsel approves the transfer mechanism |
| `SECURITY_QUESTIONNAIRE.md` | Honest answers to the questions a design partner will ask | Operator keeps evidence links current |
| `COUNSEL_OPEN_QUESTIONS.md` | The decisions only counsel can make | Counsel answers; operator records the answer and date |
| `PILOT_SOW_TEMPLATE.md` | Fill-in pilot SOW skeleton (P-03 draft, 2026-11-11): scope, acceptance, commercial placeholders, evidence gates | Operator fills entity/price/rail blanks (P-D2/P-D4); counsel reviews before any signature |
| `W4-COUNSEL-HANDOFF.md` | Week 4 factual packet index and unanswered legal/security questions | Owner selects counsel, records engagement and answers; no legal approval implied |
| `../decisions/2026-11-12-payment-rail-P-D2-DRAFT.md` | Official-source payment-rail evidence and unresolved seller-eligibility questions | Owner obtains written provider/counsel confirmation before enabling billing |

Related existing documents (also counsel-review drafts):

- `PRIVACY.md`, `TERMS.md` — product-level notices.
- `docs/pilot-remediation/PILOT_SOW_DPA_CHECKLIST.md` — pilot scope checklist.
- `docs/data-map.md` — what is stored where, with retention behavior.
- `docs/runbooks/external-validation.md` — which claims have executed evidence.

## What this packet deliberately does NOT claim

- No SOC 2, ISO 27001, HIPAA or PCI certification.
- No independent penetration test.
- No subprocessor contracts are in place yet.
- No tenant-specific data residency enforcement (the workspace field is declarative).
- Backups are not encrypted at rest; they rely on storage-layer encryption and the
  app-level encrypted secrets inside them.
- Stripe billing is flag-off (Stripe is unavailable in Pakistan).

Each of those is stated as a limitation in the questionnaire rather than hidden.
