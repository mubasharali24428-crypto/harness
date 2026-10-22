# Privacy — AI Engineering Harness

> **Status: DRAFT — COUNSEL REVIEW REQUIRED.** Real, code-backed statements about where data lives
> and how it moves; `[PLACEHOLDER: counsel review]` everywhere legal judgment
> (GDPR/CCPA roles, DPAs, retention law) is required. Not legal advice; not
> production-ready until placeholders are resolved by qualified counsel.

## 1. The short version

Self-hosted first: **your data stays in your Postgres, on your
infrastructure.** The Platform ships no phone-home channel and no third-party
analytics SDK. Everything below describes what the software itself stores and
emits inside the boundary you deploy it in.

## 2. What data the Platform stores (all self-hosted)

| Data | Where | Notes |
|---|---|---|
| Account identity | `users` table (Section 1) | Email address for passwordless OTP login; display name; role |
| Team & membership | `teams`, `team_members` | Org unit owning projects/budgets/keys |
| Plans, briefs, run events | `agent_events` + section tables | Append-only event-sourced audit trail; the platform's core record |
| Spend records | `cost_ledger`, `cost_ledger_daily` | Per-model-call cost rows (model, tokens, USD) — no prompt text |
| Audit log | `audit_log` | Compliance-grade append-only record of sensitive operations |
| Model API keys | Section 3 KeyVault, encrypted under your `HARNESS_MASTER_KEY` | BYO keys; never logged, never sent to model context (`AGENTS.md` law) |
| Code content | Your git repos + engine workspaces (`DSH_WORKSPACE`) | Processed transiently during runs; not copied out of your infra |

## 3. What leaves your machine

By default, nothing to US — but note what leaves YOUR box as part of normal
operation:

- Model provider calls: approved plan execution sends prompts/code context to
  the model endpoint YOU configure (`DEEPSEEK_BASE_URL`; local Ollama keeps it
  on-machine). Provider privacy posture is whatever that endpoint's policy is.
- GitHub API calls: PR/diff operations use the OAuth app / token YOU register.

### Subprocessors and provider flow

There is no committed hosted subprocessor list. A pilot operator must record
every model provider, email provider, GitHub integration, hosting provider,
backup store, and support vendor used for that pilot. What prompt/code context
leaves the operator boundary, the provider's retention/training posture, and
international transfer terms are **COUNSEL REVIEW REQUIRED** before use.

## 4. Telemetry (as implemented)

Source of truth:
`section10/src/harness_onboarding/telemetry.py` (server-side enforcement).

- **Baseline telemetry ON by default:** error logs, usage analytics, prompt
  *metadata* (model name, token counts), code *metadata* (files-changed
  counts, languages).
- **Full prompt text / full code content: OFF until explicit opt-in** with an
  acknowledgment flag; a request trying to enable them without acknowledgment
  is rejected server-side, not just hidden in UI.
- Self-hosted operators can disable baseline collection entirely via their own
  configuration.

`[PLACEHOLDER: counsel review]` — if a hosted variant ever collects telemetry
off-box: lawful basis, cookie/analytics disclosures, DPA, subprocessor list,
international-transfer mechanism (SCCs etc.).

## 5. Login mechanics

Passwordless email OTP (and optionally GitHub OAuth for repo connection).
Dev deployments without SMTP print OTP codes to local container logs — dev
mode only. `[PLACEHOLDER: counsel review]` — consent language at signup,
marketing-communications opt-in, and account-deletion SLA once hosted
accounts exist.

## 6. Retention (defaults as shipped)

Configurable via the events-retention sidecar
(`deploy/runners/events_retention.py`); defaults:

| Store | Default retention |
|---|---|
| `agent_events` (+ notification outbox) | 30 days |
| `audit_log` | 365 days |

Retention sweeps are optional compose profiles — if you never enable them,
data persists indefinitely in YOUR database. Deletion is therefore always
under the operator's control. `[PLACEHOLDER: counsel review]` — statutory
retention obligations (e.g. tax/commercial-law minimums) vs. GDPR storage-
limitation for hosted offerings; backup (WAL-G) restore-window disclosure.

Deletion requests, backup expiry, legal holds, export format, and incident
notice timelines are **COUNSEL REVIEW REQUIRED**. The software defaults are
not a legal retention promise.

## 7. Security measures (factual, not a certification)

Sandboxed execution with command allow-list and egress pinning (Section 5),
fail-closed bearer auth everywhere, rate limiting on expensive endpoints,
append-only event store, secret-scrubbed audit emission, gitleaks + pip-audit
in CI. These are engineering facts from the codebase — **not** a SOC 2 or ISO
attestation, and nothing here should be quoted as one.

`[PLACEHOLDER: counsel review]` — security-page claims review before any
enterprise marketing uses this list.

---

Related: [`TERMS.md`](TERMS.md) · [`LICENSE`](LICENSE) · [`PRICING.md`](PRICING.md)
