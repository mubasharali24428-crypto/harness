# Decision: D-U4 — risk tiers and auto-approval

**Date:** 2026-11-13 · **Status:** ACCEPTED by the decision owner (Mubashar Ali, in session), 2026-11-13.
**Resolves:** UI overhaul plan §6 D-U4; FUTURE PLAN 2.0 §5A Monday decision.
**Related:** P-21 / I-05 risk-tiered approval, which needs P-D6.

## Context

The UI plan wants risk tiers so routine changes can be batched and risky ones gated. The repository's
standing rule is no auto-approval anywhere, and the risk-tier backend decision (P-D6) is not taken.

## Options

1. **Risk tiers stay off; every approval remains explicit.** Recommended until P-D6 is decided.
   The review UI still shows recorded risk flags and the verdict card, but no approval is inferred
   from them.
2. **Ship visual tiers now with auto-approval for low risk.** Not recommended: it creates an
   approval authority the backend has not defined and contradicts the no-auto-merge/no-auto-approval
   boundary.

## Recommendation

**Option 1.** Show risk information, never act on it automatically. If tiers are displayed later,
they must be labelled as advisory until P-D6 defines the policy and the audit trail.

## Consequences if accepted

- No risk-tier code in U3 this week; the verdict card and risk flags remain read-only facts.
- A risky path outside scope still requires explicit human approval even when every automated check
  passes (the U3 negative test).
- P-21 remains the place where tiers, if any, get their backend contract and decision record.

**Owner decision:** ACCEPTED — Option 1 (risk tiers off; every approval explicit).
**Date:** 2026-11-13 · **Rationale:** as recommended; no divergence.
