# A1 — Security, Identity, Models and Commercial Governance: close-out (2026-10-17)

Worktree `/Users/user/untitled folder 2/harness-a1` (branch `codex/harness-a1`, base 31bf407). Nothing committed, pushed or deployed.
Machine-readable ledger: [ledger.json](ledger.json) (30 F rows, 10 VR rows). Per-session reports: `session-1` … `session-5`.

## Final gate (re-run at close)
| Suite | Result |
|---|---|
| packages/security-policy | 95 passed, ruff clean |
| section1 | 688 passed / 10 skipped (baseline 639/10), ruff clean |
| section3 | 106 passed (baseline 95), ruff clean |
| section4 (read-only, section1 interpreter) | 525 passed / 8 skipped; untouched baseline checkout: 525 passed / 7 skipped / 1 env failure — no regression |
| Postgres | migrations 0001-0038 apply on a throwaway local DB (dropped) |
Not run: section11, MCP, frontend build/e2e, live browser, any live provider/IdP/Stripe, concurrency on real Postgres.

## Audit corrections to the earlier A1 work
S1 report claimed ruff clean and a `.venv` (neither true); a truncated PEM private key was ALLOWED by the secret scanner (fixed); the HAP
module had no tests and an accidental sentinel (fixed); the promised `/api/v1/policy` adapter did not exist (built).
Defects found elsewhere in A1's code: session of a removed member gave HTTP 500 and inactive users were never rejected (fixed in
`auth.get_principal`); `PUT /billing/plan` was a free self-upgrade under `HARNESS_BILLING=stripe` (fixed); `Team.budget_usd` has no ORM mirror (raw SQL).
Known, not fixed: legacy router silently remaps an unknown requested model when no policy provider is wired; member removal deletes a row that
`audit_log` RESTRICTs; OTP/SSO can still mint sessions for deactivated members (every request then 401s).

## Release / integration limits
- Everything new is opt-in or additive. Section 3 policy enforcement is OFF until deploy calls `set_policy_provider(pool_policy_provider(pool))`;
  section3 now needs `packages/security-policy` installed (A5: images/CI); section1 degrades to "endpoints absent" without it.
- NEEDS INTEGRATION (non-owned): S4/S5/SDK callers of `/api/v1/policy/evaluate`, `resolve_shared_key`, `/package-trust/verify`; A4 UI for keys,
  consent, allowlist, trial, console; A5 cron for `expire_due_trials` and alert delivery; A2 engine key race (global finding) is NOT fixed by A1.
- NEEDS EXTERNAL VALIDATION: live Stripe checkout/usage drill, real IdP (OIDC+SCIM), provider-terms and residency legal review, pilot economics.
- Keep disabled until combined acceptance: policy provider wiring in production, fallback (default off), cache (default off), SCIM tokens (none minted).
