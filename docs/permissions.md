# Permissions and policy model (part 38)

Code: `section1/src/harness_identity/policy.py` (pure engine + `router`), migration `0039_policy.sql`.

## Model

- Levels (outer to inner): `org > team > project > workspace > run`. A resource is described by a chain that starts at the org and descends (levels may be skipped, never reordered).
- Permissions: `read`, `comment`, `steer`, `approve`, `administer`, `export`, `billing`. Billing is separate: only `owner` and `billing_admin` hold it, `admin` does not.
- Roles bind to a node and apply to that node and everything below it. A user's roles come from `org_members` (org), `team_members` (team nodes) and `policy_assignments` (any lower node).
- Policy documents attach to a node (`policy_documents`): rules with `effect` allow/deny, `permissions`, `subjects` (`*`, `user:<id>`, `role:<name>`, `kind:human|agent`) and, for deny, `overridable`. The org document also carries `allow_lower_grants`, `stepup_actions`, `stepup_max_age_seconds`, `emergency_max_minutes`.
- Custom roles are org-scoped named sets of the seven permissions (`policy_custom_roles`), cannot shadow a built-in role, and cannot contain a permission the creator does not hold.

## Precedence rule

Evaluation is default deny. Collect every matching rule down the chain: role grants (membership), active emergency grants, and policy rules. A deny rule that is not `overridable`, at any level, wins over everything. An `overridable` deny stands unless an eligible allow exists at a strictly deeper level than the deepest such deny (same level or shallower does not override). Allows come from role bindings, emergency grants and policy allow rules, but a policy allow below the org level is only eligible when the org policy sets `allow_lower_grants: true`; otherwise lower levels can only narrow. If no deny stands and at least one eligible allow exists the answer is allow. Rules are processed in `(level, rule id)` order and the decision never depends on input order.

| Situation | Result |
|---|---|
| No matching rule | deny (default) |
| Role default grants the permission, no deny | allow |
| Non-overridable deny at any level | deny, whatever allows exist |
| Overridable deny at org, role allow at project | allow (deeper level overrides) |
| Overridable deny and allow at the same level | deny |
| Policy allow at team/project/... without `allow_lower_grants` | ignored (reported as `ignored`) |
| Policy allow at org level | allow |
| Emergency grant inside its window | allow, but never beats a non-overridable deny |
| Emergency grant after `expires_at` | no effect (checked against `now`, no sweeper needed) |

`evaluate()` returns `Decision(allowed, permission, reason, chain)`; `chain` lists every matching rule with `status` `decisive` / `superseded` / `ignored`, so each answer is explainable.

## Role defaults

| Role | Permissions |
|---|---|
| owner | all seven |
| admin | all except billing |
| billing_admin | read, billing |
| engineer | read, comment, steer, approve |
| member | read, comment, steer |
| viewer | read, comment |
| guest | read |

## Step-up, emergency access, escalation

- Step-up actions (default `policy.save`, `role.change`, `export`, `emergency.grant`; replaceable in the org document) need a fresh `policy_stepup.stepup_at` for the calling session (default max age 300 s). `POST /api/v1/stepup/verify {code}` runs a pluggable verifier: default is the existing email OTP (`auth.verify_otp`, code requested through `/auth/request-login`); override with `app.state.stepup_verifier` (tests use a double). 403 `STEPUP_REQUIRED` otherwise.
- Emergency: requester files (reason of at least 10 characters, duration at most `emergency_max_minutes`, default 240), a different org administer holder who also holds the permissions approves (step-up required), grant auto-expires, can be revoked. Audit: `emergency.granted/used/revoked/expired`.
- Anti-escalation: `missing_for_grant()` is used for custom roles, role assignments and emergency approval; `evaluate_delegated()` makes an agent action require both the agent and its delegating human to hold the permission.

## Endpoints (`/api/v1`)

`POST /stepup/verify`; under `/policy/orgs/{org}`: `GET documents`, `PUT documents/{level}/{scope}`, `GET roles`, `PUT roles/{name}`, `POST assignments`, `POST check`, `POST preview`, `GET decisions`, `POST|GET emergency`, `POST emergency/{id}/approve|revoke`. Non-members of an org always get 404.

## Decision log

`policy_decision_log` (decision, subject, permission, resource, rule ids, ts). Every `authorize()` call writes a row (allow and deny). Admin query is bounded to 200 rows and a 31-day window. Retention: keep 90 days, purge by scheduled job (see the migration header); it is an operational log, the audit chain stays the record of consequence.

## Enforcement (part 38.005)

One gate for both services: `contracts/src/harness_contracts/policy_enforce.py`. The pure engine moved to
`contracts/.../policy_engine.py` (identity re-exports it from `policy.py`), so identity and orchestrator evaluate the same rules.

- **Classification table.** `IDENTITY_RULES` and `ORCHESTRATOR_RULES` map (method, concrete path) to a permission or `EXEMPT`.
  GET = `read`; run/plan control and engineer-level writes = `steer`; plan/change decisions = `approve`; admin-only routes =
  `administer`; audit export = `export`; plan changes = `billing`. Tests fail when any OpenAPI route is unclassified
  (`test_policy_gate.py`, `test_policy_gate_orchestrator.py`).
- **Exempt:** `/health`, `/ready`, `/metrics`, `/auth/*`, `csp-report`, `invites/redeem`, `stepup/verify`, `/orgs/*` (own org-scoped
  checks in `orgs.py`) and `/policy/*` (gated by `policy._gate`).
- **Middleware** (`install_policy_gate`, installed in `make_app` and in `deploy/runners/services.app_orchestrator`). For a caller
  whose team has `teams.org_id`, it loads the org's policy with raw SQL, evaluates the chain org > team > (project | workspace | run
  from the path) and answers 403 `POLICY_DENIED` on deny. Every deny and every non-read allow lands in `policy_decision_log`;
  every deny also writes a `policy.denied` audit row. Read allows are not logged (volume).
- **Composition.** The gate is additive to each route's legacy role check: a request needs both. So policy can narrow what a
  role allows (deny rules, custom-role limits, emergency expiry) but a policy allow cannot widen a route past its legacy check.
  A user of an org team who is not an org member is evaluated with their team role only; org deny rules still bind them.
- **Fail-closed.** An unclassified `/api/` route for an org team answers 403 `POLICY_UNCLASSIFIED`; any fault other than a missing
  policy schema answers 503 `POLICY_UNAVAILABLE`. A bare schema without the org/policy tables enforces nothing.
- **Teams without an org are untouched**, so the pilot behaves as before until a team is linked.
- **Step-up** is wired to team role change (`role.change`), audit export (`export`) and the policy API's own mutations.
- Legacy invites are admin-only and the admin role holds every permission, so they cannot escalate; `missing_for_grant` guards
  org-level grants (assignments, custom roles, emergency approval).

### Known limits
- The collaboration WebSocket is not passed through the gate (it is bound by single-use, fingerprint-bound tickets).
- `/orgs/*` routes use org roles, not the engine's deny documents or custom roles.
- Project and workspace ids come from the URL path; the gate does not verify that the project belongs to the team (the routes'
  own ownership checks do).
- Non-HTTP actors (queue worker, engine) are not evaluated; they act on runs already authorised at request time.
- Cost: an org-linked request adds about six small policy reads; no caching yet.
