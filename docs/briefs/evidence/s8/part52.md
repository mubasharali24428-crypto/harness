# Part 52 — security and privacy program

## Directive results

| # | Status | Evidence and remaining gap |
|---|---|---|
| 001 | Partial | `docs/briefs/threat-model.md` and `evidence/s1/threat-model-evidence.md` maintain threat inventory; Session 8 confirmed live SDK vs Section 5 policy is a distinct boundary and requires proof/integration. |
| 002 | External/deployment evidence required | Vault/provider secrets are encrypted by application code; transport, database disks, object storage and backups rely on deployment controls not verified in this source-only pass. |
| 003 | Partial | Team/run ownership guards and cross-team tests exist; data residency is not separately routed across stores and providers. |
| 004 | Partial | Section 1 vault encrypts supported credentials; this is not an external dedicated secret manager and not every deployment secret is held there. |
| 005 | Partial | Webhook secrets can rotate; OAuth/API credentials have revocation flows. No comprehensive rotation schedule/credential inventory is verified. |
| 006 | Partial | Org service identities have a closed scope set. Deployment service accounts and live provider scopes need operator review. |
| 007 | Partial | Security headers, SSRF checks, parameterized SQL, Section 5 allow-list/path checks and secret scrubbing are present. Section 4 pins `sdk-minimal`, applies a per-run `workspace-write` overlay, and refuses plans with non-empty `forbiddenPaths`; its shell still bypasses Section 5's command allow-list and cannot selectively enforce paths. |
| 008 | External/operational | Dependency manifests and CI exist; current automated vulnerability scan results and response SLAs were not available in this review. |
| 009 | Partial | Backup, audit and incident-related runbooks exist; customer notification and evidence-preservation procedures need an owner-approved operational process. |
| 010 | Partial | `docs/data-map.md`, `docs/sandbox-isolation.md` and this report describe limits. No independent pentest or security review was conducted. |

## Verification boundary

Local security invariants are covered by the Section 1 auth/audit tests, Section 4 authorization and
isolation tests, and Section 5 sandbox policy/isolation tests. The precise commands and results from
this Session 8 pass are recorded in `session8-summary.md`. Source review does not establish deployed
TLS, encryption at rest, least-privilege cloud roles, model-provider retention behavior, or absence of
unknown vulnerabilities.

Part 52 remains partial and externally capped pending an independent penetration test and operator
evidence for deployment security controls. No certification or compliance claim is made.
