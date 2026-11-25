# Phase 6 multiplayer foundation: slice plan

- **Date:** 2026-11-25 · **Base:** `w28-s2-sprints1-4` at `fe2233c`
- **Status:** PROPOSED. Planning only. Slices M0 run without P-D7 (design and tests). Slices M1
  onward start **only** after the owner signs P-D7 GO
  (`docs/decisions/2026-11-24-p-d7-multiplayer-build-gate6-DRAFT.md`, rev 2). Nothing here is
  started by this document.
- **Gap ids** (G1…G14) refer to `docs/plans/phase6-multiplayer-gap-map-2026-11-25.md`.

## Rules every slice keeps

- Forward-only numbered migrations in `section1/migrations/`; never edit an applied one.
- Any `contracts/` change (for example a project role on `Principal`) takes the full ritual: version
  bump, regenerated TS mirror, mocks, conformance tests, in one commit.
- Enforcement before UI. A read path is scoped on the server, with a negative test, before any
  screen depends on it.
- No-oracle answers: a foreign or invisible object answers exactly like an unknown one.
- Every new surface stays behind `HARNESS_PILOT_TEAMS` (or a new per-slice flag that defaults
  **off**) until Gate 6.
- No auto-merge, no auto-assignment, no agent authority beyond the approved assignment. Payment and
  global P-D6 stay off throughout.
- Each slice ends with an evidence file under `docs/briefs/evidence/multiplayer/` naming the
  revision, commands and exit codes. Only the owner marks anything `Verified at <revision>`.

## M0. Design and baseline (allowed now, no P-D7 needed)

| Step | Work | Closes | Done when | Where it runs |
|---|---|---|---|---|
| M0.1 | Permission matrix doc: rows = admin (org), project admin, engineer, viewer, guest, agent; columns = create, invite, read, assign, approve, steer, export, revoke, delete; each cell cites the code that enforces it today or says "not enforced" | G1 | Owner reviews alongside D-02 and D-07 | Docs |
| M0.2 | Schema design doc for `project_members`, run/task visibility and agent ownership, with the migration it would become, rollback note and the `Principal` contract impact | G2–G4, G14 | Reviewed by technical lead; no SQL file added | Docs |
| M0.3 | "Today's behaviour" negative tests: a viewer and an engineer on the same team reading each other's runs, search hits, exports and notifications. Marked `xfail(strict=True)` with the reason "P-D7: member visibility not built", so they flip loudly when enforcement lands | G6 baseline | Tests run in cloud pytest; xfail counts recorded | Cloud |
| M0.4 | Owner-Mac re-run of `section7/e2e/invite-revoke-live.e2e.ts` after D2 (Task 42) and D3 (Task 41) | G5, G12 | PASS or FAIL logged with revision | Owner Mac |
| M0.5 | Extend the one-page threat model to member-vs-member inside a team; name an independent reviewer candidate | G13 | Doc merged; reviewer named by owner (D-09) | Docs |

## M1. Project membership (first build slice after GO)

- **Scope:** migration `00NN_project_members.sql` (`project_id`, `member_id`, `project_role`,
  `added_by`, `added_at`, `removed_at`), backfill every existing team seat into every existing
  project with its team role (so behaviour does not change on deploy), admin-only add/remove
  routes, audit events.
- **Depends on:** D-02 (project admin), D-08 (removal). P-D7 GO.
- **Done when:** idempotent add/remove; removing someone from project A leaves project B intact;
  team removal cascades to project seats; backfill proven on a copy of the compose DB.
- **Tests:** section1 unit + PG suite on owner Mac.
- **Size:** medium. **Risk:** low (no read path changes yet).

## M2. One visibility predicate, enforced on runs (the risky slice)

- **Scope:** a single `visible_run_ids(principal, project)` rule: project admins see all; members
  see runs tied to their assignments plus explicit grants (D-03). `authorize_run` calls it after
  the team check. Contract change if `Principal` needs a project role (full ritual).
- **Schema:** `agent_runs` has no starter or assignee column today, so this slice adds nullable
  `started_by_member_id` and `assignment_ref` (new migration). Legacy runs with neither are
  visible to project admins only, **or** to every project member until an admin assigns them.
  That legacy rule is an owner choice to record with D-03 before M2 ships.
- **Depends on:** M1, D-03.
- **Done when:** the M0.3 xfails for **runs** flip to passing; cross-member reads answer the same
  404 as an unknown id; an `authz.denied` audit row is written.
- **Tests:** section4 unit; section1 conformance if the contract moves.
- **Size:** medium. **Risk:** high (every run route). Ships behind a default-off flag.

## M3. The same predicate everywhere else

- **Scope:** reuse M2's rule in WebSocket subscribe and presence, search serving, export build,
  notification fan-out, artifacts, discussion.
- **Depends on:** M2.
- **Done when:** every M0.3 xfail flips; a revoked member's open socket is closed within the lease
  window; search returns no snippet, count or cursor for an invisible run.
- **Tests:** section4 unit; WS leg on the live stack (owner Mac).
- **Size:** large (six surfaces). Split per surface if a PR passes ~600 lines.

## M4. Assignment lifecycle (B-05)

- **Scope:** per-assignment rows split out of the plan JSON: proposed → approved → accepted → in
  progress → blocked or review → complete, plus decline, reassign and handoff. CAS updates, actor,
  timestamps, audit rows with ids and states only.
- **Depends on:** M2 (assignee becomes the visibility key), D-05, D-08.
- **Done when:** concurrent transitions have one winner; a reassigned task disappears from the old
  assignee's `/mine` and runs; audit trail is complete.
- **Size:** medium.

## M5. Least-privilege context and canaries (B-08)

- **Scope:** planner and member-facing agent context assembled from the M2 predicate; a canary
  suite plants a unique string in member A's assignment and asserts it is absent from member B's
  prompt, tool results, logs, events, errors and search snippets.
- **Depends on:** M2, M3, D-06.
- **Done when:** canary suite green in cloud; one live canary run on owner Mac.
- **Size:** medium.

## M6. Admin and member workspaces (B-06, B-07)

- **Scope:** admin progress, cost and exceptions per assignment; member workspace showing only
  authorized tasks, requests and notifications. Uses only M2–M4 server data.
- **Depends on:** M3, M4.
- **Done when:** vitest + axe at 375 px; two members cannot see each other's restricted data via UI
  **or** direct request (the direct-request half is already proven by M3).
- **Size:** medium-large.

## M7. B-09 acceptance and Gate 6 pack

- **Scope:** one Playwright journey at one revision: admin invites two differently skilled
  members, assigns, they work concurrently, one reconnects, one is revoked mid-task; API
  assertions alongside. Threat model updated; independent reviewer reads it.
- **Depends on:** M1–M6.
- **Done when:** browser + API evidence at one revision; owner and reviewer sign Gate 6.

## Order and what can overlap

```
M0 (now) ──► P-D7 GO ──► M1 ──► M2 ──► M3 ──┬──► M5 ──┐
                                      └──► M4 ──┴──► M6 ──► M7 ──► Gate 6
```

M4 and M5 can run in parallel after M3. Nothing after M0 starts without the signed P-D7 record.

## Rough size

M1–M7 is about six to eight cloud PRs plus three owner-Mac live runs (M1 PG, M3 WS, M7 B-09).
These are estimates, not commitments; Phase 6 gates on evidence, not dates.
