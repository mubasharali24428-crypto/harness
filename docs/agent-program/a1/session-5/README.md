# A1-S5 — Self-service governance and customer evidence

Owner A1. Features F022 F141 F142 F146 F148 F149. Risks VR017 VR019 VR041 VR047 VR048.
Migration `0038_trials_workspace_close.sql`.

## Built (section1 `/api/v1/identity`, package, billing)
- F022 (preserve): NOT rewritten and NOT editable by A1 (Section 4 is A2's). Read-only re-run against the
  worktree: `test_plan_gate.py`, `test_plan_gate_scoping.py`, `test_plan_concurrency.py` = 38 passed; the approval
  TTL (`approval_expired`, `state_machine.py`, `service.py`) is present. Full section4 (section1 interpreter):
  525 passed / 8 skipped here vs 525 passed / 7 skipped / 1 env failure (section11 driver) on the untouched baseline checkout =
  no regression. A1 adds approval evidence counts to F149 (`plan_approval_gate`).
- F141 `lifecycle_api.py`: create additional workspace (caller = admin, max 5 live), admin rename, admin close
  (`confirmName` must match; sessions deleted, team keys/SCIM tokens/shares revoked, `teams.closed_at` blocks all
  authentication; refused while a paid plan is active). Hard delete is intentionally NOT an API (audit FK is RESTRICT).
- F142 trial-to-paid: one trial per team ever (`team_trials`), starts the `pro` plan through the existing
  `billing_plans` row so every tier consumer honors it; lazy expiry (on `GET /trial`, `GET /billing/state`) and
  operator sweep `expire_due_trials()` revert to `free` only if the plan is still the trial plan; a paid provider event
  (`stripe_adapter.process_event`) marks the trial converted so expiry never downgrades a paying team.
  **Defect fixed:** `PUT /billing/plan` let any admin set any tier with no payment even when `HARNESS_BILLING=stripe`;
  it now answers 409 BILLING_MANAGED_BY_PROVIDER in that mode (local/manual mode unchanged).
- F146 `GET /org/overview` (admin only): roles/deactivated/pending invites, plan+trial, SSO/SCIM, key counts, model policy,
  residency, supply chain, retention/holds, spend ceiling level. "Organization" = the workspace; multi-team org hierarchy is NOT modeled.
- F148 data residency: team-declared `residency` block in the model policy (allowed regions + per-provider declared regions).
  Enforced inside `evaluate_model`, which Section 3 `/models/call` and `/models/compare`, opt-in fallback and the policy
  simulator all use: undeclared provider => `model.residency_unknown`, any out-of-region => `model.residency_denied`
  (fail closed; fallback equivalents are filtered by the same rule).
- F149 `GET /compliance/evidence` (+`/verify`): evidence/1 document of counts/flags/ids (no emails, key material, or
  content) with a SHA-256 audit-row chain digest and an HMAC-SHA256 integrity block under a key derived from the master key;
  `verify` is true only for an unmodified document issued by this deployment for the caller's team.

## Verification (2026-10-17)
- section1 688 passed / 10 skipped, ruff clean (baseline 639/10); section3 106 passed; package 95 passed; all 38 migrations
  apply on a throwaway local Postgres DB. 9 new tests in `tests/test_lifecycle_s5.py` plus 5 residency tests in the package:
  billing-bypass regression, workspace limit/close/paid-plan block/session death, trial once/expiry/sweep/conversion race,
  overview admin-only + tenant scoping, evidence redaction/verification/tamper/cross-team, residency deny + unknown + simulate.
- Not done: live Stripe checkout drill (F142), real browser journey of the signup/onboarding UI (A4), cron wiring of
  `expire_due_trials` (A5), legal review of the evidence wording and of provider region declarations.

## Dispositions
| F | Status |
|---|---|
| F022 | VERIFIED IMPLEMENTED (preserved; section4 regression suites green; A1 changed nothing there) |
| F141 | PARTIAL: workspace create/rename/close API proven locally; onboarding UI + email-based first signup flow not re-drilled; no hard delete by design |
| F142 | PARTIAL: trial + conversion + honest expiry locally proven; NEEDS EXTERNAL VALIDATION (live Stripe test-mode) and NEEDS INTEGRATION (scheduled sweep) |
| F146 | PARTIAL: admin console DATA API only; UI = A4; no multi-team org layer |
| F148 | PARTIAL: enforced for model/provider choices Section 3 makes; tools/storage/other lanes' routes not covered; regions are declarations, not verified provider facts |
| F149 | PARTIAL: signed redacted evidence export; no external-framework mapping, no scheduled export, not a certification |
Risks: VR047 PARTIAL CONTROL (residency + retention + evidence; legal/customer acceptance open). VR048 PARTIAL CONTROL (declared
provider regions + allowlist; provider-terms review is a human task). VR041 PARTIAL CONTROL. VR017 CONTROL VERIFIED for A1's endpoints
(tenant-scoped tests), no guarantee for other lanes. VR019 PARTIAL CONTROL.

## Human/external prerequisites (not fabricated)
Counsel review of PRIVACY/TERMS vs residency claims; provider data-processing terms per region; a live IdP and Stripe test-mode drill;
named authority to accept residual risks.

## Rollback
Remove `lifecycle_api` include; `DROP TABLE team_trials; ALTER TABLE teams DROP COLUMN closed_at;` (revert the auth `Team` join first).
