# Part 51 — administration and governance

## Directive map

| # | Result | Evidence / remaining limit |
|---|---|---|
| 001 | Partial | Organization memberships, roles, policy, emergency access and team integrations have scoped APIs/UI. Integrations are team-scoped; no organization-wide integration console. |
| 002 | Implemented locally | Policy documents and enforcement cover agent/tool permissions through the shared policy gate; default deny and route classification tests apply. |
| 003 | Implemented locally when enabled | Organization owners can require a second admin for high-risk policy changes (deny rules, sensitive permissions, or lower-scope grants). Requester self-approval is rejected; approval is step-up protected and version checked. |
| 004 | Implemented locally | `POST /policy/orgs/{org_id}/preview` invokes the same decision function as enforcement; governance UI provides draft preview before save. |
| 005 | Partial | Policy decision history and organization audit history are available; general audit UI filter/export is not unified on this page. Existing export APIs remain separately permission gated. |
| 006 | Implemented locally when enabled | High-risk policy edits are staged for a different admin's step-up protected approval or rejection. Dual approval is owner-configurable. |
| 007 | Implemented locally | Owner/admin can stop or re-enable organization execution with step-up verification, a required reason, and before/after audit. New starts and queue claims are blocked; active workers stop when they observe the setting. |
| 008 | Partial | Owners can set shorter organization event and audit retention windows; the retention worker applies them. Tenant-specific physical residency is unsupported in this single-region deployment. Audit pruning also follows the deployment's global horizon, so org values currently shorten but cannot extend it. |
| 009 | Implemented locally | Organization and policy audit records include actor and before/after values for supported changes. |
| 010 | Partial | Org admin powers are documented separately from team/session control; session leadership remains a run-level collaboration lease. |

## Verification

- Existing Section 1 policy, org and enforcement suites are the authoritative backend checks.
- `section7/app/settings/governance/` adds membership/role, policy preview/save, emergency access,
  history, dual-approval and retention controls; `section7/app/settings/integrations/` is a team-admin surface.
- The settings navigation now links to governance and integrations. These pages are typechecked;
  no real-browser accessibility or end-to-end admin flow was run in this session.
- Dual approval is owner-configurable and applies to high-risk policy changes. Retention settings
  apply to event and audit data; physical residency remains deployment-wide. Part 51 remains partial.
  Active worker termination is cooperative and occurs when the worker observes the stop.

See `docs/organizations.md` and `docs/permissions.md` for underlying authorization and audit
semantics.
