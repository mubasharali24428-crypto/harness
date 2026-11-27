# DPIA draft — harness limited pilot (DRAFT — for counsel review)

Processing: a self-hosted developer platform processes team identity data
(emails, names), customer-authored repository content, customer-configured
provider credentials (encrypted at rest), and operational logs, to execute
AI-assisted software tasks for the pilot team.

- **Necessity & proportionality:** data minimised per table (retention-table.md);
  team-scoped access (authz matrix evidence); BYOK keys encrypted (Fernet,
  dual-read KDF); audit chain integrity (hash chain + external anchor);
  guest portal deny-by-default; erasure path exists.
- **Risks to individuals:** (a) provider receives repo content — mitigated by
  the customer's own key choice and provider agreement; (b) credential leak —
  mitigated by encryption + redaction sweeps + rotation drill; (c) sandbox
  misuse on the operator host — mitigated by the isolation boundary (G-B
  evidence) + BYO host policy decision pending; (d) backups exposure —
  mitigated by bucket policy + retention holds; residual: bucket lifecycle
  outside holds (documented).
- **Measures:** the security master prompt program itself (P0–P10 evidence
  under docs/briefs/evidence/security/); on-call + incident runbook; 72 h
  breach-notification rule (incident-response.md §4).
- **Residual risk accepted by name:** the owner's pending decisions
  (G1/G2/G3 dispositions, docs/security/byo-host-policy.md,
  worker-daemon-isolation-design.md) become part of this DPIA on signature.
