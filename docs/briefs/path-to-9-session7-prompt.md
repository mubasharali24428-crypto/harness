# Session 7 — Integrations, administration and billing (parts 49, 51, 57)

Prefix: `docs/briefs/stable-prompt-prefix.md`. Directives: `docs/master-prompts/harness-multiplayer-master-build-prompt.md` sections 49, 51, 57
(read only these sections, plus `docs/briefs/PROJECT-STATE.md` section 6 “Facts that bite”). Target: each part at 9/10 where possible — every directive implemented or explicitly out of scope with a reason, invariant tests, evidence, matching docs, and an honest matrix row. Part 57 is externally capped at 8; do not represent provider evidence as verified without live Stripe/OIDC evidence.

## Current repository evidence to verify

- Session 3 already added organization membership, roles, policy documents, assignments, simulation (`POST /policy/orgs/{org_id}/preview`), emergency grants, and audit decision history. Inspect `section1/src/harness_identity/orgs.py`, `policy.py`, `teams_api.py`, `settings_api.py`, migrations 0038–0040 and the corresponding Section 7 settings pages.
- Integration code exists for GitHub (`section2/`), OIDC SSO (`section1/src/harness_identity/sso.py`), and Stripe checkout/webhooks (`stripe_adapter.py`, migrations 0031–0032). Confirm scope, signature/replay behavior, retries, credential handling, health/reconnect/revoke/rotation flows, and tests from source. Do not infer live provider verification from mocks.
- Billing state, plan changes and quota resolution exist in `section1/src/harness_identity/billing.py`, `contracts/src/harness_contracts/budget.py`, and `contracts/src/harness_contracts/tiers.py`. Inspect the run-start budget enforcement, per-team overrides, race behavior, UI and audit trail before deciding what remains.
- Matrix rows 49 and 51 are partial; row 57 is blocked and capped at 8. Session 6 is committed locally as `25fde58`; the workspace also contains unrelated dirty changes. Preserve all unrelated work and stage explicit owned paths only.

## Session requirements

1. Start with `git status`; inspect the three directive sections and the referenced implementation, tests, docs and UI. Produce a numbered plan with an exact verify step for each item.
2. Map every directive in 49, 51 and 57 to existing code and evidence. Identify externally gated evidence separately. Add or update one evidence report per part under `docs/briefs/evidence/s7/` and describe built behavior and limits in relevant docs.
3. For part 49, prioritize the versioned integration surface, least-privilege provider scopes, explicit team setup for chat/notifications, signed outbound webhook delivery with replay protection and bounded retries, idempotency for external side effects, extension boundaries, integration health/reconnect/revoke/rotation, compatibility policy, and consistent human approval. Reuse Stripe's verified inbound webhook work where applicable; do not claim outbound webhook support from inbound signature validation.
4. For part 51, verify membership/role/policy controls, approval thresholds, policy simulation, audit filtering/export, policy-change approval, emergency execution disablement, retention/deletion configuration, before/after audit detail, and the admin/leader boundary. Extend the admin UX where server behavior already supports it; keep APIs deny-by-default and team/org scoped.
5. For part 57, maintain one budget truth and clearly show attribution, estimates, alerts, hard limits, exhaustion behavior, reconciliation, billing permissions and the pilot boundary. Preserve run/audit state on quota exhaustion. Do not run paid/live provider calls. Explicitly document the live Stripe and OIDC evidence cap at 8.
6. Add invariant tests that fail on wrong-team access, wrong role, signature/replay errors, duplicate retries, policy threshold bypass, quota races/exhaustion, and audit content leakage as applicable. Use existing section test conventions and fixture migrations. Do not add tests merely for coverage count.
7. Run targeted tests and Ruff for touched Sections 1/2/4/7 and contracts; run the relevant full suites if environment allows. Report each exact command/result and blockers. Do not claim full suites pass when socket, Docker, or Seatbelt limits prevent them.
8. Update matrix rows 49, 51, 57 honestly, write `session7-summary.md`, update `PROJECT-STATE.md` and the checkpoint. Keep part 57 capped at 8 and do not claim the prior Session 6 part 44 gap closed.
9. Commit Session 7 by task using explicit file path staging only, local only; never push. Leave all unrelated dirty files untouched.

## Directive text

## 49. INTEGRATIONS AND EXTENSIBILITY
001. Define a versioned integration API with scoped OAuth or equivalent credentials.
002. Support repository providers and issue trackers through least-privilege scopes.
003. Support chat and notification integrations only with explicit team setup.
004. Provide webhooks with signatures, replay protection, and delivery retries.
005. Define idempotency keys for external side-effecting operations.
006. Offer a documented extension interface with sandbox and permission boundaries.
007. Review integrations for data exfiltration and privilege-escalation risks.
008. Provide integration health, reconnect, revoke, and credential rotation flows.
009. Maintain compatibility and deprecation policies for APIs and extensions.
010. Keep user approval requirements consistent across internal and external tools.

## 51. ADMINISTRATION AND GOVERNANCE
001. Provide organization administration for memberships, roles, policies, and integrations.
002. Provide workspace policy controls for agent tools and high-impact actions.
003. Allow administrators to set approval thresholds and eligible approver roles.
004. Support policy simulation before enforcing a new configuration.
005. Provide audit views with filters, actor details, and export controls.
006. Support policy change approval for regulated or high-assurance environments.
007. Allow emergency disablement of agent execution at organization or workspace level.
008. Provide data retention, residency, and deletion configuration where supported.
009. Record administrative actions with before-and-after policy values.
010. Document boundaries between organization admin powers and session leadership.

## 57. BILLING, QUOTAS, AND COST CONTROLS
001. Decide whether the product is paid, internal, or usage-metered before implementing billing.
002. Represent usage by organization, workspace, run, and agent where appropriate.
003. Set configurable limits for concurrent runs and provider consumption.
004. Provide clear budget estimates before expensive or long-running execution.
005. Allow administrators to set alerts and hard limits for spend.
006. Handle quota exhaustion without losing run state or audit history.
007. Make usage attribution understandable to organization administrators.
008. Protect billing data and separate billing permissions from run permissions.
009. Define refunds, failed provider calls, and usage reconciliation behavior.
010. Keep billing scope out of the pilot unless user evidence requires it.

## Report back

Give the local commit(s), matrix changes, tests and checks with exact counts, unrun checks, externally gated evidence, and remaining gaps. Then prepare Session 8 (52, 58, 31 enforcement) as a separate prompt; do not begin Session 8 implementation in this task.
