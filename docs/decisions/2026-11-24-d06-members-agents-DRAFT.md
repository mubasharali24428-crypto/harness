# D-06 — members' agents — DRAFT (unsigned)

- **Status:** DRAFT — engineering recommendation, **not an owner decision**.
- **Date:** 2026-11-24 · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#d-06`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`

## Context
The register grants members' agents no access wider than explicit grants and keeps existing approval and merge rules.

## Options
1. **Keep the default in force (recommended):** no access wider than explicit grants; existing approval and merge rules apply.
2. Give agents the member's own permissions automatically — convenient, but removes the explicit grant boundary.
3. Read-only agents by default with opt-in write — narrower, needs a new grant surface.

## Consequences of the default
Task 10 external adapters join read-only and cannot widen scope.

## Still missing
Owner signature.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- ACCEPTED record to be created at: `docs/decisions/______________________`
