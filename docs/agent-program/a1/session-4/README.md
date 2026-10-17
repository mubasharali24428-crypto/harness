# A1-S4 — Supply-chain trust, enterprise provisioning and lifecycle policy

Owner A1. Features F097 F098 F099 F100 F145 F147. Risks VR019 VR047 (+VR011/VR015/VR017).
Migration `0037_trust_scim_retention.sql` (next free number was 0037 after S2=0035, S3=0036).

## Built (section1, `/api/v1/identity`)
- F097 `package_trust.py` + `/package-trust/*`: Ed25519 signature over the canonical manifest
  {name, version, sha256, issuer, keyId, issuedAt[, notAfter]}. Trusted only if: digest matches the bytes, keyId
  is an ACTIVE key in THAT team's trust store, issuer matches, signature verifies, not expired, not revoked by
  (name, version) or digest. No allow-by-default catalog. `bytesBound=true` only when the server hashed uploaded
  bytes (<=8MB); digest-only mode is reported as not byte-bound.
- F098 `security_ops.correlate` + `GET /security/correlations`: per-actor rules over audit metadata —
  repeated_denials (5/10min), denials_then_credential_change (critical), mass_revocation. Team scoping via
  `detail.teamId` (audit_log has no team column), newest 5000 rows.
- F099 `security_ops.propose_remediation` + `POST /dependency-remediation/propose`: from a caller-supplied
  advisory feed; proposals only (`autoApply:false`, gates tests_pass + manual_review [+ major_upgrade_review]).
  No feed fetching, no installs, no PRs.
- F100 `POST /policy/simulate`: RBAC (any role), team model policy, outbound content (secret/DLP/injection),
  egress URL, sensitive path in one call; read-only (`mutated:false`, writes no audit rows); DNS not resolved
  (`dnsChecked:false`).
- F145 `scim_api.py`: SCIM 2.0 Users (list+filter `userName eq`, get, create, PATCH active/roles/displayName,
  DELETE), ServiceProviderConfig; per-team hashed bearer tokens (admin mints/revokes). SCIM cannot grant admin
  (only engineer/viewer), cannot edit an admin's role, cannot deactivate the last active admin, cannot see
  other teams. Deprovision = `team_members.deactivated_at` + delete that team's sessions; row/audit history kept;
  `active:true` restores the prior role; global `users` row untouched.
- F147 `/retention` (+`/plan`, `/apply`): per-team and per-project policy (7..3650 days, resource `agent_events`),
  legal hold (blocks shortening and deletion; a team-wide hold covers all projects), dry-run plan, apply requires
  `confirm` + `expectedCount` == current plan, <=10,000 rows per call, audited. Apply is IRREVERSIBLE here
  (restore = operator backup runbook).

## Defects found and fixed
- `auth.get_principal`: a session whose membership row was gone raised an unhandled NoResultFound (HTTP 500), and
  inactive users were never rejected. Now 401 for missing/deactivated membership and inactive user.
- Existing member removal (`teams_api`) deletes the row although `audit_log.actor_member_id` is ON DELETE RESTRICT
  (fails for members with audit history on Postgres) — NOT changed (out of S4 scope); SCIM avoids it by deactivating.
  `_count_other_admins` now ignores deactivated admins.

## Verification (2026-10-17)
- section1: 679 passed / 10 skipped (baseline 639/10; +40 across S1-S4), ruff clean. S4 adds 14 tests in
  `tests/test_governance_s4.py` (bad/forged/foreign-key/revoked signatures, substitution of bytes/version/issuer,
  expiry, cross-team trust, SCIM escalation/cross-tenant/admin immutability/last-admin/offboarding token
  invalidation/reactivation, correlation negatives + team scoping, remediation gating, simulator composition and
  non-mutation, retention hold/confirm/plan-staleness/cross-tenant). A mutation of the last-admin guard was
  confirmed to fail its test. FK-inventory guard 47->56.
- All 37 migrations apply cleanly on a throwaway local Postgres DB (dropped).
- NOT done: real IdP (Okta/Entra) SCIM or OIDC drill; real PG run of the app tests; list-members endpoints still show
  deactivated members; OTP/SSO login can still mint a session for a deactivated member (every request is then 401).

## Dispositions
| F | Status |
|---|---|
| F097 | VERIFIED IMPLEMENTED (verification+trust store+revocation) — NEEDS INTEGRATION: installers (A5 extensions, A2 agents) must call `/package-trust/verify` and hash what they install; no publisher tooling beyond `sign_manifest` |
| F098 | PARTIAL: rule-based correlation over audit metadata; no streaming engine/alerts; other lanes' events not ingested |
| F099 | PARTIAL: advisory-driven proposals only; no feed, no test execution, no PR creation |
| F100 | PARTIAL: composes RBAC/model/content/egress/file checks; capability grants and residency not simulated |
| F145 | PARTIAL: SCIM server + deprovision semantics proven locally; NEEDS EXTERNAL VALIDATION against a real IdP |
| F147 | PARTIAL: policy API + hold + dry-run/apply for `agent_events`; other resources and a console UI not covered |
Risks: VR019 PARTIAL CONTROL (SCIM/session hardening local; real IdP/webhook/OAuth drills pending). VR047 PARTIAL CONTROL
(retention+hold controls exist; residency/compliance acceptance is a human/legal validation).

## Rollback
Remove routers from `app.py`; `DROP TABLE retention_policies, scim_tokens, package_revocations, package_trust_issuers;
ALTER TABLE team_members DROP COLUMN deactivated_at;` (revert the `auth.py` join first or sessions fail to resolve).
