# X-D1 — approval rendering owner — DRAFT (unsigned)

- **Status:** DRAFT — engineering recommendation, **not an owner decision**. The default is already implemented in code.
- **Date:** 2026-11-24 · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#x-d1`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`

## Context
The register assigns rendering ownership: a member selects their own view, an admin may set it for a member, and rendering never changes disclosed facts or the policy risk tier. Implemented at `aed8a339`; Sprint 3 Task 13 added the self-service settings UI.

## Options
1. **Keep the default in force (recommended):** member self-selects; admin may set it for a member; no change to disclosed facts or risk tier.
2. Admin-only rendering — consistent view, but removes self-service (X-D1 says otherwise).
3. Let rendering narrow disclosure — rejected: presentation must not change policy.

## Consequences of the default
`GET/PUT /api/v1/me/rendering` is display-only; every change is audited; no gate or permission changes.

## Still missing
Owner signature.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- ACCEPTED record to be created at: `docs/decisions/______________________`
