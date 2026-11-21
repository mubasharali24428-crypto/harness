# Security questionnaire — draft answers

**Status: working answers, updated 2026-10-25.** Written for a design-partner
security review. Every "not yet" is intentional; do not delete a limitation to
make an answer look better.

**Week 4 reconciliation (2026-11-12):** this file predates the current RC evidence.
Before sending it to a partner, reconcile it with `docs/briefs/evidence/90day/week-03.md`,
`week-04.md`, `docs/pilot-remediation/A01-DISPOSITIONS-2026-11-11.md` and
`W4-COUNSEL-HANDOFF.md`. In particular, do not claim that container mounts enforce
within-workspace read scope, that every backend is network-confined, or that historical
drills certify the current image set. Billing remains disabled; payment-rail eligibility
is unresolved in `../decisions/2026-11-12-payment-rail-P-D2-DRAFT.md`.

## Company and certifications

| Question | Answer |
|---|---|
| Do you hold SOC 2 / ISO 27001 / PCI / HIPAA? | **No.** No certification or independent audit exists. |
| Have you had a penetration test? | **No.** Internal threat modelling and adversarial tests exist; an external pen test has not been commissioned. |
| Who is the contracting entity? | [OPERATOR TO COMPLETE] |
| Where is data hosted? | Wherever the operator deploys the compose stack and the customer's bucket. No enforced tenant residency. |

## Architecture

| Question | Answer |
|---|---|
| What is the deployment model? | Single-tenant compose stack per customer (Postgres, seven services, sandbox, optional Caddy edge). The customer can host it or the operator can. |
| SaaS multi-tenant? | No. Teams are isolated inside one deployment by team-scoped authorization, not by separate infrastructure. |
| Which model providers are contacted? | Only one the customer configures. OpenRouter + DeepSeek were used in the recorded drills. Prompts and selected code leave the deployment. |
| Is customer code used for training? | The product sends no training data. The chosen model provider's terms govern its copy. |
| Can the model write to the repo? | Only inside a per-run workspace, after a human approves the plan. Changes return as diffs and checkpoints; nothing is pushed without an explicit review/merge action. |

## Access and identity

| Question | Answer |
|---|---|
| Authentication | Passwordless email OTP by default; OIDC SSO (PKCE + nonce + JWKS) for Team tier. |
| MFA | OTP possession is the second factor; there is no TOTP/WebAuthn option yet. |
| Roles | Team-scoped admin / engineer / viewer / guest, enforced server-side. |
| Session lifetime | Configurable; defaults documented in `docs/sessions.md`. |
| Employee access to customer data | The operator with database access can read stored data. There is no customer-managed encryption key or per-tenant KMS. |

## Sandbox and isolation

| Question | Answer |
|---|---|
| How is agent code execution isolated? | Seatbelt on macOS hosts; Landlock on Linux; an optional per-task Docker boundary (read-only root, dropped capabilities, no network by default). |
| Proven on Linux? | Yes: Landlock read/write suites run in CI, and a native-Linux container drill was added 2026-10-25. That drill found and fixed a read-allow-list bug that had granted `/` for `/bin/sh`. |
| Proven on macOS Docker Desktop? | **No.** Docker Desktop virtiofs mounts cannot enforce Landlock writes. macOS host execution uses Seatbelt; container tool writes are not supported there. |
| Egress control | Network off by default; opt-in allow-list CONNECT proxy for pinned registries/GitHub. Not yet exercised against a live Docker daemon. |
| Can a run read another team's files? | Not through the supported paths; sibling workspaces are outside the read allow-list and tests cover it. The boundary is host/kernel dependent. |

## Data protection

| Question | Answer |
|---|---|
| Encryption in transit | TLS at the edge; internal compose traffic is plaintext on a private bridge. |
| Encryption at rest | Database volume depends on the host; app-level encryption covers provider keys, GitHub tokens and SSO secrets. **Backups are not encrypted by the app.** |
| Retention | Collaboration and agent events 30 days, audit 365 days by default, tokens swept; the retention sidecar is opt-in and must be enabled and monitored. |
| Deletion | Account erasure pseudonymises the user and removes credentials/sessions/seats/mail. Authored text needs a separate deletion request. Backups expire on their own schedule. |
| Residency | Not enforced. The workspace field is declarative. |
| Backups | Base backup + WAL archiving, drilled against S3-compatible storage on 2026-10-25 (base 2 s, fetch 1 s, replay 1 s on dev-sized data). Restores are not encrypted by the app. |

## Operations

| Question | Answer |
|---|---|
| Monitoring | Prometheus + Alertmanager profiles ship; SLOs in `docs/runbooks/slo-and-capacity.md`. |
| Incident response | Runbooks exist (`docs/runbooks/`). No 24/7 on-call; pilot support is business-hours unless the SOW says otherwise. |
| Availability SLA | None during the pilot. |
| Change management | CI gates on every PR; branch protection with required checks; real-DB migration gate. |
| Supply chain | Pinned dependencies, secret scanning, pip-audit (warn posture). |
| Billing | Stripe adapter present but flag-off (unavailable in Pakistan). |

## Vendor and legal

| Question | Answer |
|---|---|
| Subprocessors | See `SUBPROCESSORS.md`; no contracts are in place yet. |
| DPA | Draft in `DPA_DRAFT.md`, not reviewed by counsel. |
| Insurance | [OPERATOR TO COMPLETE] |
| Breach notification | Target 72 hours from confirmation; counsel must confirm. |

## Known gaps a reviewer should test

1. Backups and object storage encryption.
2. Cross-border model-provider transfer, especially DeepSeek (China).
3. Retention sidecars actually enabled and monitored.
4. Docker Desktop/macOS container isolation (unsupported; do not accept claims otherwise).
5. Hosted customer IdP claims mapping.
6. Operator database access (no customer-held keys).
