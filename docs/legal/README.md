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
