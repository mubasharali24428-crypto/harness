# D-02 — project admin model — DRAFT (unsigned)

- **Status:** DRAFT — engineering recommendation, **not an owner decision**.
- **Date:** 2026-11-24 · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#d-02`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`

## Context
The register leaves the number of project admins and ownership transfer to the owner. The code today treats project admins as fully visible but keeps lifecycle actions gated.

## Options
1. **Keep the default in force (recommended):** project admins fully visible; invite/remove/transfer needs owner approval.
2. Allow multiple admins with self-service invite/remove — faster, but removes the owner control point.
3. Single admin with delegated operators — simpler authority, but blocks parallel administration.

## Consequences of the default
Phase 6 admin lifecycle stays behind P-D7; nothing here enables it.

## Still missing
Owner choice on multiple admins + ownership transfer.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- ACCEPTED record to be created at: `docs/decisions/______________________`
