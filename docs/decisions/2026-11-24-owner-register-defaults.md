# Owner decision register — defaults in force (FUTURE PLAN 2.0 §17)

**Date:** 2026-11-24 · **Status:** RECORDED BY ENGINEERING — every row below is **BLOCKED on the
decision owner's signature** unless a linked ACCEPTED record says otherwise. Engineering cannot
confirm or amend an owner decision; this file records the default that governs code today, so
each §17 row has one dated, linkable record.
**Decision owner:** project owner (all rows). **Base revision:** `aed8a339`, branch `w28-s1-delegate`.
**Ledger:** `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`.

Shape per row: Context → Decision (default in force) → Consequences → Evidence still missing.

## D-01 — deployment priority
- **Context:** draft `2026-11-11-d01-deployment-priority-DRAFT.md` recommends self-host first.
- **Decision (default):** self-host first; preserve both; no new cloud-only dependency.
- **Consequences:** no hosted-only code paths merge.
- **Missing:** owner signature on the draft.

## D-02 — project admin model
- **Decision (default):** project admins fully visible; lifecycle (invite/remove/transfer) needs owner approval.
- **Consequences:** Phase 6 admin lifecycle stays behind P-D7.
- **Missing:** owner choice on multiple admins + ownership transfer.

## D-03 — member visibility
- **Decision (default):** members see assigned and explicitly authorized items only.
- **Consequences:** Task 3 assignee package returns only the caller's own assignment.
- **Missing:** owner choice on shared goals / dependency status.

## D-04 — member communication
- **Decision (default):** requests go to admin; no implicit access or invitation authority.
- **Missing:** owner signature.

## D-05 — admin approval of plans
- **Decision (default):** AI proposes; admin approves every assignment; agent plan approval stays mandatory.
- **Consequences:** Task 3 plan versions are inert until an admin `approve` review.
- **Missing:** owner signature.

## D-06 — members' agents
- **Decision (default):** no access wider than explicit grants; existing approval and merge rules.
- **Consequences:** Task 10 external adapters join read-only and cannot widen scope.
- **Missing:** owner signature.

## D-07 — roles and capabilities
- **Decision (default):** admin-maintained attributes; no authority inferred from a title.
- **Missing:** owner signature.

## D-08 — leavers and recovery
- **Decision (default):** revoke promptly; keep audit; an authorized admin reassigns.
- **Missing:** owner signature.

## D-09 — pilot providers / hosts / reviewers
- **Decision (default):** existing documented pilot limits; no unsupported compliance claims.
- **Missing:** owner list of providers, jurisdictions, external reviewers.

## P-D2 — payment rail
- **Context:** evidence draft `2026-11-12-payment-rail-P-D2-DRAFT.md` (Phase 3.9): Stripe direct excludes
  Pakistan; Paddle/FastSpring seller eligibility unconfirmed; Atlas needs counsel.
- **Decision (default):** verify eligibility first; billing flag stays **off**.
- **Consequences:** 10.11 line "payment rail live with a recorded test transaction" cannot be met.
  No provider account, key or test transaction was created by engineering.
- **Missing:** written provider confirmation of seller eligibility + owner choice of rail.

## P-D3 — product name
- **Context:** `2026-11-11-rename-shortlist-DRAFT.md` (no trademark clearance performed).
- **Decision (default):** no public launch under the colliding name.
- **Missing:** counsel search + owner pick.

## P-D4 — pilot pricing band
- **Decision (default):** $500–2K / month / partner; counsel review before any quote.
- **Missing:** owner signature + counsel review.

## P-D5 — open-source flip
- **Decision (default):** MIT chosen; repo stays private until 10.13.
- **Missing:** nothing until Phase 10.

## P-D6 — approval envelopes (4.10)
- **Context:** D-U4 ACCEPTED 2026-11-13 (`2026-11-13-du4-risk-tiers.md`): tiers advisory only, no auto-approval.
- **Decision (default):** envelopes **not enabled**.
- **Consequences:** Task 5 severity grid is display-only; it never approves.

## P-D7 — multiplayer build go/no-go (Gate 6)
- **Context:** Gate 6 requires B-09 evidence, a threat model, owner + independent-reviewer approval.
  The collab pilot (approval digests, leader approvals) exists behind `HARNESS_PILOT_TEAMS`.
- **Decision (default):** **DEFERRED — design-complete is allowed, build-authorized is NOT.**
  Decided from pilot demand evidence, not momentum.
- **Consequences for Task 2:** no new invasive multiplayer UI or lifecycle routes; hardening and negative
  tests on the existing pilot surface are allowed; the pilot stays single-member with admin review.
- **Missing:** pilot demand evidence (signed SOWs asking for multi-member), B-09 run, reviewer sign-off.

## CD-01 — cache retention / tenancy
- **Decision (default):** no cross-tenant sharing; month-scale cap; purge on erasure.

## CD-02 — learned memory and skills
- **Decision (default):** not in pilot scope; **proposals only, never auto-applied**.
- **Consequences:** Tasks 6, 7 and 9 emit proposal/queue objects only; nothing injects into prompts or rules
  without a human review.

## X-D1 — approval rendering owner
- **Decision (default, implemented `aed8a339`):** member self-selects; admin may set it for a member;
  rendering never changes disclosed facts or the policy risk tier.

## X-D2 — 10X benchmark spend cap
- **Decision (default):** dry corpus and local models first; paid trials only with an agreed budget.
