# P-D7 — multiplayer build go/no-go (Gate 6) — DRAFT (unsigned)

- **Status:** DRAFT — engineering recommendation, **not an owner decision**. The register default
  (DEFERRED, design-only) remains in force until the owner signs below.
- **Revision:** 2 (2026-11-25). Rev 1 (2026-11-24) gave three one-line options; rev 2 adds the gap
  map, the slice plan, explicit conditions for each option and a revocation clause. No option has
  been chosen; nothing in this revision enables anything.
- **Date:** 2026-11-24 (rev 2: 2026-11-25) · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#p-d7`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`
- **Inputs:** gap map `docs/plans/phase6-multiplayer-gap-map-2026-11-25.md`; slice plan
  `docs/plans/phase6-multiplayer-slice-plan-2026-11-25.md`; B-09 pack
  `docs/briefs/evidence/multiplayer/B-09-checklist-2026-11-24.md`.

## Context

FUTURE PLAN 2.0 §11 puts Phase 6 build work (6.3–6.9) behind P-D7, and conflict C1 (§18) resolves
"multiplayer before real users" vs "decide at Day 90" by running design early and building only
after P-D7. Gate 6 itself requires B-09 evidence, a threat model of the multiplayer slice, owner
sign-off and an independent reviewer.

What exists: team seats, invites bound to the invited mailbox, soft removal with session
revocation (D2), roster `userId` (D3), a shared-run collaboration pilot behind
`HARNESS_PILOT_TEAMS`, and an admin-reviewed assignment plan whose `/mine` view is member-scoped.

What is missing (gap map §3): **visibility below the team.** Every seat on a team can read every
run, search hit, export and notification of that team. The §1 privacy invariant needs project
membership (G2), a per-assignment visibility rule enforced on every read path (G3, G6, G7), an
assignment lifecycle (G8), least-privilege agent context with canaries (G11), and the full B-09
journey at one revision (G12).

## Options

### Option 1 — Stay DEFERRED (register default)

- Design work continues (slice M0: permission matrix, schema design doc, xfail negatives that pin
  today's team-wide reads, owner-Mac invite→revoke re-run, threat model extension).
- No migration, route, UI or contract change for multiplayer. The pilot stays single-member with
  admin review.
- **Choose this if:** there is no signed design-partner demand for multi-member projects yet.

### Option 2 — Staged GO: enforcement first, behind a default-off flag

- Authorizes slices **M1–M3 only** (project membership, the visibility predicate on runs, then the
  same predicate on WS, search, exports, notifications, artifacts, discussion).
- Every new behaviour ships behind a flag that defaults **off**; the deployed pilot behaves exactly
  as today until the owner turns it on for a named team.
- A second sign-off (this record, "Stage 2" line below) is required before M4–M7 (lifecycle,
  canaries, workspaces, B-09 journey).
- **Choose this if:** the owner wants multiplayer built but wants the privacy enforcement proven by
  negative tests before any new screen or lifecycle exists.

### Option 3 — Full GO (M1–M7)

- Authorizes the whole slice plan in order, with Gate 6 review at the end.
- Fastest path to a multi-member pilot; spends the gate without demand evidence and commits about
  six to eight PRs plus three owner-Mac live runs.

### Option 4 — Hardening only (rev 1 option 3)

- Fixes and negative tests on the existing team-level pilot; no project membership, no new
  lifecycle surface. Overlaps heavily with Option 1's M0 and is listed for completeness.

## Engineering recommendation

**Option 1** while the standing owner rule "never enable P-D7" is in force and no signed SOW asks
for multi-member projects. **If the owner decides to lift that rule, Option 2**, because the risky
part of multiplayer is the read-path enforcement (gap G6), and Option 2 proves it with flipping
xfail tests before any UI or lifecycle depends on it.

## Conditions that apply to any GO (Options 2–4)

1. The D-02 and D-03 records are signed first, because M1 and M2 cannot be built without them
   (project admin model; what a member may see, including the legacy-run rule in slice plan M2).
2. Forward-only migrations; any `contracts/` change follows the full ritual.
3. Payment stays off; global P-D6 stays off; no auto-merge; no auto-assignment; no agent authority
   beyond the approved assignment.
4. Each slice lands as a draft PR into the working branch with an evidence file; only the owner
   marks anything `Verified at <revision>`.
5. Gate 6 still needs B-09 at one revision, the threat model and an independent reviewer. A GO
   here authorizes **building**, not **piloting** with multiple members.

## Revocation

The owner can return to Option 1 at any time by signing a new line below. Built slices then stay
behind their default-off flags; nothing is deleted, and no migration is reversed (forward-only).

## Consequences of the default (Option 1)

No new invasive multiplayer UI or lifecycle routes; hardening and negative tests on the existing
pilot are allowed; the pilot stays single-member with admin review.

## Still missing

Pilot demand evidence (signed SOWs asking for multi-member), D-02 and D-03 signatures, the
post-D2/D3 invite→revoke re-run, a named independent reviewer.

## Sign-off (owner only — leave blank)

- Decision (Option 1 / 2 / 3 / 4): ______________________________________
- Signature: ____________________________________  Date: ______________
- Stage 2 (Option 2 only, authorizes M4–M7): ___________________  Date: ______________
- Independent reviewer / date: _______________________________________
- ACCEPTED record to be created at: `docs/decisions/______________________`
