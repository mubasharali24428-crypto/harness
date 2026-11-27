# CD-01 — cache retention / tenancy — DRAFT (unsigned)

- **Status:** DRAFT — engineering recommendation, **not an owner decision**.
- **Date:** 2026-11-24 · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#cd-01`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`

## Context
The register forbids cross-tenant cache sharing and sets a month-scale retention cap with purge on erasure.

## Options
1. **Keep the default in force (recommended):** no cross-tenant sharing; month-scale cap; purge on erasure.
2. Longer retention for reused artifacts — fewer recomputes, but keeps data past the pilot window.
3. Shared cross-tenant cache — cheaper, but breaks the tenancy boundary the register requires.

## Consequences of the default
Unchanged from the register default; no cache behavior changes with this draft.

## Still missing
Owner signature.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- ACCEPTED record to be created at: `docs/decisions/______________________`
