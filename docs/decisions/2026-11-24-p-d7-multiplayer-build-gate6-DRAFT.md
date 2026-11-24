# P-D7 — multiplayer build go/no-go (Gate 6) — DRAFT (unsigned)

- **Status:** DRAFT — engineering recommendation, **not an owner decision**. Design-only remains in force.
- **Date:** 2026-11-24 · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#p-d7`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`

## Context
Gate 6 requires B-09 evidence, a threat model, owner sign-off and an independent reviewer. The collab pilot (approval digests, leader approvals) exists behind `HARNESS_PILOT_TEAMS`; design work and negative tests are allowed, build authorization is not.

## Options
1. **Keep the default in force (recommended):** DEFERRED — design-complete allowed, build-authorized NOT, decided from pilot demand rather than momentum.
2. Authorize the full B-03..B-09 build now — faster, but spends the gate without demand evidence or reviewer sign-off.
3. Authorize a hardening-only phase — fixes and tests on the existing pilot, no new lifecycle surface.

## Consequences of the default
No new invasive multiplayer UI or lifecycle routes; hardening and negative tests on the existing pilot are allowed; the pilot stays single-member with admin review.

## Still missing
Pilot demand evidence (signed SOWs asking for multi-member), B-09 run, reviewer sign-off.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- Independent reviewer / date: _______________________________________
- ACCEPTED record to be created at: `docs/decisions/______________________`
