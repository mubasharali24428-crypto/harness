# Data-flow map per subprocessor (DRAFT — for counsel review)

Pilot deployment shape: single operator host (macOS or one Linux host), RC
stack, zero third-party SaaS beyond the listed ones.

| # | Data class | Where it lives | Subprocessor | Flow |
|---|---|---|---|---|
| 1 | Team/user identity (emails, names) | harness DB (Postgres, local volume) | none (self-hosted) | in: OTP email via operator SMTP; out: none |
| 2 | Provider API keys (BYOK) | api_keys table, Fernet-encrypted under the master key | the provider named by the key (e.g. OpenAI/OpenRouter/DeepSeek) | out: model calls only, from the worker, scoped to the team's own key |
| 3 | Repo content under test | workspace volume (runs) | none | never leaves the host except to the model provider as prompts |
| 4 | Run events / audit rows | agent_events, audit_log (hash-chained) | none | UI reads only |
| 5 | Webhooks/SIEM destinations | integration_webhooks, siem_sinks (allow-listed, HTTPS-only) | customer-operated endpoints | out: signed event payloads to customer URLs only |
| 6 | Backups | WAL-G archive (file dir or customer S3 bucket) + store snapshots | customer's bucket provider (if cloud) | encrypted at rest by the bucket policy; contains DB content incl. table 1 |
| 7 | Email (OTP, alerts) | operator SMTP | operator's email provider | OTP codes (short-lived, hashed at rest) + alert text |

No subprocessor receives repo content except the model provider the team
itself configures. No cross-team flows exist (team-scoped queries throughout).
