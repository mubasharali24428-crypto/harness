# Retention table (DRAFT — for counsel review)

| Data class | Default retention | Configurable | Deletion path |
|---|---|---|---|
| agent_events (per month partitions) | retention sweep (ops-configurable; events-retention service) | yes (governance UI: event/audit retention knobs) | partition drop + purged-run sweep |
| audit_log | retention knob (governance UI), hash-chained; anchor file survives | yes | verified purge under holds |
| runs / artifacts / exports | until team purge or hold expiry; backups skip expiry under a hold | per-team holds | scoped content deletion + purge sweep |
| auth_sessions | expiry (SESSION_TTL_HOURS) + logout revocation | TTL | row delete on expiry/revoke |
| login_otps | consumed/expired rows (single-use, short TTL) | TTL fixed | consumed at use; expiry marks consumed |
| api_keys (BYOK) | until revoked by the team | — | soft-revoke (revoked_at) |
| backups (WAL-G, store) | STORE_KEEP (14 by default) / WAL-G retention | env | script expiry; bucket lifecycle is the residual gap (58) |
| account erasure | pseudonymisation (never hard-delete held runs) | — | POST /api/v1/account/erasure |
