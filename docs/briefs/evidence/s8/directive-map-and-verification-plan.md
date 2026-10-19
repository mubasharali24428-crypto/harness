# Session 8 — directive map and verification plan

Prepared 2026-10-19 before implementation. Sources: master prompt parts 31, 52 and 58; current
capability matrix; `docs/data-map.md`; `docs/briefs/threat-model.md`; and the Section 6 facts-that-bite
list in `PROJECT-STATE.md`. The Session 7 prerequisite commit is present in local history as
`f583d89`; Session 7 continuation edits are still uncommitted and will be kept separate.

## Part 31 — data and privacy

| Directive | Current code/evidence | Verification planned |
|---|---|---|
| 001 collect minimum collaboration data | `docs/data-map.md`; event and audit schemas | inspect event payload constructors and redact/minimize tests |
| 002 bounded event payloads | Section 1 event bus, Section 4 event emitters | search limits/schema and payload-size tests |
| 003 never log credentials | Section 1 logging/auth, Section 4 worker logging, `docs/PRIVACY.md` | secret redaction tests and targeted logs/auth tests |
| 004 prompts/generated content sensitive | run, project rules, plans, artifacts and export stores | trace access boundaries and export sanitization tests |
| 005 run/audit readers defined | Section 1 audit APIs; Section 4 run and export guards | authz tests for same-team, cross-team and org membership |
| 006 retention/deletion behavior | `deploy/runners/events_retention.py`, Section 4 retention, migration 0023, S7 settings | retention and deletion tests; inspect legal hold interaction |
| 007 exported evidence protected | Section 4 exports/artifacts | export authorization and expiration tests |
| 008 synthetic demo data | demo fixtures and demo runner | inspect for synthetic-only guarantees |
| 009 telemetry explained | `docs/data-map.md`, pilot metrics | check docs against actual emitted fields |
| 010 no unauthorized external analytics | telemetry and provider paths | search for outbound analytics and document provider boundaries |

## Part 52 — security and privacy program

| Directive | Current code/evidence | Verification planned |
|---|---|---|
| 001 threat model | `docs/briefs/threat-model.md`, prior threat-model evidence | reconcile current controls and residual threats |
| 002 transit/at-rest encryption | TLS configuration, vault encryption, database/storage deployment | inspect config/docs; no claim of platform encryption without operator evidence |
| 003 tenant isolation | authz, queries, event stream, exports, object/workspace stores | cross-tenant invariant tests and SQL predicate review |
| 004 dedicated secret store | Section 1 vault and encrypted provider credentials | inspect storage boundaries and secret exposure tests |
| 005 rotate/revoke credentials | API keys, OAuth/provider tokens, webhooks | rotation/revocation tests; identify gaps |
| 006 least-privilege service identities | service identity scopes, deploy services | inspect scopes, deployment credentials and permissions |
| 007 CSRF/XSS/SSRF/injection/file safety | auth/security headers, webhook allowlist, Section 5 path policy | targeted security tests and bypass review |
| 008 dependency scans/review/response | lockfiles, CI scripts, security runbooks | inspect existing scanning configuration; no external audit claim |
| 009 incident response/evidence/customer comms | runbooks and audit export | find/document gaps without inventing process |
| 010 publish verified posture | security/privacy docs | align claims with verified code and operator controls |

## Part 58 — compliance and data lifecycle

| Directive | Current code/evidence | Verification planned |
|---|---|---|
| 001 target markets/obligations | privacy/terms docs | document unresolved counsel decision; do not infer applicability |
| 002 personal/sensitive data map | `docs/data-map.md` | refresh against current migrations and providers |
| 003 retention, legal hold, backup expiry | event/audit retention, Section 4 run retention, migration 0056, audited hold API, backup runbook | review that run-file/event/audit retention and partition retirement honor holds; record user-deletion and backup-expiry gaps |
| 004 export/deletion workflows | Section 4 search/export and org/user deletion paths | verify owner auth, scope and deletion coverage |
| 005 subprocessors/model-provider handling | provider config and privacy docs | reconcile actual providers and disclosures |
| 006 residency controls | deployment topology | explicitly document deployment-wide region only |
| 007 preserve audit/minimize content | audit scrubber, audit retention/export | scrub and retention tests |
| 008 periodic access review | org membership and service identity controls | inspect for review workflow; document missing if absent |
| 009 regulated change/release evidence | migrations, checkpoints, CI | verify local records, no regulated-customer claim |
| 010 no premature compliance claim | product/privacy/marketing docs | scan claims and report external gates |

## Verification plan

1. Read-only inspect the directive-relevant source, tests, docs and migration sequence without touching
   unrelated dirty files.
2. Add focused invariant tests only for concrete gaps that can be fixed safely in this pass.
3. Run those test files and Ruff for touched sections; run typecheck only if Section 7 changes.
4. Run `git diff --check` and review exact file paths. Do not run broad destructive cleanup, migration
   rollback, full-stack networking, or overwrite existing evidence.
5. Record external evidence separately: independent pentest, counsel-reviewed terms/privacy, live
   data-residency proof, and production platform encryption/backup evidence remain gated.
