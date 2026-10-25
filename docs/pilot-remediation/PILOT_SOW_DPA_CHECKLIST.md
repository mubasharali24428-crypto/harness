# Private pilot SOW / DPA checklist

Status: DRAFT — **COUNSEL REVIEW REQUIRED** for every item below. This is an
operator preparation checklist, not a contract, legal advice, or a claim that
the platform is legally sufficient.

## Scope and acceptance

- [ ] **COUNSEL REVIEW REQUIRED:** identify the contracting entity, pilot team,
  services, environment, countries, support contacts, and pilot dates.
- [ ] **COUNSEL REVIEW REQUIRED:** acceptance criteria: at least 10 real tasks
  completed unaided; explicit human plan approval before execution; audit and
  cost evidence available per run; no confirmed sandbox escape; and a written
  continue/stop decision at the end of four weeks.
- [ ] **COUNSEL REVIEW REQUIRED:** define exclusions: no production secrets,
  regulated data, payment-card data, or Linux sandboxed execution during this
  pilot.

## Data processing

- [ ] **COUNSEL REVIEW REQUIRED:** identify controller/processor roles and the
  permitted data categories, including source code, prompts, transcripts,
  identity data, audit events, and spend metadata.
- [ ] **COUNSEL REVIEW REQUIRED:** record data retention, deletion requests,
  backup expiry, legal holds, export format, and deletion verification.
- [ ] **COUNSEL REVIEW REQUIRED:** list subprocessors and hosting locations,
  including Postgres/object storage, email/OTP, GitHub, model providers, and
  support tools. No provider is approved merely because it appears in this
  checklist.
- [ ] **COUNSEL REVIEW REQUIRED:** document model-provider data flow, provider
  retention/training settings, transfer mechanism, and whether prompts/code may
  leave the pilot team's infrastructure.
- [ ] **COUNSEL REVIEW REQUIRED:** define incident classification, notice clock,
  contacts, cooperation, evidence preservation, and customer notification.

## Commercial and risk allocation

- [ ] **COUNSEL REVIEW REQUIRED:** price, taxes, payment rail, refunds,
  renewal/termination, support scope, SLA, and pilot conversion terms.
- [ ] **COUNSEL REVIEW REQUIRED:** confidentiality, IP ownership, acceptable
  use, open-source license interaction, limitation of liability, warranties,
  indemnities, and security-claim wording.
- [ ] **COUNSEL REVIEW REQUIRED:** governing law, venue, dispute process,
  sanctions/export restrictions, and data-return obligations at termination.

## Operator evidence before acceptance

- [ ] real master key and backups configured; no secrets in examples or logs;
- [ ] one macOS Seatbelt host is used for sandboxed execution;
- [ ] local/mock model provider only, unless a separately approved provider
  drill is recorded;
- [ ] retention/deletion and restore drill evidence is attached to the SOW;
- [ ] the customer has reviewed the current `BASELINE_2026-10-21.md` and the
  explicit blockers, including durable-state and provider limitations.
