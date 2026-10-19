# Organizations and identity (part 37)

Code: `section1/src/harness_identity/orgs.py` (router under `/api/v1/orgs`), migration `section1/migrations/0038_organizations.sql`.
Tests: `section1/tests/test_orgs.py`. The lead mounts `router` in `app.py` (not done by lane O).

## 001 Definitions (six distinct things)

| Term | What it is | Where it lives |
|---|---|---|
| Organization | The billing and policy boundary. Owns members, domain policy, SSO/MFA requirements, service identities. | `organizations`, `org_members` |
| Team | A working group inside one org (or no org yet). Holds seats (`team_members`), projects, provider keys. A team belongs to at most one org (`teams.org_id`). | `teams`, `team_members` |
| Project | A repository-backed unit of work owned by one team. | `projects` |
| Workspace | The governed place work runs for a project (states active/paused/archived/deleted). Lane W. | `workspaces` |
| Session | Two meanings kept apart: an auth session (a login, `auth_sessions`) and a collaboration session (people around runs, part 40). Org code only touches auth sessions. | `auth_sessions` |
| Run | One agent execution. Belongs to a project/workspace, never to an org directly. | `agent_runs` |

Org role is separate from team role. Team roles (admin/engineer/viewer/guest) govern a team; org roles govern the org.

## 002 Multiple orgs and teams
`org_members` is keyed `(org_id, user_id)`; a user can hold a role in any number of orgs and teams. `GET /orgs` lists the caller's active orgs. Revocation is per org: leaving org A kills only sessions on A's teams.

## 003 Org roles and invariants
Roles: `owner`, `admin`, `billing_admin`, `member`, `guest`.
- Owner and admin manage members, invites, service identities, domain policy, history.
- billing_admin and member can list members; billing_admin has no management rights (billing endpoints belong to the billing module, which can ask `org_role`).
- Guests see only the org itself (`GET /orgs/{id}`); no member list.
- An org always keeps at least one owner (409 `LAST_OWNER` on demote, remove, leave, or identity deletion). Checked under an org row lock.
- Admins cannot grant owner, change an owner, or remove an owner (403). Only an owner changes owners. Invites can never carry `owner`.
- Only an owner changes `require_sso` / `require_mfa`.
- Non-members and foreign org ids get 404, never 403 (no existence oracle).

Helper for lane P: `org_role(db, org_id, user_id) -> str | None` (None for non-members, removed members, expired guests, inactive orgs). It expires due guests as a side effect (audited and committed).

## 004 Invitations and domain policy
`POST /orgs/{id}/invites` (returns the raw token once; only the SHA-256 is stored), `GET` list with derived status (sent/redeemed/expired/revoked), `DELETE /orgs/{id}/invites/{inviteId}` revoke, `POST /orgs/invites/accept`. Expiry is 7 days. Accepting is bound to the invited mailbox (a different account gets 404) and redeems atomically. `allowed_domains` (set through `PATCH /orgs/{id}`) restricts non-guest invites at mint time and again at accept time. Guests are outside the domain policy by design (they are external people) but always expire.

## 005 Recovery, session expiry, sign-out
Existing and unchanged: OTP login/recovery (`auth.py`), session TTL 14 days, `POST /auth/logout` deletes the session. Lane O tests confirm an expired session and a signed-out session both get 401 on the org API. There is no separate password-recovery flow: sign-in is by emailed code.

## 006 SSO and MFA
What exists: team-level OIDC SSO (`sso.py`, migration 0033), live IdP exchange. There is no TOTP/WebAuthn code in the repo.
Added: `organizations.require_sso` and `require_mfa`, plus `org_session_assurance(token_hash, sso, mfa)`. Every org route except the owner's flag changes demands the flagged assurance on the presenting session (403 `SSO_REQUIRED` / `MFA_REQUIRED`). The owner can always change the flags (break-glass). The completion paths must call `mark_session_assurance(db, token_hash, sso=True)` (SSO) or `mfa=True` (future MFA); until the lead wires that in `sso.py`'s `_mint_session`, turning the flags on locks non-owner sessions out of org routes, which fails closed. Live IdP flows stay externally gated.

## 007 Organization history
`GET /orgs/{id}/history` (owner/admin) reads `audit_log` rows with `target_type="org"` and `target_id=<org id>`: org.created/updated, member added/role changed/removed, guest added/expired, domain policy, team linked, service identity created/revoked, invite minted/redeemed/revoked. Role and removal rows carry `before` and `after`; `actorUserId` is in `detail`.

## 008 Guests
A guest has a mandatory `expires_at` (default 7 days, max 90, set at invite or role change). Every membership read (`org_role`, list, history, accept) sweeps due guests: row removed, sessions on org teams and their service identities revoked, one `org.guest_expired` audit row (exactly once). A role change away from guest clears `expires_at`.

## 009 Service identities
`POST /orgs/{id}/service-identities` (owner/admin) returns `hsi_...` once; only the hash is stored. Scopes come from a closed list (`runs:read`, `runs:write`, `artifacts:read`, `members:read`, `audit:read`, `webhooks:send`); anything else, including owner/admin/org scopes, is 422. A service identity has no `org_members` row so it never holds an org role, and it cannot call the human org API. `GET /orgs/service/whoami` authenticates a token and records `last_used_at`. Revoke with `DELETE`. Optional expiry. Created-by is tracked so the creator's departure revokes it.

## 010 Prompt revocation
One transaction deletes the user's `auth_sessions` on the org's teams, their `team_members` seats on those teams, and revokes service identities they created, then writes the audit row. Triggers: member removal or leave, guest expiry, org deletion (`DELETE /orgs/{id}`, owner), team departure (`DELETE /orgs/{id}/teams/{teamId}/members/{userId}`), identity deletion (`DELETE /orgs/me/identity`: all sessions everywhere, deactivates the user). Tests assert the very next request returns 401.

## Known gaps (honest)
- Team seats referenced by an audit row (append-only FK) are not deleted; sessions are still revoked, but such a user could sign in again to that team through the passwordless flow. Closing it needs a soft-remove column on `team_members` (models.py is frozen this session).
- `require_sso` is satisfiable: `sso._mint_session` calls `mark_session_assurance(sso=True)` (test_orgs.py). `require_mfa` is not: no MFA factor exists yet and nothing marks `mfa=True`.
- Provider API keys (`api_keys`) are team-owned and not user-bound, so they are not revoked on user departure; service identities are.
- `teams.org_id` is added by migration only; the ORM `Team` model does not know it.
- Concurrent owner changes are serialized by an org row lock on Postgres; the org/policy/workspace happy paths ran on real Postgres (`test_session3_pg.py`), the concurrent-owner race did not.
- Live SSO and Stripe billing stay externally gated.
