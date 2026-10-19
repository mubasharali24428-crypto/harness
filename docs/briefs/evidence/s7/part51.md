# Part 51 — administration and governance

## Directive map

| # | Result | Evidence / remaining limit |
|---|---|---|
| 001 | Partial | Organization memberships, roles, policy, emergency access and team integrations have scoped APIs/UI. Integrations are team-scoped; no organization-wide integration console. |
| 002 | Implemented locally | Policy documents and enforcement cover agent/tool permissions through the shared policy gate; default deny and route classification tests apply. |
| 003 | Partial | Policies carry approval-related rules and eligible roles; no separate high-risk approval threshold workflow for changing policy itself. |
| 004 | Implemented locally | `POST /policy/orgs/{org_id}/preview` invokes the same decision function as enforcement; governance UI provides draft preview before save. |
| 005 | Partial | Policy decision history and organization audit history are available; general audit UI filter/export is not unified on this page. Existing export APIs remain separately permission gated. |
| 006 | Out of scope for pilot | Policy save is immediate after authorization/step-up; no regulated dual approval workflow exists. UI labels this limitation. |
| 007 | Implemented locally | Owner/admin can stop or re-enable organization execution with step-up verification, a required reason, and before/after audit. New starts and queue claims are blocked; active workers stop when they observe the setting. |
| 008 | Out of scope for pilot | No organization retention/residency/deletion configuration surface; existing retention behavior is fixed or separately controlled. |
| 009 | Implemented locally | Organization and policy audit records include actor and before/after values for supported changes. |
| 010 | Partial | Org admin powers are documented separately from team/session control; session leadership remains a run-level collaboration lease. |

## Verification

- Existing Section 1 policy, org and enforcement suites are the authoritative backend checks.
- `section7/app/settings/governance/` adds membership/role, policy preview/save, emergency access,
  and history controls; `section7/app/settings/integrations/` is a team-admin surface.
- The settings navigation now links to governance and integrations. These pages are typechecked;
  no real-browser accessibility or end-to-end admin flow was run in this session.
- Policy save approval workflow and retention settings remain explicit gaps. Part 51 remains
  partial. Active worker termination is cooperative and occurs when the worker observes the stop.

See `docs/organizations.md` and `docs/permissions.md` for underlying authorization and audit
semantics.
