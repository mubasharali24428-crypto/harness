# Decision draft: D-01 — deployment & commercial priority

**Date:** 2026-11-11 · **Status:** PROPOSED — owner decides.
**Resolves:** `FUTURE PLAN.md` D-01 ("Self-hosted first, managed cloud first, or both?")
and plan task P-D1. Until signed, the FUTURE PLAN default holds: preserve existing
architecture and pilot deployment; no new cloud-only dependency.

## Recommendation: SELF-HOST FIRST, cloud later (both preserved)

### For

1. **It is the differentiation.** The verified demo (2026-11-11) runs the full
   governance stack on the operator's own infrastructure with BYO model keys.
   "We have no channel to your code even if we wanted one" (PRICING.md §2) is a
   sales sentence cloud-only competitors cannot say.
2. **Buyer fit.** The beachhead wedges — Pakistani export software houses proving
   AI-change safety to Western clients, and regulated enterprises — both require
   data to stay on their infrastructure. Self-host is the requirement, not a feature.
3. **Solo-founder capacity.** Managed cloud adds on-call, SLOs, multi-tenant
   isolation at scale, and a security surface the FUTURE PLAN already gates behind
   Milestone E. One person + fleet cannot staff both motions now.
4. **Revenue mechanics work self-hosted.** Open-core (MIT) + paid Team tier
   (SSO/SCIM/SIEM gates already enforced in code) + support/SOW pilots. No cloud
   dependency needed to collect the first dollar (payment rail is P-D2, separate).
5. **Evidence already exists.** The w24b1 demo set boots self-hosted end-to-end;
   the current-source RC build (A-06, in progress) targets the same posture.

### Against (honest)

- Cloud onboarding is faster (no Docker install); self-host friction kills some pilots.
- Some investors pattern-match "self-hosted = services business"; the counter is
  open-core + enforced tier gates + metering, which is a product motion.
- The older `EXPANSION_PLAN.md` said cloud-first; this draft supersedes it **only
  when the owner signs** (FUTURE PLAN §2 warns against silently treating old plans
  as current policy — same rule applies here).

### What "both preserved" means concretely

- No cloud-only dependency added to the core (FUTURE PLAN default).
- The compose stack stays the single deployment artifact; a future managed offering
  runs the same images per-tenant (the engine container path is already per-run
  isolated — P-04 review).
- Revisit at the Day-90 gate with pilot evidence: if ≥2 partners ask for managed
  hosting and would pay for it, scope a cloud pilot as a separate decision.

**Owner decision:** ______ · **Date:** ______ · **Rationale (if different):** ______