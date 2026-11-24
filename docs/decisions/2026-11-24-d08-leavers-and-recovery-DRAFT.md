# D-08 — leavers and recovery — DRAFT (unsigned)

- **Status:** DRAFT — engineering recommendation, **not an owner decision**.
- **Date:** 2026-11-24 · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#d-08`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`

## Context
The register requires prompt revocation, retained audit records and an authorized admin reassignment.

## Options
1. **Keep the default in force (recommended):** revoke promptly; keep audit; an authorized admin reassigns.
2. Delayed revocation with a grace window — less disruption, but leaves an access window after departure.
3. Automatic reassignment to any admin — faster, but removes the "authorized admin" check.

## Consequences of the default
Unchanged from the register default; no code path is enabled by this draft.

## Still missing
Owner signature.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- ACCEPTED record to be created at: `docs/decisions/______________________`
