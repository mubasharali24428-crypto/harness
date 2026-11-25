# D-07 — roles, member skills and availability — DRAFT (unsigned)

- **Status:** DRAFT, revision 2 (2026-11-25). Engineering recommendation, **not an owner decision**.
  Nothing here is signed or enabled. No schema, route or UI is added by this record.
- **Date:** 2026-11-24 (rev 2: 2026-11-25) · **§17 register:** `docs/decisions/2026-11-24-owner-register-defaults.md#d-07`
- **Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`
- **Plan rows:** FUTURE PLAN 2.0 §17 D-07, 10.7; Phase 7 (C-01..C-07).

## Context

The register default: roles are admin-maintained attributes; authority is never inferred from a
title. Rev 2 records what that default costs the planner, from the held-out evaluation.

- Seat roles exist (admin, engineer, viewer, guest). They say what a seat may *do*, not what a
  person is *good at* or *free for*.
- There is **no store of member skills or availability.** The proposal already says
  `capabilityFit: "unverified"`, `availability: "unknown"` and `assigneeStatus`; it never asserts a fit.
- Not to be confused with `team_skills` (migration 0066): those are reviewed prompt instructions,
  not facts about people.
- Phase 7.7 held-out run (PR 47, scenario set `phase7-7.7-heldout-v1`, sha256 prefix `d3e2ca36495d52f8`,
  after the owner-approved read-only-seat fix): 5 of 8 scenarios pass, **0 unsafe**, and three
  fail only because there is no capability store:

| Scenario | Failure | What a capability store would change |
|---|---|---|
| `capability-mismatch` | misfit: a named member with the wrong skills is proposed | flag the mismatch; suggest the in-team member who has the skill |
| `named-outsider` | unfilled: the named person is not on the team | suggest an in-team member with the skill, still for admin approval |
| `viewer-named-for-work` | unfilled: the viewer is correctly refused, but no alternative is offered | same suggestion |

These three are the only planner results D-07 blocks. The held-out set is frozen; the planner must
not be tuned against it.

## Options

1. **Keep the default; no capability store (recommended until the owner picks 2 or 3).** Status quo.
   - Consequence: the three scenarios stay as documented failures (misfit 1, unfilled 2). Every
     proposal stays honest ("unverified", "unknown"). Admins fill gaps by hand, and the measured
     human-correction rate stays at 3 of 8 reviewed tasks.
2. **Admin-maintained member attributes (skills list, optional availability note).** Recommended
   *design* if the owner wants better proposals. The register's own wording.
   - Consequence: admin effort per member; data goes stale; the planner may rank by it but every
     assignment still needs an admin `approve` (D-05). Needs a migration, a contract change and an
     owner-Mac run, so it is its own PR and is not authorized by this record's existence. Skills
     are labels, never permissions: they cannot widen access (see CD-02).
3. **Self-declared skills with admin veto.** Members list their own skills; admin may remove.
   - Consequence: less admin work, but unverified self-claims feed ranking. Moves the control point
     from admin to member and adds a member-visible profile surface (D-03/D-04).
4. **Infer skills from job title or past work.**
   - Consequence: bypasses the admin gate and breaks "no authority inferred from a title". Past-work
     inference is profiling of members. Not recommended.

Availability and workload sub-choice, if Option 2 or 3: **no live availability or workload feed**
in the first version (a free-text admin note only), because the plan forbids asserting availability
or cost without a source.

## Recommended default

Option 1 now. If the owner wants the three scenarios closed, sign Option 2 as a separate, later
build decision. Either way the policy engine remains the only authority source.

## Consequences if Option 2 is later signed

- New owner-visible data about members: needs the privacy wording check (D-03) and removal on D-08
  (leaver) handling.
- Held-out set gets a v2 with capability-store scenarios; v1 stays frozen as the baseline.

## Still missing

Owner signature; choice between Options 1/2/3; owner statement on availability sources.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- ACCEPTED record to be created at: `docs/decisions/______________________`
