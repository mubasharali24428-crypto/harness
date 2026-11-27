# CD-02 — learned memory and skills — DRAFT (unsigned)

- **Status:** DRAFT — engineering recommendation, **not an owner decision**.
- **Date:** 2026-11-24 · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#cd-02`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`

## Context
The register keeps learned memory out of pilot scope and requires proposals only, never automatic application.

## Options
1. **Keep the default in force (recommended):** not in pilot scope; proposals only, never auto-applied.
2. Auto-apply high-confidence proposals — less human work, but removes the review gate the register mandates.
3. Inject proposals into prompts as hints — more context, but still a disclosure change needing owner approval.

## Consequences of the default
Tasks 6, 7 and 9 emit proposal/queue objects only; nothing injects into prompts or rules without human review.

## Still missing
Owner signature.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- ACCEPTED record to be created at: `docs/decisions/______________________`
