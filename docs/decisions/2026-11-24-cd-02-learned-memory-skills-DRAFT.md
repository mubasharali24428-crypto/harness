# CD-02 — learned memory and skills: memory never grants capability — DRAFT (unsigned)

- **Status:** DRAFT, revision 2 (2026-11-25). Engineering recommendation, **not an owner decision**.
  Nothing in this record is signed, enabled or applied. The register default stays in force until
  the owner signs below.
- **Date:** 2026-11-24 (rev 2: 2026-11-25) · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#cd-02`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`
- **Plan rows:** FUTURE PLAN 2.0 §17 CD-02, 8.10, X-20; item S-10 (learned proposals).

## Context

The register says learned memory and skills are **not in pilot scope**: learned entries are
proposals only and are never auto-applied. Rev 2 adds the second half of the rule that the code
already follows but no signed record states: **memory is advice, never authority.**

What exists today (read from the repo, not from a measurement):

- `section4/.../memory_loader.py` renders reviewed, unexpired project notes into an advisory prompt
  block. Drafts never reach a prompt. The block says memory cannot override project rules, the
  approved scope or approvals. It never touches the sandbox allow-list or `forbidden_paths`.
- `section1/migrations/0066_team_skills.sql`: a team skill is reviewed Markdown pinned by digest.
  There is no column for a command, script, hook, env, tool or permission, and unknown manifest
  keys are refused.
- `section4/.../case_memory.py` (Task 9): every case is `status="proposed"`; recall is shown next
  to a denial stop; promotion stays a human PUT on the rules API.
- Nothing above can widen a scope, approve a step, add a tool or loosen a gate. Section 5 and the
  policy engine remain the only places a capability is granted.

The open question for the owner is whether to **sign that boundary as a rule**, so a later feature
(auto-promotion, S-10 learned proposals, curator consolidation X-13, a context graph X-12) cannot
quietly cross it.

## Options

1. **Keep the default and sign the boundary (recommended).** Learned memory and skills stay out of
   pilot scope. Proposals only, never auto-applied. Additionally: *memory never grants capability*.
   No memory entry, skill, case, similarity edge or recalled lesson may add a tool, widen a path or
   command scope, relax an approval or risk tier, raise a limit, or count as an approval. It may
   only inform a human or a prompt, and only after human review.
   - Consequence: Tasks 6, 7 and 9 stay as built. A future promotion feature needs a new owner
     record. Cost is human review time on every promoted rule.
2. **Keep out of scope, but leave the boundary unwritten.** Same behaviour today.
   - Consequence: the boundary lives only in code comments and tests. A later change can cross it
     without a decision record, which is the failure this record exists to prevent.
3. **Auto-apply high-confidence proposals.**
   - Consequence: less review work, but removes the review gate the register mandates and lets a
     scored guess change rules. A poisoned or wrong memory could then widen behaviour. Not
     recommended; needs a threat model and benchmark evidence first.
4. **Inject proposals into prompts as hints before review.**
   - Consequence: more context for the model, but a disclosure change: unreviewed text reaches the
     prompt and the cache prefix (S-02). Needs a separate disclosure decision.

## Recommended default

Option 1. It is the register default plus one sentence, so behaviour does not change.

## Consequences if signed

- A test-backed invariant is owed (not written by this record): a memory/skill/case object
  cannot appear in any policy, scope, allow-list or approval input. Until it exists, the claim is
  "by construction in the current code", not "tested".
- Promotion of any learned rule requires the benchmark-improves gate (8.10, X-20) and a named
  human reviewer other than the proposer (see CD-02 mention in `experiments/context-store/README.md`).
- S-02 stable prefix (PR 39) is unaffected: it freezes the *reviewed* memory snapshot at run start.

## Still missing

Owner signature. No pilot or benchmark evidence for learned-rule promotion exists, so Options 3 and 4 are
not evidence-backed.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- ACCEPTED record to be created at: `docs/decisions/______________________`
