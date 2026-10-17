# Pilot Remediation — Session Plan

Source of truth: `RISK_REGISTER.md` (50 items, verified evidence).
Rule: a risk closes only with a passing test / runtime check / reproducible
operational procedure — never because a function exists.

## Session 1 — five pilot-blocking risk areas (R-01 … R-05)
Primary model: DeepSeek V4.1 Flash.
Scope: (1) authn/authz/tenant isolation · (2) secrets · (3) sandbox/network
boundaries · (4) prompt injection · (5) audit/cancellation/recovery.

Per-risk completion gates (all required before the next risk starts):
- architecture/data-flow map written down (file:line anchors)
- smallest safe implementation, diff reviewed line-by-line
- focused tests added incl. negative tests (bypass, denial, malformed, cross-tenant,
  interrupted execution)
- full section test suite for every touched section is green
- threat model + migration/rollback note written in `session1/`
- human-readable verification result (commands + output summary)

Exit criteria for Session 1: R-01..R-05 each have a `session1/S1-Rxx-*.md` note
with the evidence above, and no new regressions in touched-section suites.

**Status: COMPLETE — exit criteria met.** All five notes carry §Verification
results; register rows R-01..R-05 are MITIGATED & VERIFIED. Final suite
state: S1 633/10 skip, S4 480/5 skip, plus session-wide greens unchanged by
R-05 (S2 36, S3 95, S5 176/1 skip, S6 116, S8 88, S10 69, S11 50, mcp 18);
ruff clean on every touched section. Nothing committed (program rule: human
reviews the diff). Session 2 starts with a re-verification pass (register
states decay).

## Session 2 — 25 risks: hardening, durability, operability (R-06 … R-30)
Primary model: TBD at session kickoff (program governance).
Covers: deployment exposure (ports/TLS), master-key config, outbox crash window,
run-state truth, reaper narrative, cost/provider failure paths, metrics/docs/
retention exposure, SSRF lead verification, token hygiene, single-replica
assumptions, disk retention, migration rollback, dev literals, error passthrough,
event duplicates, GitHub offboarding, SDK supply chain, audit read surface.
Gate: each item re-verified at session start (register states decay); fixes
follow the same per-risk gate as Session 1.

## Session 3 — 20 risks: privacy/compliance/support/usability/integrations/
model quality/competitive/commercial (R-31 … R-50)
Primary model: TBD at session kickoff.
Covers: legal skeletons (counsel), telemetry sign-off, retention table, support
process, E2E demo-path tests, UI truthfulness, Stripe/MCP pilot decisions,
model-quality measurement, docs-vs-code overstatement (rules not injected),
pricing/commercial readiness, SSO need-by, onboarding path, frontend in-flight
work review, provider-key operator docs, input hygiene long tail.
Gate: same; several items are decisions/ops procedures rather than code — a
decision must be recorded, not implied.

## Sequencing law (all sessions)
No parallel risky edits to the same surface. One risk in flight at a time.
Every diff stays small and reviewable; >100 lines triggers a check-in.
No commits unless asked; changes are left as reviewable working-tree patches.
