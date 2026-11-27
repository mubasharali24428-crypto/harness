# Phase 6 multiplayer foundation: gap map (design only)

- **Date:** 2026-11-25 · **Base:** `w28-s2-sprints1-4` at `fe2233c`
- **Status:** DESIGN ONLY. Nothing in this file enables P-D7, changes code, adds a migration or
  flips a flag. P-D7 stays **DEFERRED** (`docs/decisions/2026-11-24-p-d7-multiplayer-build-gate6-DRAFT.md`).
  Payment and global P-D6 stay off. Verdict stays **NOT READY**.
- **Inputs read:** `FUTURE PLAN 2.0.md` §1, §2, §11 (Phase 6), §12, §17; B-09 evidence pack
  `docs/briefs/evidence/multiplayer/B-09-checklist-2026-11-24.md`; D-02…D-08 drafts in
  `docs/decisions/`; code in `section1/src/harness_identity/` (`teams_api.py`, `invites.py`,
  `memberships.py`, `orgs.py`), `section4/src/harness_orchestrator/` (`authz.py`, `collab.py`,
  `presence.py`, `search.py`, `exports.py`, `assignment_plan.py`), migrations up to `0082`.
- **Companions:** slice plan `docs/plans/phase6-multiplayer-slice-plan-2026-11-25.md`; decision
  record `docs/decisions/2026-11-24-p-d7-multiplayer-build-gate6-DRAFT.md` (revision 2).

## 1. The one-paragraph answer

Harness already has real multi-user plumbing: teams with seats, admin-controlled invites bound to
the invited mailbox, soft member removal that kills sessions, a shared-run collaboration pilot
(one leader, digest-bound approvals, presence, handoff), and an admin-reviewed assignment plan
whose `/mine` view shows a member only their own tasks. What it does **not** have is the thing
Phase 6 is about: **visibility below the team.** Today the unit of privacy is the team. Every
seat on a team can read every run, search hit, export and notification of that team
(`roles.can(..., "read")` is `True` for all roles; `authorize_run` proves team ownership only).
The privacy invariant in §1 ("members see their own assigned work and explicitly authorized
project information; only project admins see everything") needs a per-project membership, a
per-assignment visibility rule, and that rule enforced in every read path. That is the bulk of
the gap, and it is why Gate 6 is a real gate rather than paperwork.

## 2. What exists today (verified in code at `fe2233c`)

| Area | What exists | Where |
|---|---|---|
| Team seats and roles | `team_members` with roles admin / engineer / viewer / guest; guest is invite-only legacy | `0001`, `contracts/src/harness_contracts/roles.py` |
| Org layer | Organizations with owner / admin / billing_admin / member / guest, separate from team role | `0038_organizations.sql`, `orgs.py`, `docs/organizations.md` |
| Invites | Mint, list, resend, revoke, redeem; tokens stored as SHA-256; redemption atomic; bound to the invited email (M18-C5) | `invites.py`, `test_invites.py`, `test_invite_identity_binding.py` |
| Remove / role change | Admin-only, no-oracle 404 across teams, LAST_ADMIN and SELF_REMOVAL guards, step-up for role change, audit events | `teams_api.py` |
| Soft removal (D2) | Seat kept as audit FK target, `user_id` nulled so every lookup misses it; team sessions deleted on removal | `0082_team_member_soft_remove.sql`, `memberships.py` (Task 42) |
| Roster ids (D3) | Roster items carry `userId`; UI Remove sends the right id | Task 41, `task-41-d3-userId.md` |
| Guest project grants | Per-project read grant for **guests only** | `0076_project_guest_grants.sql`, `fastapi_auth.guest_gate` |
| Run authorization | Fail-closed team-ownership guard; role gate for write/approve | `section4/.../authz.py` (M18-C1) |
| Shared-run collaboration | Participants, single leader lease, redirects, handoff, digest-bound approvals, presence | `collab.py`, `control.py`, `presence.py`, `0035`, `0042`, `0043` |
| Assignment planning (Phase 7 C-01…C-05, C-07 note) | Immutable plan versions, admin-only propose / review, `/mine` returns the caller's own approved tasks with dependency keys only | `assignment_plan.py`, `0078` |
| Search / exports | Team-scoped; serving re-checks live source, run/session access and pilot allowlist | `search.py`, `exports.py` |
| Pilot flag | `HARNESS_PILOT_TEAMS` allowlist gates the collab pilot | `section4/.../api.py`, `test_pilot_flag_rollback.py` |
| Live evidence | Two-context multiplayer spec PASS at `49445e4f`; invite→revoke spec FAILED at removal (D2/D3), fixes landed since, live re-run still owed | B-09 pack rows S4-25, S5-36 |

## 3. Gap map

State key: **Met** · **Partly** · **Missing** · **Decision** (blocked on an owner decision record).
"Gated" says whether closing the gap is design work (allowed now) or build work (needs P-D7 GO).

| # | Phase 6 item | Needed for multiplayer | Today | Gap | Blocked on | Gated |
|---|---|---|---|---|---|---|
| G1 | 6.1 / B-01 roles | Written permission matrix over create, invite, read, assign, approve, steer, export, revoke, delete, per role, at **project** level; org admin vs project admin separated | Role matrix tested at team level (`test_authz_matrix.py`); org roles exist separately | No reviewed matrix document; **no project-admin role** (team admin = project admin); no rule for an org admin's reach into a team's projects | D-02 (who is project admin, how many, transfer), D-07 (fixed roles vs capabilities) | Design now |
| G2 | 6.2 / B-02 project membership | A member can belong to project A and not project B of the same team | Membership is team-wide; only guests have per-project grants (0076) | **Missing** `project_members` (or equivalent) with project role; no migration plan; no TS mirror | D-02, D-03 | Schema design now; migration after GO |
| G3 | 6.2 / B-02 task visibility | Each task/run carries who may see it: assignee, project admins, explicit grantees | Assignment plan stores assignee in a JSON blob; runs carry no assignee or visibility | **Missing** visibility model on runs and tasks; `agent_runs` has no owner/assignee column usable for scoping | D-03 (assigned-only vs shared goals / dependency status) | Design now |
| G4 | 6.2 / B-02 agent ownership | An agent run belongs to an assignment and a human owner; agent context limited to that | External adapters are read-only observers (`authority []`); no agent→assignment link | **Missing** agent ownership schema; links to 7.6 (C-06) | D-06 | Design now |
| G5 | 6.3 / B-03 lifecycle | Invite, accept, remove, role change, transfer; idempotent; expired/revoked invite denied; removal revokes sessions | Met at team level: invites, soft removal, session revocation, role change, owner transfer tests | **Project-level** invite / remove / transfer do not exist; removal from a project (vs the team) undefined; live invite→revoke re-run after D2/D3 still owed | D-02, D-08 | Live re-run allowed now (owner Mac); project lifecycle after GO |
| G6 | 6.4 / B-04 server-side scoping | Every read path filters by project membership + assignment visibility; cross-member negatives across API, WS, search, exports, notifications, artifacts | Cross-**team** no-oracle 404 everywhere tested; `read` allowed for every team seat | **Cross-member within a team: not enforced anywhere except `/mine`.** Needs a single `visible_runs(principal)` predicate reused by API, WS subscribe, search serving, export build, notification fan-out, artifact reads | D-03 | Negative tests **documenting today's behaviour** allowed now (xfail with reason); enforcement after GO |
| G7 | 6.4 WebSocket | Subscriptions scoped to visible runs; revoked member's socket closed | Presence is team-scoped; live two-member spec passed; cross-team WS probe and post-revocation socket close not run | **Missing** cross-member WS negative; socket teardown on removal not verified | — | Test now (stack needed) |
| G8 | 6.5 / B-05 assignment lifecycle | proposed → approved → accepted → in progress → blocked / review → complete; reject, reassign, handoff; actor, timestamps, CAS | Only proposed / approved / rejected (plan level, not per task) | **Missing** per-assignment state machine, accept/decline by assignee, reassignment record, audit events | D-05, D-08 | After GO |
| G9 | 6.6 / B-06 admin workspace | Goals, plan, people, agents, ownership, dependencies, approvals, progress, cost, exceptions | Planning page shows plan, people, versions, approvals | **Missing** progress, cost, exceptions views per assignment | B-05 (needs states to show progress) | After GO |
| G10 | 6.7 / B-07 member workspace | Only authorized tasks, instructions, agent work, status, requests, notifications; nothing else via UI or direct request | `/mine` plus member planning view | Runs list, search, notifications, discussion still show the **whole team** to every member | G6 | After GO |
| G11 | 6.8 / B-08 least-privilege context | Planner, member-facing agents, summaries, logs, model calls get only authorized context; canary tests | Secrets never enter context (existing rule); no per-member context assembly | **Missing** canary suite: a restricted string planted in member A's assignment must not appear in member B's agent prompt, logs, events, errors, search snippets | D-03, D-06 | Canary harness design now; suite after G6 |
| G12 | 6.9 / B-09 acceptance | Invite→revoke with an admin and two differently skilled members, concurrent updates, reconnect; browser + API at one revision | Two-context spec PASS (S4-25); invite→revoke FAIL (S5-36), D2/D3 fixed since | Full B-09 journey at **one** revision never passed; needs G6–G11 to be meaningful | All of the above | Re-run of current spec allowed now; full B-09 after GO |
| G13 | Gate 6 | Threat model of the multiplayer slice; independent reviewer | One-page threat model in B-09 pack | Threat model covers team-level pilot only; no reviewer named | D-09 (reviewers) | Design now |
| G14 | Contracts | Any new `Principal` field (e.g. project role) or roles enum change | `Principal{userId, teamId, role}` frozen | A project-scoped principal needs a **contract version bump + TS mirror + mocks + conformance** (full ritual) | Owner review of the contract change | After GO |

## 4. What multiplayer needs, in plain terms

1. **A project-level membership** (G2), because a team today is all-or-nothing.
2. **One visibility predicate** (G3, G6) that every read path calls, so "member sees only their
   assigned work" is a server rule and not a UI filter.
3. **A per-assignment lifecycle** (G8) so assignees can accept, block, hand off and complete, and
   admins can see progress (G9).
4. **Least-privilege context** (G11) so agents and the planner cannot leak one member's work into
   another's prompt or logs.
5. **Proof** (G12): the B-09 journey green at one revision, plus a threat model and an
   independent reviewer (G13).

Items 1, 2 and 4 are the risky ones: they change who can read what across a dozen surfaces. They
should land first and alone, before any new UI, so a leak shows up in negative tests rather than
in a pilot.

## 5. What can happen before P-D7 without crossing the line

Allowed now (design, tests, evidence; no new product surface, no flag flips):

- The written permission matrix (G1) as a doc for D-02/D-07 sign-off.
- Schema design for `project_members`, run/task visibility and agent ownership (G2–G4) **as a
  design doc**, not a migration.
- Negative tests that pin today's team-wide read behaviour, marked `xfail(strict=True, reason=
  "P-D7: member-level visibility not built")`, so the day enforcement lands the tests flip.
- The canary harness design (G11).
- Owner-Mac re-run of `invite-revoke-live.e2e.ts` now that D2/D3 have landed (G5, G12).
- Extending the threat model to the multiplayer slice (G13).

Not allowed before GO: new migrations for project membership or visibility, new lifecycle
routes, member-scoped read enforcement in shipped code paths, new member/admin workspace UI,
any `Principal` contract change.

## 6. Open owner decisions that block design sign-off

| Decision | What multiplayer needs from it | Draft |
|---|---|---|
| D-02 | Project admin = team admin, or a separate per-project admin? How many? Transfer rule? | `2026-11-24-d02-project-admin-model-DRAFT.md` |
| D-03 | Assigned-only, or shared goals / dependency status too? Sets the visibility predicate | `2026-11-24-d03-member-visibility-DRAFT.md` |
| D-04 | Who can request more context or invite? | `2026-11-24-d04-member-communication-DRAFT.md` |
| D-05 | Admin approves every human task and agent plan? | `2026-11-24-d05-admin-approval-of-plans-DRAFT.md` |
| D-06 | Members using agents on their own tasks; agent reach | `2026-11-24-d06-members-agents-DRAFT.md` |
| D-07 | Fixed roles vs capabilities | `2026-11-24-d07-roles-and-capabilities-DRAFT.md` |
| D-08 | Leavers, unavailable admin, recovery | `2026-11-24-d08-leavers-and-recovery-DRAFT.md` |
| P-D7 | Build go / no-go | `2026-11-24-p-d7-multiplayer-build-gate6-DRAFT.md` (rev 2) |
