# Data map (Master prompt parts 31/52/58 — updated for Session 8)

Updated 2026-10-19 after reviewing migrations 0001–0056, `section4/src/harness_orchestrator/retention.py`,
`section4/src/harness_orchestrator/exports.py`, `deploy/runners/events_retention.py`, and the security
and privacy paths referenced below. "Reader" = code path serving the data; each authorization claim
still depends on the targeted tests referenced by parts 31/50/52. Retention runners are opt-in
compose profiles; unless operators enable and monitor them, expired data remains stored.

## Database tables (Postgres, owner: section1 migrations)
| Data class | Tables | Reader (code) | Retention / deletion rule | Notes |
|---|---|---|---|---|
| Identity & sessions | `users`, `auth_sessions`, `ws_tickets`, `login_otps`, `oauth_states`, `sso_login_states`, `key_derivation_params` | identity (section1) | OTP/ticket/state rows carry `expires_at`; no general scheduled purge of expired rows or account deletion flow | Dev OTP console output exists only in explicit dev transport; production SMTP errors log type/status, not message contents (`section1/src/harness_identity/notifier.py`) |
| Teams & settings | `teams`, `team_members`, `team_invites`, `team_settings`, `user_settings`, `team_sso_configs`, `api_keys` | identity teams/settings/api_keys modules | FK deletion story in migration 0023; no scheduled purge | API keys encrypted (vault, `crypto.py`, `reencrypt_vault.py`) |
| Projects & work | `projects`, `meetings`, `meeting_messages`, `engineering_briefs`, `agent_plans`, `project_rules`, `context_chunks` (pgvector) | identity / orchestrator | none scheduled | `context_chunks` holds repo-derived text |
| Runs | `agent_runs`, `run_claims`, `run_participants`, `run_handoffs`, `pull_requests`, `review_comments`, `github_connections`, `model_configs`, `run_retention_holds` | orchestrator | run rows kept; `retention.py` purges staged material only for eligible, unheld runs | Team admins can create/read/release run holds through the audited Section 4 API; Section 1 event/audit pruning and PostgreSQL event-partition removal preserve held-run evidence. `github_connections` holds tokens (encrypted) |
| Collaboration events | `run_collab_events` | orchestrator `collab.py` | deleted by `retention.py` for eligible runs (default 30 d, `HARNESS_RETENTION_DAYS`) | guidance text may contain sensitive content |
| Approvals | `high_impact_approvals` | orchestrator `staging.py` | metadata (digest, paths, state) never purged; diffs blanked by retention | |
| Event stream | `agent_events` (monthly partitions, 0028) | orchestrator + WS replay | 30 d deployment default via events-retention runner; orgs can request shorter windows in 0055 | runner is opt-in; held-run events are excluded from global deletes and prevent dropping any partition that contains held evidence; WS replay is bounded by stored rows |
| Audit | `audit_log` | identity `audit.py` route | 365 d deployment default via sidecar; orgs can request shorter windows in 0055 | append-only/hash-chain while retained; run-targeted audit rows survive while their run hold exists; retention is opt-in and shorter org window does not override global purge |
| Billing & cost | `billing_plans`, `cost_ledger`, `cost_ledger_daily`, `stripe_webhook_events`, `rate_buckets` | identity billing | none scheduled | Stripe never driven against live provider (part 57 gate) |
| Delivery | `notification_outbox`, `idempotency_keys` | identity notifier | outbox 30 d (`OUTBOX_RETENTION_DAYS`) | outbox is the only notification path today |

## Files, logs, caches
| Data class | Location | Reader | Retention / deletion | Notes |
|---|---|---|---|---|
| Per-run workspace (outputs) | `P1_WORKSPACE`/`DSH_WORKSPACE` per run id | sandbox/engine worker | Section 4 run-retention CLI is dry-run unless `--apply`; workspace output is kept unless `--include-workspace`; held runs are excluded | User deletion and backup expiry remain separate workflows and do not honor run holds |
| Staging baseline + withheld files | engine session root (`DSH_SESSION_ROOT`) | orchestrator staging | removed by `retention.py` after run end unless the run has a hold | withheld material awaiting a decision is never deleted |
| Diff store | `diffstore.py` | orchestrator | follows run retention (unverified) | crafted-diff risk: see threat model |
| Service logs | stdout of each service | operators | operator-defined | correlation ids: see ops evidence |
| Backups / WAL | WAL-G target (`docs/runbooks/backup-and-pitr.md`) | operator | operator-defined; backup expiry is separate from live-row retention | PITR never run on real object store (part 53 gate); deleted data may remain in backups until expiry |
| Frontend | browser storage / session token | user's browser | until logout | `section7/src/lib/session.ts` |

## Telemetry fields
| Field source | What it contains | Where |
|---|---|---|
| `pilot_metrics.py` CLI | per-team run counts, approval latency, interventions (derived from events) | stdout, no PII beyond ids |
| `csp_report.py` | CSP violation reports | identity logs |
| cost ledger | tokens/cost per run & team | `cost_ledger*` |

## Gaps confirmed in Session 8

- Migration 0056 adds run holds managed by team admins through Section 4's audited API. Section 4
  run purge, Section 1 event/audit pruning and PostgreSQL partition retirement honor active holds.
  User deletion requests and backup expiry remain separate workflows and do not honor run holds.
- No tenant-specific physical residency routing. Workspace `dataResidency` is a declared workspace
  field; it does not route databases, event partitions, exports, provider calls, or backups.
- No account deletion workflow or identity-table expiry sweep. Part 50 deletion is an authored-content
  erasure workflow with documented retained records, not full account erasure.
- Event/audit retention is only active when the opt-in retention sidecar is deployed and monitored.
- Section 4 now pins the SDK `sdk-minimal` profile and adds a per-run `workspace-write` policy overlay
  after operator patches. Runs whose approved plan has non-empty `forbiddenPaths` are refused before
  invoking the SDK. The live SDK shell still does not pass through Section 5's command allow-list and
  cannot selectively enforce paths; the installed SDK callback is post-dispatch notification only,
  so no common authorization adapter exists.
- External model/provider handling, encryption at rest/in transit, and backup expiration depend on
  deployment/provider controls and have not been independently verified here.
