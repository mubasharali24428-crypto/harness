# Part 58 — compliance and data lifecycle

## Directive results

| # | Status | Evidence and remaining gap |
|---|---|---|
| 001 | External/counsel | Target markets and applicable obligations require product/legal decisions; none are inferred by this implementation review. |
| 002 | Partial | Storage/providers are mapped in `docs/data-map.md`; provider-side retention and all deployment backup copies are not independently verified. |
| 003 | Partial | Global and shorter organization event/audit retention, run retention, comment erasure and backup retention mechanisms exist. Team-admin run holds have audited create/read/release routes; Section 4 purge, event/audit row pruning and PostgreSQL partition retirement honor active holds. User deletion and backup expiry do not honor holds; retention sidecars are opt-in, and this does not establish a complete legal-hold process. |
| 004 | Partial | Part 50 provides scoped evidence export and authored-content deletion requests. This is not full account erasure, and artifacts/approval metadata/audit evidence have documented retained exceptions. |
| 005 | Partial | Provider/model configuration is present; a current counsel-reviewed subprocessor list and contractual data-handling terms were not found. |
| 006 | Partial | Deployment placement can be selected by operators; no tenant-specific residency routing/enforcement across DB, events, artifacts, providers and backups exists. |
| 007 | Partial | Audit emission scrubs secret-shaped keys and retention bounds data; retained run guidance and audit evidence may still contain sensitive business content. |
| 008 | Missing | Org membership and service identity administration exist; no periodic access recertification workflow or evidence schedule was found. |
| 009 | Partial | Numbered migrations, checkpoint/evidence files and local tests provide development change records; regulated release approval is not established. |
| 010 | External/legal gate | No certification, legal approval, or compliance audit is claimed. |

## Verification

Existing Section 4 tests cover export authorization, export expiry, authored-content deletion,
retained exceptions, search removal, and exclusion of held runs from Section 4 run purging. Existing
Section 1 retention tests cover event/audit pruning but do not apply run holds. Deployment of scheduled
retention and expiry is an operator prerequisite. Part 58 remains partial and externally gated pending
counsel review, complete legal-hold behavior, account lifecycle decisions and independent evidence.
