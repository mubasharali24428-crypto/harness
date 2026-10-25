# Subprocessors — draft register

**Status: DRAFT.** No subprocessor contract is in place. Counsel must approve
the transfer mechanism before a customer signs. This register is factual about
what the software can contact; it is not an authorisation.

| # | Subprocessor | Purpose | Data it can see | Location / transfer | Contracted? |
|---|---|---|---|---|---|
| 1 | Hosting provider chosen by the operator (VPS/cloud) | Runs Postgres, services, object storage | Everything stored in the deployment, plus backups | [OPERATOR TO CHOOSE] | No |
| 2 | Object storage for backups — customer/operator-chosen S3-compatible bucket (AWS S3, Cloudflare R2, MinIO, SeaweedFS) | Base backups and WAL archive | Full database (secrets encrypted at app level only) | [BUCKET REGION] | No |
| 3 | Email / SMTP provider chosen by the operator | OTP codes and notifications | Email address, message text | [PROVIDER, COUNTRY] | No |
| 4 | OpenRouter (model routing) — **only if the customer enables it** | Routes prompts to a model | Prompts, selected code context, tool outputs, token counts | United States | No |
| 5 | DeepSeek (model provider) — **only if the customer enables it** | Executes model calls | Same as #4 | China | No |
| 6 | GitHub / GitLab — **only if a repository is connected** | Repository content, pull requests, review comments | Repo content and metadata; connection tokens are encrypted at rest | United States / EU | No |
| 7 | Customer's own identity provider — **only if SSO is configured** | Authenticates users | Name, email, group/role claims | Customer's choice | Customer-controlled |
| 8 | Support / incident tooling [none selected] | Would receive incident details | — | — | N/A |

Deliberately absent:

- **Stripe** — not used. Stripe does not operate in Pakistan; billing routes are
  unmounted without a key. If billing is later enabled, this register must add
  Stripe and the customer must approve it.
- **Analytics / telemetry SaaS** — none.
- **Keycloak / MinIO images in `deploy/`** — drill-only, never part of a
  customer deployment.

## What the customer must decide

1. Which model provider (if any) may receive its code and prompts.
2. Whether the operator's chosen hosting region meets its residency needs.
3. Whether the email provider is acceptable for identity mail.
4. Whether repository connections (GitHub/GitLab) are in scope.

## What the operator must do before a pilot

- [ ] Replace every [PLACEHOLDER] above with a real entity, country and contract.
- [ ] Obtain DPAs from hosting, storage, email and model providers, or document
      why the customer's own account is used instead.
- [ ] Enable and monitor the retention sidecars, otherwise the retention column
      in the DPA's Annex A is not true in practice.
- [ ] Record the customer's written approval of this register.
