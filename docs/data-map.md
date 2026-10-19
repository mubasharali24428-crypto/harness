# Data map (Master prompt part 31 — mapping only; enforcement is Session 8)

Built 2026-10-18 from `section1/migrations/0001–0035`, `section4/src/harness_orchestrator/retention.py`,
`deploy/runners/events_retention.py`, `PRIVACY.md`. "Reader" = the code path that serves the data;
role-level authorization was NOT re-verified here (see `capability-matrix.md`, parts 31/38).
Retention values are shipped defaults; sweeps are optional compose profiles, so unless enabled the
effective retention is indefinite.

## Database tables (Postgres, owner: section1 migrations)
| Data class | Tables | Reader (code) | Retention / deletion rule | Notes |
|---|---|---|---|---|
| Identity & sessions | `users`, `auth_sessions`, `ws_tickets`, `login_otps`, `oauth_states`, `sso_login_states`, `key_derivation_params` | identity (section1) | OTP/ticket/state rows carry `expires_at`; no documented purge of expired rows or of users | OTP codes are logged in dev mode (PRIVACY §5) |
| Teams & settings | `teams`, `team_members`, `team_invites`, `team_settings`, `user_settings`, `team_sso_configs`, `api_keys` | identity teams/settings/api_keys modules | FK deletion story in migration 0023; no scheduled purge | API keys encrypted (vault, `crypto.py`, `reencrypt_vault.py`) |
| Projects & work | `projects`, `meetings`, `meeting_messages`, `engineering_briefs`, `agent_plans`, `project_rules`, `context_chunks` (pgvector) | identity / orchestrator | none scheduled | `context_chunks` holds repo-derived text |
| Runs | `agent_runs`, `run_claims`, `run_participants`, `run_handoffs`, `pull_requests`, `review_comments`, `github_connections`, `model_configs` | orchestrator | run rows kept; `retention.py` purges staged material, not rows | `github_connections` holds tokens (encrypted) |
| Collaboration events | `run_collab_events` | orchestrator `collab.py` | deleted by `retention.py` for eligible runs (default 30 d, `HARNESS_RETENTION_DAYS`) | guidance text may contain sensitive content |
| Approvals | `high_impact_approvals` | orchestrator `staging.py` | metadata (digest, paths, state) never purged; diffs blanked by retention | |
| Event stream | `agent_events` (monthly partitions, 0028) | orchestrator + WS replay | 30 d (`events_retention.py`, partition drop) | append-only; WS replay by cursor |
| Audit | `audit_log` | identity `audit.py` route | 365 d (`AUDIT_RETENTION_DAYS`) via sidecar; never purged by `retention.py` | secret-scrubbed at emission |
| Billing & cost | `billing_plans`, `cost_ledger`, `cost_ledger_daily`, `stripe_webhook_events`, `rate_buckets` | identity billing | none scheduled | Stripe never driven against live provider (part 57 gate) |
| Delivery | `notification_outbox`, `idempotency_keys` | identity notifier | outbox 30 d (`OUTBOX_RETENTION_DAYS`) | outbox is the only notification path today |

## Files, logs, caches
| Data class | Location | Reader | Retention / deletion | Notes |
|---|---|---|---|---|
| Per-run workspace (outputs) | `P1_WORKSPACE`/`DSH_WORKSPACE` per run id | sandbox/engine worker | kept unless `include_workspace` | cross-run isolation tested (part 09) |
| Staging baseline + withheld files | engine session root (`DSH_SESSION_ROOT`) | orchestrator staging | removed by `retention.py` after run end | withheld material awaiting a decision is never deleted |
| Diff store | `diffstore.py` | orchestrator | follows run retention (unverified) | crafted-diff risk: see threat model |
| Service logs | stdout of each service | operators | operator-defined | correlation ids: see ops evidence |
| Backups / WAL | WAL-G target (`docs/runbooks/backup-and-pitr.md`) | operator | operator-defined | PITR never run on real object store (part 53 gate) |
| Frontend | browser storage / session token | user's browser | until logout | `section7/src/lib/session.ts` |

## Telemetry fields
| Field source | What it contains | Where |
|---|---|---|
| `pilot_metrics.py` CLI | per-team run counts, approval latency, interventions (derived from events) | stdout, no PII beyond ids |
| `csp_report.py` | CSP violation reports | identity logs |
| cost ledger | tokens/cost per run & team | `cost_ledger*` |

## Gaps this map exposes (input to Session 8)
No legal hold, no data-residency control, no user/account deletion or export flow, no scheduled purge for
identity tables, no per-class retention config UI. Each is `missing` in the capability matrix (parts 31, 58).
