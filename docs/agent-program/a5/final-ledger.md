# A5 final ledger — 30 features / 10 risks

Program: A5 — MCP, integrations and operations · baseline
`31bf4077e9b567b1f50ecb99818d501baab3115a` · machine-readable twin:
`final-ledger.json` (generated from the five session ledgers).

**Result: 30/30 features dispositioned — 26 VERIFIED IMPLEMENTED, 4 PARTIAL.
10/10 risks dispositioned — 4 PARTIAL CONTROL, 6 EXTERNAL VALIDATION
REQUIRED.** PARTIAL is used where the local slice works but a documented
piece is missing (deliberately not upgraded to "verified" — see per-feature
`limits` in the session ledgers).

## Features

| Session | Feature | Capability | State |
| --- | --- | --- | --- |
| S1 | F061 | First-class MCP server registry | VERIFIED IMPLEMENTED |
| S1 | F062 | MCP permission scopes | VERIFIED IMPLEMENTED |
| S1 | F063 | MCP server health checks | VERIFIED IMPLEMENTED |
| S1 | F064 | Custom tool builder | VERIFIED IMPLEMENTED |
| S1 | F065 | Reusable skills or playbooks | VERIFIED IMPLEMENTED |
| S1 | F066 | Plugin marketplace | **PARTIAL** (curated bundled catalog; no third-party publishing/payments) |
| S2 | F067 | Namespaced slash commands | VERIFIED IMPLEMENTED |
| S2 | F068 | Event hooks | VERIFIED IMPLEMENTED |
| S2 | F069 | Tool allowlists/denylists | VERIFIED IMPLEMENTED |
| S2 | F070 | Extension SDK | VERIFIED IMPLEMENTED |
| S2 | F125 | Notion/internal-wiki context | **PARTIAL** (adapter point documented; live Notion OAuth unverifiable here) |
| S2 | F150 | API docs & SDK distribution | **PARTIAL** (no package-index publication; wheel-install proof only; no TS SDK) |
| S3 | F121 | Slack intake/status | VERIFIED IMPLEMENTED |
| S3 | F122 | Teams intake/status | VERIFIED IMPLEMENTED |
| S3 | F123 | Linear integration | VERIFIED IMPLEMENTED |
| S3 | F124 | Jira integration | VERIFIED IMPLEMENTED |
| S3 | F126 | Sentry error→agent | VERIFIED IMPLEMENTED |
| S3 | F127 | Datadog/Grafana incident→agent | **PARTIAL** (Grafana complete; Datadog via custom payload template — default payload unparsed) |
| S4 | F112 | OpenTelemetry export | VERIFIED IMPLEMENTED |
| S4 | F113 | Agent quality dashboard | VERIFIED IMPLEMENTED |
| S4 | F114 | Developer productivity dashboard | VERIFIED IMPLEMENTED |
| S4 | F115 | Cost per task / accepted change | VERIFIED IMPLEMENTED |
| S4 | F116 | Time-to-first-useful-change | VERIFIED IMPLEMENTED |
| S4 | F117 | Searchable run archive | VERIFIED IMPLEMENTED |
| S5 | F118 | Exportable audit evidence | VERIFIED IMPLEMENTED |
| S5 | F119 | Stall/risk/cost/policy alerts | VERIFIED IMPLEMENTED |
| S5 | F120 | Usage APIs + webhooks | VERIFIED IMPLEMENTED |
| S5 | F128 | CI provider integrations | VERIFIED IMPLEMENTED |
| S5 | F129 | Chat approve/reject/inspect | VERIFIED IMPLEMENTED |
| S5 | F130 | Scheduled maintenance tasks | VERIFIED IMPLEMENTED |

## Risks

| Risk | State | One-line position |
| --- | --- | --- |
| VR014 | PARTIAL CONTROL | Dependency scanning/collector hardening → Section 10 assembly |
| VR020 | PARTIAL CONTROL | No attestation/pen-test; pilot-grade posture; runbook + evidence exports added |
| VR031 | PARTIAL CONTROL | Instruments deployable; no pilot → no measured ROI, none claimed |
| VR040 | PARTIAL CONTROL | Runbook now ships; end-user onboarding still minimal |
| VR036 | EXTERNAL VALIDATION REQUIRED | Market risk — not code-closable |
| VR037 | EXTERNAL VALIDATION REQUIRED | Buyer validation not performed |
| VR038 | EXTERNAL VALIDATION REQUIRED | Procurement evidence not gathered |
| VR042 | EXTERNAL VALIDATION REQUIRED | Pricing not validated |
| VR044 | EXTERNAL VALIDATION REQUIRED | Willingness-to-pay not measured |
| VR050 | EXTERNAL VALIDATION REQUIRED | Distribution/market risk — not code-closable |

## Exact release / integration limits

1. Nothing was deployed or auto-merged; no outreach was sent and no customer
   acceptance is implied anywhere in these artifacts.
2. Combined-lane assembly (orchestrator + ops + auth + telemetry archive in
   one environment) has never run — per-lane evidence only. Evaluator:
   `scripts/agent-program/a5/integration_check.py` (inspection-only).
3. ROI/productivity claims are blocked until a real pilot runs the
   pre-registered instruments (`session-4/pilot-measurement.md`); honest
   sample sizes required.
4. All provider integrations were exercised against local HTTP transports,
   never real Slack/CI/customer endpoints.
5. Pilot-grade ops: SQLite single-node, no queue/HA; backup + restore drill
   and IdP/billing/model-drill checklists in `session-5/release-blockers.md`
   must pass before rollout.

Per-session evidence: `session-{1..5}/{report.md, ledger.json,
verification.log}` + goldens under each session's `interop/golden/`.
