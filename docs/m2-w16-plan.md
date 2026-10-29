# Marathon 2.0 W16 — enterprise readiness (M2-W16)

Follows W15 (multi-tenant hardening shipped; one scheduled-drill leg still owed —
see `docs/m2-w15-plan.md` §Gate position). The roadmap row in one line: *SCIM +
group-mapped SSO, audit export to SIEM, customer-managed encryption keys,
residency options, pen test, counsel sign-off.* The marathon invariants that
matter here: a seat that is revoked must be revoked everywhere, an audit trail a
customer can verify without trusting us, and no claim in docs that the code does
not enforce.

## Purpose

An enterprise buyer asks four questions of this codebase. Three of the four have
honest answers today; one does not, and two of the honest answers are weaker than
their documentation implies.

1. **"Can my IdP take a seat away?"** Not yet. There is no SCIM surface, and
   `is_active=false` is advisory: it changes what notifications target, not what
   a principal may do.
2. **"Does SSO honour my directory's groups?"** Only email *(as audited;
   closed by slice 2 below)*. A group change in the IdP changed nothing in
   Harness.
3. **"Can I ship your audit trail into my SIEM?"** Pull-only. `GET
   /api/v1/audit/export` streams JSON the customer must poll, and chain
   verification is an operator CLI on PostgreSQL.
4. **"Are my data encrypted with my key, in my region?"** No on both counts, and
   the current docs are close to implying otherwise. This plan fixes the wording
   and states what is actually buildable.

## Audit (2026-10-29, source inspected)

| Area | Observation | Where |
|---|---|---|
| OIDC SSO | discovery + auth-code-with-PKCE, RS256 raw JWS verify; team-tier gated writes | `section1/src/harness_identity/sso.py:13,92-129,331` |
| Claims mapping | **email only** *(as audited; group claims shipped in slice 2)* — `email = str(claims.get("email") or "")`; role came from stored membership, never from a claim | `sso.py:741,761,789` |
| Provisioning | deliberately mapping-only: a stranger IdP subject is refused `SSO_EMAIL_NOT_MEMBER`; OTP login *does* create users | `sso.py:26,753`; `auth.py:37-41` |
| SCIM | **absent.** No endpoint, schema, table, client or token anywhere (`scim`, `/scim/v2`, `patchOp`, `externalId`, `deprovision`) | repo-wide search (2026-10-29) |
| Deactivation | `users.is_active` written false by self-deletion and erasure | `orgs.py:498`, `erasure.py:121` |
| Deactivation enforcement | **none at auth** *(as audited; closed by slice 1 below)*. `get_principal` checked session expiry + membership only; `login_or_register` selected `User` by email with no `is_active` term, so a deactivated account could request an OTP and mint a fresh session; the SSO member query did not filter `is_active` although its docstring promised "an ACTIVE human member" | `auth.py:92-117,37`, `sso.py:24,770-787` |
| Session kill on revoke | `_revoke_access` is called by the org-removal paths and self-deletion, not by erasure (erasure survives because it nulls `team_members.user_id` and rewrites the email) | `orgs.py:104,158,496,777,918` |
| Audit trail | `audit_log` sealed by DB triggers (migration 0036): `chain_pos/prev_hash/row_hash`; anchor sink `HARNESS_AUDIT_ANCHOR_PATH` every 300 s; retention 365 d | `models.py:502-522`, `audit_chain.py:51`, `deploy/runners/services.py:505`, `events_retention.py:20` |
| Audit export | `GET /api/v1/audit/export` — admin + step-up, cursor paged ≤1000, JSON only, itself audited (`audit.exported`) | `section1/src/harness_identity/app.py:721-766`, `audit_emit.py:624` |
| SIEM delivery | **absent.** No NDJSON/CSV/syslog/CEF/LEEF formatter, no push sink, no webhook of audit rows (`WEBHOOK_EVENT_TYPES` is lifecycle only) | `webhooks.py:55-75` |
| Chain verification | library + CLI, PostgreSQL-only; no customer-facing verify endpoint | `section1/scripts/verify_audit_chain.py:21,35` |
| Key material | Fernet under a salted memory-hard KDF (scrypt 2^15,8,1; argon2id preferred), 4 encrypted columns, dual-read window, `rotate_master_key` | `crypto.py:7-11,22-24,158-163,166-247` |
| Customer-managed keys | **absent.** One install-wide master key from the environment, one global KDF-params row (`id=1`); no KEK/DEK split, no KMS provider interface (`kms`, `wrapped_key`, `data_key`, `kek`, `dek`, `transit`, `seal` → no key-hierarchy hits) | `crypto.py:96,255`, `config.py:9` |
| Residency | a per-workspace **label**: allowlist-validated, stored, PATCHable — and consulted nowhere else. No region/placement/pinning code exists (single Postgres DSN) | `workspaces.py:33,51,86-89,461,554`, `config.py:7` |

## Slice 1 — a revoked seat is revoked everywhere (shipped 2026-10-29)

The prerequisite for SCIM and for any honest enterprise claim: `is_active` must
decide what a principal may do, not just what a notification targets.

* **Login refuses a disabled account.** `login_or_register` answers
  `403 ACCOUNT_DISABLED`. The message is deliberately actionable — unlike the
  no-oracle 401 a bearer surface gets, the caller here has already proven they
  control the inbox, so telling them the seat is gone is useful and not a leak.
* **Every request consults it.** Section 1's `get_principal` answers the *same*
  frozen `401 invalid or expired session` as a dead session, so a live token
  cannot be probed for account status.
* **The shared dependency is where the rule became deployment-wide.** Sections
  2, 8, 11, the deployed runners and the extensions server resolve principals
  through `harness_contracts/fastapi_auth.make_bearer_dep`, whose lookup joined
  only `auth_sessions ⋈ team_members`. It now joins `users … is_active`, so every
  one of those services refuses without a line of new service code.
* **The socket and its ticket obey it too.** `_principal_from_token` (the WS
  handshake) and `redeem_ws_ticket` had their own paths. A ticket is a
  pre-authorized handle carrying a role snapshot; its short TTL is not a
  revocation.
* **SSO honours its own docstring.** the mapping returns three outcomes, because
  "you are not a member" and "your seat was revoked" are different queues; the
  docstring had promised an ACTIVE member for months while the query never asked.
* **`issue_otp` was deliberately NOT changed.** It stays enumeration-safe:
  writes the code, answers 200 whether or not the address exists. A disabled
  account receives a code it cannot use, and the refusal arrives at
  verification.

Dropped from this slice during implementation, on evidence: the planned
"every `is_active=false` write must call `_revoke_access`". The org-removal paths
already do, and a seat rule that depends on every writer remembering a helper is
a rule that gets forgotten — the live drill revokes a seat while deleting **no
session row at all** and every door still closes. Auditing the flag flip is
deferred to slice 3, where the endpoint that writes it lives.

Evidence: 8 tests in `section1/tests/test_account_deactivation.py` — each door,
plus one proving the rule is the seat and not the clock (a session extended to
2099 still dies); 1 test in `section11/tests/test_api.py` that runs the REAL
shared contract dependency against a non-identity service; and
`make drill-seat-revocation` against the deployed containers
(`docs/briefs/evidence/pilot-prep/w16-seat-revocation-1793292990.json`, 9 checks:
the same token 200→401 on identity and 404→401 on the shared door, `403
ACCOUNT_DISABLED` on a fresh login, then 200 again after re-enabling). Three test
fixtures had to create the `users` table they never had; a fixture that omits a
table the real schema joins is not simpler, it is wrong.

Operator text: `docs/runbooks/ops.md` §3.7 (five doors, one rule, and how to
diagnose "I revoked them and they still got in").

## Slice 2 — group-mapped SSO (opt-in, never wider than the map)

`team_sso_configs` gains `groups_claim` plus `group_roles` (JSON list of
`{group, role}`, migration 0069). They are ordinary config columns — group names
are not secrets; the client secret stays Fernet-encrypted as before. Rules as
shipped:

* **off unless configured.** No claim name or no rules → the login is exactly
  the old email-match flow, and existing teams cannot change behaviour by
  upgrading.
* **the map is the ceiling, but it can promote.** Every matching `fnmatch`
  pattern is considered and the strongest role wins
  (`guest < viewer < engineer < admin`). An IdP group can move a member up or
  down the role ladder, but only through a rule a team admin wrote; there is no
  implicit mapping by group-name convention. `admin` is the top of the enum;
  team *ownership* transfer is a separate flow that SSO never touches.
* **a claim never creates access.** It cannot add a member (no JIT
  provisioning), cannot reactivate a deactivated seat (that check runs before
  mapping), and an email that matches no member still gets
  `SSO_EMAIL_NOT_MEMBER`.
* **the decided role is persisted to the membership.** Every later request
  resolves its role from `team_members`, so a login that only *answered* admin
  would be a lie. When the claim disagrees with the stored role, the login
  updates the row and writes a `member.role_changed` row (`source: sso_claim`,
  system actor). One exception: a demotion that would leave the team with ZERO
  admins is refused — the login succeeds with the stored admin role and the
  `sso.login` row says `membership_last_admin`, because the last-admin invariant
  predates SSO.
* **an unmatched member keeps the stored role.** Silence is not demotion,
  because a broken claim mapper at the IdP must not lock a whole team out. The
  corollary is explicit: once a claim has promoted someone, removing that group
  at the IdP does NOT demote them by itself, because the map says nothing about
  the base role. The directory-driven pattern is a baseline rule — map a group
  everyone carries (`everyone → engineer`) alongside the privileged group — and
  the live drill exercises exactly that (admin group removed → baseline rule →
  engineer on the next login). Manual role changes and SCIM (slice 3) remain the
  revocation path.
* **the decision is recorded on login.** A `sso.login` audit row carries
  `role`, `source` (`claim` | `membership`) and the matched group names — so
  "why is this person an admin?" has an answer that is not "ask the IdP".
  Audit failure never denies a valid login (same law as every audit helper).
* **the config API refuses dead config.** Rules whose role is not in the enum,
  an empty/whitespace group, more than 50 rules, or rules without a claim name
  are `422 VALIDATION_ERROR` at PUT time rather than silent no-ops at login.

Evidence: 16 tests in `section1/tests/test_sso_group_mapping.py` (opt-in
default, exact/glob match, strongest wins, unmatched keeps the role, claim-shape
tolerance, validation bounds, the seam's `source`/`matched`), 6 end-to-end tests
in `section1/tests/test_sso_oidc.py` (promotion + audit row, the promoted role
surviving into a real `get_principal` lookup, last-admin demotion refused,
absence keeps the role, unknown role refused, rules-without-claim refused), and
a live Keycloak drill: join the admin group → `role=admin, source=claim` and the
minted token passes the admin-only `/audit` door; leave it → the baseline rule
gives `role=engineer, source=claim` and `/audit` answers 403
(`scripts/oidc_drill.sh`, evidence in
`docs/briefs/evidence/pilot-prep/oidc-keycloak-drill.json`, 22 checks).

## Slice 3 — SCIM 2.0 subset for the seat lifecycle

Full SCIM is not the ask; the two operations an IdP actually needs are, and they
must land on slice 1's enforcement rather than beside it:

* `GET /scim/v2/Users` (+`?$filter=externalId eq …`), `GET/PATCH/PUT
  /scim/v2/Users/{id}` implementing `active` and `userName` — the minimum that
  makes "deprovision in Okta/Entra" real;
* a SCIM bearer token (team-scoped, stored encrypted, revocable) — never a user
  session token;
* `externalId` mapping onto the existing user identity, with the response shape
  SCIM clients require (`schemas`, `meta`, `id`, `externalId`,
  `userName`, `active`);
* every mutation audited, and `meta.location`/`etags` honest rather than
  decorative.

This is a contract-level surface: OpenAPI spec, both SDKs, `make check-ts-dist`
and the drift gate all move together, as `model_routed` did in W15.

## Slice 4 — audit export a SIEM can actually consume

* `format=ndjson` on the existing export endpoint: one self-contained JSON object
  per line, chain fields preserved, so a collector that appends lines is enough;
* an **outbound push sink** (HTTPS collector URL + HMAC-SHA256 signature header +
  bounded retry with backoff + a delivery cursor so a restart does not re-send or
  skip);
* a customer-facing verification endpoint (`POST /api/v1/audit/verify` with a
  cursor window) that returns the chain check result for *exported* rows, so
  tamper-evidence does not require our CLI or our database.

## Not in this week (stated, not hidden)

* **Customer-managed keys.** Today: one install-wide master key from the
  environment (`crypto.py:96`, `config.py:9`). A CMEK design needs a KEK/DEK
  split per tenant and a provider interface before it can be honest; shipping a
  `kms_key_id` column that nothing consults would be worse than saying no. The
  plan for it belongs in W17 with the design note this audit seeds.
* **Residency enforcement.** `data_residency` is a label, consulted only in
  `workspaces.py`. Real placement needs region-pinned storage and workers, which
  this deployment (one Postgres, one host) does not have. What W16 can do is stop
  the docs implying enforcement — done in `docs/billing-and-quotas.md` /
  `docs/contracts-and-limits.md` — and add a deployment-side admission check: a
  deployment that declares its region can refuse workspaces whose residency does
  not match, which is a true statement about *that* deployment.
* **Pen test.** An external engagement is a human/legal step. What can be done
  honestly is the internal adversarial pass (the `docs/clientela/raw-*.md`
  precedent) against these new surfaces, and it is scheduled with slices 2-3.
* **Counsel sign-off on the placeholder terms** — blocked on a human; tracked in
  `docs/backlog.md`, not restated as progress anywhere.

## Gate position

| W16 exit claim | Evidence required | State |
|---|---|---|
| A deactivated user cannot authenticate or act | unit per surface + live drill (old token 401, fresh login 403) | **shipped 2026-10-29** |
| SSO can map an IdP group to a role, only through an admin-written rule | mapping tests + Keycloak live drill (promote, demote, audit source) | **shipped 2026-10-29** |
| An IdP deprovisioning reaches Harness | SCIM subset + live PATCH drill | pending (slice 3) |
| A SIEM can ingest and verify the audit trail | NDJSON + push sink + verify endpoint, live proof | pending (slice 4) |
| No doc claims key or residency management the code lacks | doc diff + the audit table above | pending |
